"""Verify every sample invoice against the fixtures and the PRD rules it is meant to exercise.

    python invoice-templates/verify.py

Two layers, both must pass:
  1. Manifest vs fixtures: the printed values in manifest.json are checked against
     backend/seed (vendor, bank, PO, line pairing via app.utils.normalise, unit prices),
     arithmetic E1-E4 (PRD §9.1), GST regime (A-02), dates and duplicate tiers (§9.6,
     README_FIXTURES), each against the outcome the invoice is designed to produce.
  2. PDF vs manifest: the file exists, matches SHA256SUMS, is within the upload limits (A-23),
     has a text layer (A-24) or none for scans, and its text layer actually prints the values.
If a PDF and the fixtures disagree, the PDF is wrong: nothing here adjusts a fixture.
"""
import hashlib
import json
import re
import sys
from datetime import date, timedelta
from pathlib import Path

import pymupdf

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(REPO / "backend"))
from app.db.seed import load_fixtures  # noqa: E402
from app.policy import load_policy  # noqa: E402
from app.utils.masking import normalise_account_number  # noqa: E402
from app.utils.money import format_inr, rupees_to_paise  # noqa: E402
from app.utils.normalise import (  # noqa: E402
    normalise_description, normalise_gstin, normalise_invoice_number, normalise_item_code,
)

MANIFEST = HERE / "manifest.json"
GSTIN_RE = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$")
DATE_FORMATS = ("%d %b %Y", "%d/%m/%Y", "%d-%b-%Y", "%d %B %Y", "%Y-%m-%d")
REQUIRED_TESTS = {"T-01", "T-02", "T-03", "T-04", "T-05", "T-06", "T-07", "T-08", "T-09",
                  "T-12", "T-15", "T-16", "T-18", "T-25a", "T-25b"}
ALLOWANCE = 100  # paise, A-08

# README_FIXTURES "Constraints the test invoice PDFs must respect" (load-bearing, stated exactly).
T01_BANK = ("50200012341234", "HDFC0001234")
T03_IFSC, T03_LAST4 = "ICIC0005678", "4471"
T02_EARLIEST = date(2026, 10, 22)      # "after 21 Oct 2026"
T02_REQUIRED = date(2026, 10, 29)      # A-35


def P(amount) -> int:
    return rupees_to_paise(amount)


def inr(amount) -> str:
    return format_inr(P(amount)).replace("₹", "")


class Ctx:
    """Fixtures, policy and indexes shared by all checks."""

    def __init__(self):
        fx = load_fixtures()
        self.vendors = {v["vendor_id"]: v for v in fx["vendors"]}
        self.pos = {p["po_number"]: p for p in fx["purchase_orders"]}
        self.ledger = fx["ledger"]
        policy = load_policy()
        self.max_bytes = policy.assumption("A-23")["max_bytes"]
        self.max_pages = policy.assumption("A-23")["max_pages"]
        self.min_chars = policy.assumption("A-24")
        self.buyer_state = policy.assumption("A-03")
        self.suffixes = policy.assumption("A-16")
        self.window = policy.assumption("A-15")["window_days"]
        self.max_age = policy.assumption("A-25")["max_age_days"]
        self.date_window = policy.assumption("A-26")["date_window_days"]
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        self.meta = manifest["_meta"]
        self.invoices = manifest["invoices"]
        self.samples = REPO / self.meta["output_dir"]


# ---------------------------------------------------------------- manifest vs fixtures

def check_vendor(inv, ctx, fail):
    v = inv["vendor"]
    gstin = normalise_gstin(v["gstin"])
    if not GSTIN_RE.match(gstin):
        fail(f"GSTIN {gstin} has invalid format (FR-031)")
    if v["state_code"] != gstin[:2]:
        fail(f"state_code {v['state_code']} != GSTIN prefix {gstin[:2]}")
    matches = [m for m in ctx.vendors.values() if m["gstin"] == gstin]
    if inv["expect"]["vendor_in_master"]:
        if len(matches) != 1:
            fail(f"GSTIN {gstin} matches {len(matches)} fixture vendors, expected exactly 1")
            return None
        m = matches[0]
        if v["vendor_id"] != m["vendor_id"]:
            fail(f"vendor_id {v['vendor_id']} but GSTIN belongs to {m['vendor_id']}")
        for k in ("legal_name", "state_code", "state_name"):
            if v[k] != m[k]:
                fail(f"vendor {k} {v[k]!r} != fixture {m[k]!r}")
        return m
    if matches:
        fail(f"GSTIN {gstin} matches fixture vendor {matches[0]['vendor_id']}, expected none")
    if any(m["legal_name"].lower() == v["legal_name"].lower() for m in ctx.vendors.values()):
        fail(f"name {v['legal_name']!r} matches a fixture vendor; expected an unknown vendor")
    if v.get("vendor_id") is not None:
        fail("unknown vendor must have vendor_id null")
    return None


