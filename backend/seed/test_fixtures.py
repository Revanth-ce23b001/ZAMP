"""Validation tests for the ClearMatch AP synthetic seed fixtures (fixtures-v1).

Run:  python -m pytest seed/test_fixtures.py -q
Uses only the standard library plus pytest. All money is integer paise.
"""
import json
import re
from datetime import date, timedelta
from pathlib import Path

import pytest

HERE = Path(__file__).parent
load = lambda n: json.loads((HERE / n).read_text())["records"]
VENDORS = load("vendors.json")
POS = load("purchase_orders.json")
LEDGER = load("ledger.json")
CONS = load("consumption.json")

R = lambda rupees: int(round(rupees * 100))
V = {v["vendor_id"]: v for v in VENDORS}
PO = {p["po_number"]: p for p in POS}
LINES = {l["po_line_id"]: (p["po_number"], l) for p in POS for l in p["lines"]}
GSTIN_RE = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$")


def norm_no(s):
    for ch in " -/.":
        s = s.replace(ch, "")
    return s.upper()


def norm_desc(s):
    s = s.lower()
    s = re.sub(r"(?<!\d)\.|\.(?!\d)", " ", s)
    s = re.sub(r"[^\w\s.]", " ", s)
    s = re.sub(r"(\d)\s+([a-z])", r"\1\2", s)
    return re.sub(r"\s+", " ", s).strip()


def committed(po_number):
    qty, val = {}, 0
    for c in CONS:
        pn, line = LINES[c["po_line_id"]]
        if pn == po_number:
            qty[c["po_line_id"]] = qty.get(c["po_line_id"], 0) + c["quantity"]
            val += c["value"]
    return qty, val