def check_bank(inv, master, fail):
    bank, mode = inv.get("bank"), inv["expect"]["bank"]
    if mode == "absent":
        if bank:
            fail("bank details printed but expected absent")
        return
    if not bank:
        fail(f"bank details absent but expected {mode}")
        return
    if master is None:
        fail("bank expectation needs a fixture vendor")
        return
    acct, ifsc = normalise_account_number(bank["account_number"]), bank["ifsc"].upper()
    m_acct, m_ifsc = master["bank_account_number"], master["ifsc"]
    if mode == "match":
        if (acct, ifsc) != (m_acct, m_ifsc):
            fail(f"bank {acct}/{ifsc} != vendor master {m_acct}/{m_ifsc}")
    elif mode == "mismatch":
        if acct == m_acct or ifsc == m_ifsc:
            fail(f"bank {acct}/{ifsc} should differ from master {m_acct}/{m_ifsc} in both account and IFSC")
    elif mode == "mismatch_ifsc_same_account":
        if acct != m_acct:
            fail(f"account {acct} should equal master {m_acct} (only IFSC differs, §8.7)")
        if ifsc == m_ifsc:
            fail(f"IFSC {ifsc} should differ from master {m_ifsc}")
    else:
        fail(f"unknown bank expectation {mode!r}")


def check_po(inv, ctx, fail):
    ref, mode = inv["po_reference"], inv["expect"]["po"]
    vid = inv["vendor"]["vendor_id"]
    if mode == "none":
        if ref is not None:
            fail(f"PO reference {ref} printed but expected none")
        return inv["expect"].get("pair_against")
    norm = {normalise_invoice_number(k): k for k in ctx.pos}
    key = norm.get(normalise_invoice_number(ref or ""))
    if mode == "explicit":
        if key is None:
            fail(f"PO {ref} not in fixtures")
            return None
        if ctx.pos[key]["vendor_id"] != vid:
            fail(f"PO {key} belongs to {ctx.pos[key]['vendor_id']}, not {vid}")
        return key
    if mode == "not_found":          # T-20 style: reference printed, but not this vendor's PO
        if key is not None and ctx.pos[key]["vendor_id"] == vid:
            fail(f"PO {ref} exists for {vid}; expected not found")
        return inv["expect"].get("pair_against")
    fail(f"unknown po expectation {mode!r}")
    return None


def pair_lines(inv, po):
    """FR-063 then FR-064. Each PO line pairs with at most one invoice line. Returns
    (pairs [(invoice_line, po_line, method)], unpaired [invoice_line])."""
    used, pairs, unpaired = set(), [], []
    for line in inv["lines"]:
        hit = None
        if line.get("item_code"):
            code = normalise_item_code(line["item_code"])
            hit = next(((pl, "code") for pl in po["lines"]
                        if pl["po_line_id"] not in used and normalise_item_code(pl["item_code"]) == code), None)
        if hit is None:
            desc = normalise_description(line["description"])
            hit = next(((pl, "alias" if desc in pl["aliases"] else "description") for pl in po["lines"]
                        if pl["po_line_id"] not in used
                        and (desc == normalise_description(pl["description"]) or desc in pl["aliases"])), None)
        if hit:
            used.add(hit[0]["po_line_id"])
            pairs.append((line, hit[0], hit[1]))
        else:
            unpaired.append(line)
    return pairs, unpaired


def check_lines(inv, ctx, po_key, fail):
    for line in inv["lines"]:
        if P(line["unit_price"]) * line["quantity"] != P(line["amount"]):
            fail(f"line {line['description']!r}: {line['quantity']} x {line['unit_price']} != amount {line['amount']}")
    if po_key is None:
        if inv["expect"].get("pair_against", "unset") is not None:
            fail("no PO to pair against; set expect.pair_against explicitly (null to skip)")
        return []
    po = ctx.pos[po_key]
    pairs, unpaired = pair_lines(inv, po)
    want_unpaired = inv["expect"].get("unpaired_lines", [])
    got_unpaired = [l["description"] for l in unpaired]
    if got_unpaired != want_unpaired:
        fail(f"unpaired lines {got_unpaired} != expected {want_unpaired} against {po_key}")
    variances = inv["expect"].get("price_variance", {})
    for line, pl, _ in pairs:
        code = pl["item_code"]
        if code in variances:
            v = variances[code]
            if P(line["unit_price"]) != P(v["invoice"]) or pl["unit_price"] != P(v["po"]):
                fail(f"{code}: price variance expected invoice {v['invoice']} vs PO {v['po']}, "
                     f"got {line['unit_price']} vs PO {pl['unit_price'] / 100:.2f}")
            if P(line["unit_price"]) <= pl["unit_price"] * 101 // 100:
                fail(f"{code}: variance case is within the 1% tolerance")
        elif P(line["unit_price"]) != pl["unit_price"]:
            fail(f"{code}: unit price {line['unit_price']} != PO unit price {pl['unit_price'] / 100:.2f}")
    for code in variances:
        if code not in {pl["item_code"] for _, pl, _ in pairs}:
            fail(f"price_variance names {code}, which no line pairs with")
    return pairs


def arithmetic(inv) -> dict[str, int]:
    """Signed variance in paise per equation (printed minus expected), PRD §9.1."""
    lines_sum = sum(P(l["unit_price"]) * l["quantity"] for l in inv["lines"])
    subtotal = P(inv["subtotal"]) if inv["subtotal"] is not None else None
    discount = P(inv["discount"] or 0)
    base = subtotal if subtotal is not None else lines_sum
    taxable = P(inv["taxable_value"]) if inv["taxable_value"] is not None else base - discount
    out = {}
    if subtotal is not None:
        out["E1"] = subtotal - lines_sum
    if inv["taxable_value"] is not None:
        out["E2"] = taxable - (base - discount)
    for key, comp in inv["tax"].items():
        if comp:
            expected = (taxable * comp["rate"] * 100 + 5000) // 10000   # paise, half-up
            out[f"E3.{key}"] = P(comp["amount"]) - expected
    taxes = sum(P(c["amount"]) for c in inv["tax"].values() if c)
    others = sum(P(c["amount"]) for c in inv["other_charges"])
    out["E4"] = P(inv["total"]) - (taxable + taxes + others + P(inv["round_off"] or 0))
    return out


def check_arithmetic(inv, fail):
    expected = {k: P(v) for k, v in inv["expect"].get("arithmetic_variance", {}).items()}
    for eq, var in arithmetic(inv).items():
        if eq in expected:
            if var != expected[eq]:
                fail(f"{eq} variance {var / 100:.2f} != intended {expected[eq] / 100:.2f}")
        elif abs(var) > ALLOWANCE:
            fail(f"{eq} off by {var / 100:.2f} (allowed 1.00)")


def check_tax_regime(inv, ctx, fail):
    intra = inv["vendor"]["state_code"] == ctx.buyer_state
    t = inv["tax"]
    if intra and (t.get("igst") or not (t.get("cgst") and t.get("sgst"))):
        fail("intra-state invoice must charge CGST + SGST and no IGST (A-02)")
    if not intra and (t.get("cgst") or t.get("sgst") or not t.get("igst")):
        fail("inter-state invoice must charge IGST only (A-02)")


def signature(lines) -> list:
    keys = [(normalise_item_code(l["item_code"]) if l.get("item_code") else normalise_description(l["description"]),
             l["quantity"]) for l in lines]
    return sorted(keys)