def ceiling(po_number):
    t = PO[po_number]["po_total"]
    return t + min(t // 100, R(1000))


# ---------- structure and foreign keys ----------

def test_counts():
    assert sorted(V) == [f"V00{i}" for i in range(1, 8)]
    assert len(POS) == 11
    assert len(LEDGER) == 5


def test_vendor_fields():
    for v in VENDORS:
        assert GSTIN_RE.match(v["gstin"]), v["vendor_id"]
        assert v["state_code"] == v["gstin"][:2]
        assert re.fullmatch(r"[A-Z]{4}0[A-Z0-9]{6}", v["ifsc"]), v["vendor_id"]
        assert v["status"] in ("active", "blocked")
    assert [v["vendor_id"] for v in VENDORS if v["status"] == "blocked"] == ["V006"]


def test_foreign_keys():
    for p in POS:
        assert p["vendor_id"] in V
    for l in LEDGER:
        assert l["vendor_id"] in V and l["po_number"] in PO
        assert PO[l["po_number"]]["vendor_id"] == l["vendor_id"]
    led = {l["ledger_id"] for l in LEDGER}
    for c in CONS:
        assert c["ledger_id"] in led and c["po_line_id"] in LINES


def test_po_totals_and_lines():
    seen = set()
    for p in POS:
        assert p["po_total"] == sum(l["quantity"] * l["unit_price"] for l in p["lines"])
        keys = set()
        for l in p["lines"]:
            assert l["po_line_id"] not in seen
            seen.add(l["po_line_id"])
            for a in l["aliases"]:
                assert norm_desc(a) == a, f"alias not normalised: {a}"
            mine = {norm_desc(l["description"]), *l["aliases"]}
            assert not (mine & keys), f"ambiguous description/alias in {p['po_number']}"
            keys |= mine


# ---------- ledger and consumption reconciliation ----------

def test_ledger_arithmetic_and_prd_amounts():
    for l in LEDGER:
        assert l["invoice_number_normalised"] == norm_no(l["invoice_number_raw"])
        assert l["total"] == l["taxable_value"] + l["cgst"] + l["sgst"] + l["igst"]
        assert l["status"] == "APPROVE"
        intra = V[l["vendor_id"]]["state_code"] == "29"
        assert (l["igst"] == 0) if intra else (l["cgst"] == l["sgst"] == 0)
    by_no = {l["invoice_number_raw"]: l for l in LEDGER}
    assert by_no["INV-0912"]["total"] == R(53100) and by_no["INV-0912"]["invoice_date"] == "2026-10-01"
    assert by_no["INV-2041"]["total"] == R(106200) and by_no["INV-2041"]["invoice_date"] == "2026-10-12"
    assert by_no["INV-GC-0921"]["total"] == R(59000) and by_no["INV-GC-0921"]["invoice_date"] == "2026-10-03"


def test_consumption_derived_from_ledger():
    expected = {}
    for l in LEDGER:
        for s in l["line_signature"]:
            pl = next(pid for pid, (pn, line) in LINES.items()
                      if pn == l["po_number"] and line["item_code"] == s["key"])
            expected[(l["ledger_id"], pl)] = (s["qty"], s["qty"] * LINES[pl][1]["unit_price"])
    got = {(c["ledger_id"], c["po_line_id"]): (c["quantity"], c["value"]) for c in CONS}
    assert len(got) == len(CONS), "duplicate (ledger_id, po_line_id)"
    assert got == expected
    for l in LEDGER:
        assert sum(c["value"] for c in CONS if c["ledger_id"] == l["ledger_id"]) == l["taxable_value"]


def test_no_opening_overrun():
    for p in POS:
        qty, val = committed(p["po_number"])
        for l in p["lines"]:
            assert qty.get(l["po_line_id"], 0) <= l["quantity"]
        assert val <= p["po_total"]


# ---------- primary demo scenario preconditions (PRD 13.1) ----------

def test_T01_happy_path_balances():
    qty, val = committed("PO-1042")
    assert val == 0 and not qty
    assert V["V001"]["bank_account_number"] == "50200012341234" and V["V001"]["ifsc"] == "HDFC0001234"
    taxable = R(95000)
    assert taxable <= ceiling("PO-1042") == R(126000)
    assert PO["PO-1042"]["po_total"] - taxable == R(30000)


def test_T02_split_po_overrun():
    qty, val = committed("PO-1041")
    assert qty == {"PO-1041-1": 220} and val == R(220000)
    assert ceiling("PO-1041") == R(301000)
    assert 220 + 100 > 300 and val + R(100000) > ceiling("PO-1041")
    # T-02 invoice must be dated > 7 days after INV-SL-0467 (same total, same lines),
    # otherwise POSSIBLE_DUPLICATE also fires. Fixture expectation: 2026-10-29.
    prior = date.fromisoformat(next(l for l in LEDGER if l["invoice_number_raw"] == "INV-SL-0467")["invoice_date"])
    assert (date(2026, 10, 29) - prior).days > 7


def test_T03_bank_mismatch_anchor():
    v = V["V003"]
    assert v["ifsc"] == "HDFC0005512" and v["bank_account_number"].endswith("4471")
    invoice_ifsc, invoice_last4 = "ICIC0005678", "4471"
    assert invoice_last4 == v["bank_account_number"][-4:] and invoice_ifsc != v["ifsc"]
    assert PO["PO-1044"]["po_total"] - committed("PO-1044")[1] >= R(42000)


def score(po_number, inv_lines, inv_taxable, inv_date):
    p = PO[po_number]
    keys = {l["item_code"] for l in p["lines"]}
    frac = sum(1 for k in inv_lines if k in keys) / len(inv_lines)
    remaining = p["po_total"] - committed(po_number)[1]
    allow = min(p["po_total"] // 100, R(1000))
    d0 = date.fromisoformat(p["po_date"])
    return round(60 * frac + (30 if inv_taxable <= remaining + allow else 0)
                 + (10 if d0 <= inv_date <= d0 + timedelta(days=180) else 0))


def test_T04_inferred_po_scores():
    inv = ["LAP-14", "DOCK-USB"]
    taxable = 4 * R(62000) + 4 * R(8000)
    assert taxable == R(280000)
    d = date(2026, 10, 30)
    assert score("PO-1031", inv, taxable, d) == 100
    assert score("PO-1036", inv, taxable, d) == 70


def test_T05_confirmed_duplicate_anchor():
    orig = next(l for l in LEDGER if l["invoice_number_raw"] == "INV-2041")
    assert orig["vendor_id"] == "V007" and orig["status"] == "APPROVE"
    assert orig["invoice_number_normalised"] == "INV2041"


# ---------- other scenario preconditions ----------

def test_duplicate_windows():
    d = {l["invoice_number_raw"]: date.fromisoformat(l["invoice_date"]) for l in LEDGER}
    assert (date(2026, 10, 6) - d["INV-GC-0921"]).days <= 7      # T-16 possible duplicate
    assert (date(2026, 10, 31) - d["INV-0912"]).days > 7          # T-06 recurring, note only


def test_T17_two_plausible_candidates():
    inv = ["LAP-14", "MON-24"]
    taxable = 2 * R(62000) + 2 * R(11500)
    d = date(2026, 10, 30)
    a, b = score("PO-1031", inv, taxable, d), score("PO-1036", inv, taxable, d)
    assert a >= 50 and b >= 50 and abs(a - b) <= 10


def test_T25_po_1045_unconsumed():
    assert committed("PO-1045")[1] == 0
    assert 50 + 60 > 100 and R(110000) > ceiling("PO-1045")


def test_provenance_annotations_present():
    for name in ("vendors.json", "purchase_orders.json", "ledger.json", "consumption.json"):
        assert "provenance_rule" in json.loads((HERE / name).read_text())["_meta"]
    for v in VENDORS + POS + LEDGER:
        assert "_synthetic_fields" in v