def duplicate_tiers(inv, ctx) -> set[str]:
    """§9.6 tiers against the seeded ledger; first match per ledger entry wins."""
    vid = inv["vendor"]["vendor_id"]
    if vid is None:
        return {"not_checked"}
    num = normalise_invoice_number(inv["invoice_number"])
    d = date.fromisoformat(inv["invoice_date"])
    sig = signature(inv["lines"])
    hits = set()
    for e in (e for e in ctx.ledger if e["vendor_id"] == vid and e["status"] in ("APPROVE", "HOLD")):
        same_total = abs(P(inv["total"]) - e["total"]) <= ALLOWANCE
        days = abs((d - date.fromisoformat(e["invoice_date"])).days)
        e_sig = sorted((s["key"], s["qty"]) for s in e["line_signature"])
        if num == e["invoice_number_normalised"]:
            hits.add("confirmed" if e["status"] == "APPROVE" else "possible")
        elif any(num == e["invoice_number_normalised"] + s for s in ctx.suffixes):
            hits.add("revision")
        elif same_total and days <= ctx.window and sig == e_sig:
            hits.add("possible")
        elif same_total:
            hits.add("similar_note")
    return hits or {"none"}


def check_dates(inv, ctx, fail):
    tid = inv["test_id"]
    d = date.fromisoformat(inv["invoice_date"])
    today = date.today()
    if d > today or (today - d).days > ctx.max_age:
        fail(f"date {d} implausible relative to {today} (A-25)")
    want = inv["expect"]["duplicate"]
    got = duplicate_tiers(inv, ctx)
    if got != {want}:
        fail(f"duplicate tiers {sorted(got)} != intended {want!r}")
    if tid == "T-02" and (d != T02_REQUIRED or d < T02_EARLIEST):
        fail(f"T-02 must be dated {T02_REQUIRED} (A-35), got {d}")
    if tid == "T-04":
        v004 = [p for p in ctx.pos.values() if p["vendor_id"] == inv["vendor"]["vendor_id"]]
        for p in v004:
            pd = date.fromisoformat(p["po_date"])
            if not (pd <= d <= pd + timedelta(days=ctx.date_window)):
                fail(f"T-04 date {d} outside {p['po_number']} window {pd}..+{ctx.date_window}d")


def check_fixed_constraints(inv, fail):
    """README_FIXTURES load-bearing constants, checked literally."""
    tid, bank = inv["test_id"], inv.get("bank") or {}
    if tid == "T-01" and (bank.get("account_number"), bank.get("ifsc")) != T01_BANK:
        fail(f"T-01 bank must be {T01_BANK}")
    if tid == "T-03" and (not bank.get("account_number", "").endswith(T03_LAST4) or bank.get("ifsc") != T03_IFSC):
        fail(f"T-03 bank must end {T03_LAST4} with IFSC {T03_IFSC}")


def check_scan_source(inv, ctx, fail):
    """T-05 must be a copy of the approved seeded INV-2041."""
    if not inv.get("scan"):
        return
    num = normalise_invoice_number(inv["invoice_number"])
    orig = [e for e in ctx.ledger if e["invoice_number_normalised"] == num and e["vendor_id"] == inv["vendor"]["vendor_id"]]
    if len(orig) != 1:
        fail(f"scan source {inv['invoice_number']} not found once in ledger")
        return
    e = orig[0]
    pairs = {
        "invoice_date": (inv["invoice_date"], e["invoice_date"]),
        "total": (P(inv["total"]), e["total"]),
        "taxable_value": (P(inv["taxable_value"]), e["taxable_value"]),
        "po_number": (inv["po_reference"], e["po_number"]),
        "status": ("APPROVE", e["status"]),
        "line_signature": (signature(inv["lines"]), sorted((s["key"], s["qty"]) for s in e["line_signature"])),
    }
    for k, (a, b) in pairs.items():
        if a != b:
            fail(f"scan {k} {a!r} != ledger {b!r}")
    p = inv["scan"]
    if not (1 <= abs(p["rotate_degrees"]) <= 2 and p["dpi"] == 200 and p["grayscale"]):
        fail("scan parameters outside the brief (200 DPI, 1-2 degrees, greyscale)")


# ---------------------------------------------------------------- PDF vs manifest

def expected_strings(inv) -> list[str]:
    s = [inv["invoice_number"], normalise_gstin(inv["vendor"]["gstin"]), inr(inv["total"]),
         inr(inv["taxable_value"])]
    s += [inr(l["amount"]) for l in inv["lines"]] + [inr(l["unit_price"]) for l in inv["lines"]]
    s += [l["description"] for l in inv["lines"]]
    s += [inr(c["amount"]) for c in inv["tax"].values() if c]
    if inv["po_reference"]:
        s.append(inv["po_reference"])
    if inv.get("bank"):
        s += [inv["bank"]["account_number"], inv["bank"]["ifsc"]]
    return s


def check_pdf(inv, ctx, sums, fail):
    path = ctx.samples / inv["file"]
    if not path.exists():
        fail(f"missing {path.name}")
        return
    data = path.read_bytes()
    if sums.get(path.name) != hashlib.sha256(data).hexdigest():
        fail("hash does not match SHA256SUMS (regenerate)")
    if len(data) > ctx.max_bytes:
        fail(f"{len(data)} bytes > {ctx.max_bytes} (A-23)")
    doc = pymupdf.open(path)
    try:
        if doc.page_count > ctx.max_pages:
            fail(f"{doc.page_count} pages > {ctx.max_pages} (A-23)")
        if doc.is_encrypted:
            fail("encrypted PDF")
        text = "".join(page.get_text() for page in doc)
        chars = len(re.sub(r"\s", "", text))
        if inv["expect"].get("text_layer", True):
            if chars / doc.page_count < ctx.min_chars:
                fail(f"text layer too thin ({chars} chars) - would be classified as a scan (A-24)")
            flat = re.sub(r"\s+", " ", text)
            for needle in expected_strings(inv):
                if needle not in flat:
                    fail(f"PDF text does not contain {needle!r}")
            d = date.fromisoformat(inv["invoice_date"])
            if not any(d.strftime(f) in flat for f in DATE_FORMATS):
                fail(f"PDF text does not show the date {d}")
        else:
            if chars:
                fail(f"scan has an extractable text layer ({chars} chars)")
            if not all(page.get_images() for page in doc):
                fail("scan page without an image")
    finally:
        doc.close()


# ---------------------------------------------------------------- driver

def check_invoice(inv, ctx, sums) -> list[str]:
    failures = []
    fail = failures.append
    master = check_vendor(inv, ctx, fail)
    check_bank(inv, master, fail)
    po_key = check_po(inv, ctx, fail)
    check_lines(inv, ctx, po_key, fail)
    check_arithmetic(inv, fail)
    check_tax_regime(inv, ctx, fail)
    check_dates(inv, ctx, fail)
    check_fixed_constraints(inv, fail)
    check_scan_source(inv, ctx, fail)
    check_pdf(inv, ctx, sums, fail)
    return failures


def read_sums(ctx) -> dict[str, str]:
    f = ctx.samples / "SHA256SUMS"
    if not f.exists():
        return {}
    return {name: digest for digest, name in (l.split("  ", 1) for l in f.read_text().splitlines() if l)}


def check_set(ctx) -> list[str]:
    """Set-level rules: required tests present, all four layouts used, no stray PDFs."""
    failures = []
    ids = [i["test_id"] for i in ctx.invoices]
    if len(ids) != len(set(ids)):
        failures.append("duplicate test IDs in manifest")
    if missing := REQUIRED_TESTS - set(ids):
        failures.append(f"missing required tests {sorted(missing)}")
    layouts = {i["layout"] for i in ctx.invoices}
    available = {p.stem for p in (HERE / "layouts").glob("*.html")}
    if len(available) < 4 or layouts != available:
        failures.append(f"layouts used {sorted(layouts)} vs available {sorted(available)}")
    stray = {p.name for p in ctx.samples.glob("*.pdf")} - {i["file"] for i in ctx.invoices}
    if stray:
        failures.append(f"PDFs not in manifest: {sorted(stray)}")
    return failures


def main() -> int:
    ctx = Ctx()
    sums = read_sums(ctx)
    rows, bad = [], 0
    for inv in ctx.invoices:
        failures = check_invoice(inv, ctx, sums)
        bad += bool(failures)
        rows.append((inv, failures))
    print(f"{'Test':5} {'Layout':8} {'Vendor':8} {'PO':8} {'Total (Rs)':>13}  Result")
    for inv, failures in rows:
        vid = inv["vendor"]["vendor_id"] or "(none)"
        print(f"{inv['test_id']:5} {inv['layout']:8} {vid:8} {inv['po_reference'] or '-':8} "
              f"{inr(inv['total']):>13}  {'PASS' if not failures else 'FAIL'}")
        for f in failures:
            print(f"      - {f}")
    set_failures = check_set(ctx)
    for f in set_failures:
        print(f"SET FAIL: {f}")
    ok = not bad and not set_failures
    print(f"\n{len(rows) - bad}/{len(rows)} invoices pass" + ("" if not set_failures else "; set checks FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
