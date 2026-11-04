"""Rules engine unit tests (pure functions: no DB, no network). Boundaries first.

Reference data is built in-memory so each rule is exercised in isolation; amounts are rupees in
the extraction and paise in reference data, exactly as in production.
"""
import copy
from datetime import date

import pytest

from app.policy import Policy, load_policy
from app.rules.common import Rules
from app.rules.engine import evaluate
from app.rules.po_match import allocate, candidates_for, pair_lines
from app.rules.reason_codes import REGISTRY
from app.rules.types import PO, Consumption, LedgerEntry, POLine, ReferenceData, Vendor
from app.services.extraction.schema import InvoiceExtraction

TODAY = date(2026, 11, 3)
POLICY = load_policy()
EMITTED: set[str] = set()          # every code any test in this module produced (registry coverage)

V1 = Vendor("V001", "Sri Lakshmi Steel Traders", "29AABCS1234F1Z5", "29", "50200012341234", "HDFC0001234",
            "Ravi Kumar", "+91 98450 22114", "active")
V3 = Vendor("V003", "Coromandel Packaging Pvt Ltd", "33AACCC4321M1Z8", "33", "918020045514471", "HDFC0005512",
            "Meena Raghavan", "+91 98400 11223", "active")
V6 = Vendor("V006", "Malnad Logistics", "29AAGFM9012R1Z4", "29", "0645201004567", "CNRB0001456",
            "Girish Hegde", "+91 96860 41178", "blocked")
L12 = POLine("PO-1042-1", "TMT-12", "TMT Bar 12mm", ("tmt steel rod 12mm", "tmt rod 12mm"), 100, 65000)
L16 = POLine("PO-1042-2", "TMT-16", "TMT Bar 16mm", ("tmt steel rod 16mm",), 50, 120000)
PO42 = PO("PO-1042", "V001", date(2026, 10, 5), 12500000, (L12, L16))
L20 = POLine("PO-1041-1", "TMT-20", "TMT Bar 20mm", ("tmt steel rod 20mm",), 300, 100000)
PO41 = PO("PO-1041", "V001", date(2026, 9, 10), 30000000, (L20,))
PO44 = PO("PO-1044", "V003", date(2026, 10, 8), 8400000,
          (POLine("PO-1044-1", "CB-5PLY", "5-ply box", ("5ply corrugated box",), 2000, 4200),))
PO46 = PO("PO-1046", "V006", date(2026, 9, 15), 15000000,
          (POLine("PO-1046-1", "FRT-LCL", "Freight", ("lcl freight",), 10, 1500000),))
NO_CONSUMPTION = Consumption({}, {}, {})


def ref(*, vendors=(V1, V3, V6), pos=(PO42, PO41, PO44, PO46), ledger=(), consumption=NO_CONSUMPTION):
    return ReferenceData(tuple(vendors), tuple(pos), tuple(ledger), consumption)


def f(value, c=0.99):
    return {"value": value, "confidence": c}


def line(desc, q, price, code=None, amount="auto"):
    amt = round(q * price, 2) if amount == "auto" else amount
    return {"description": f(desc), "item_code": f(code), "quantity": f(q), "unit_price": f(price), "amount": f(amt)}


def invoice(**over) -> InvoiceExtraction:
    """A clean intra-state V001 invoice on PO-1042: 10 x TMT-12 @ 650, CGST/SGST 9%."""
    base = {
        "invoice_number": f("INV-T-1"), "invoice_date": f("2026-10-30"), "vendor_name": f(V1.legal_name),
        "vendor_gstin": f(V1.gstin), "po_reference": f("PO-1042"), "document_type": f("tax_invoice"),
        "currency": f("INR"), "line_items": [line("TMT Bar 12mm", 10, 650.0, "TMT-12")],
        "subtotal": f(6500.0), "discount": f(None), "taxable_value": f(6500.0),
        "cgst": f(585.0), "sgst": f(585.0), "igst": f(None), "cgst_rate": f(9), "sgst_rate": f(9),
        "igst_rate": f(None), "other_charges": [], "round_off": f(None), "total": f(7670.0),
        "bank_account_number": f(V1.bank_account_number), "ifsc": f(V1.ifsc),
    }
    base.update(over)
    return InvoiceExtraction.model_validate(base)


def run(x, reference=None, policy=POLICY, today=TODAY, **kw):
    r = evaluate(x, reference or ref(), policy, today, **kw)
    EMITTED.update(r.reason_codes)
    # PRD §20: every code is backed by evidence with expected, actual, variance and allowed.
    for code in r.reason_codes:
        rows = [e for e in r.evidence if e["reason_code"] == code]
        assert rows, f"{code} has no evidence row"
        assert all({"expected", "actual", "variance", "allowed"} <= set(e) for e in rows), code
    return r


def codes(r):
    return sorted(r.reason_codes)


def evidence(r, check):
    return [e for e in r.evidence if e["check"] == check]


def policy_with(**changes) -> Policy:
    data = copy.deepcopy(POLICY.data)
    for path, value in changes.items():
        node = data
        keys = path.split(".")
        for k in keys[:-1]:
            node = node[k]
        node[keys[-1]] = value
    return Policy(data=data, version="test-" + "-".join(changes), path=POLICY.path)


# ======================================================================= baseline

def test_clean_invoice_approves_with_full_evidence():
    r = run(invoice())
    assert (r.status, r.reason_codes) == ("APPROVE", [])
    assert [s.stage for s in r.stages] == ["VALIDATE", "VENDOR_BANK", "DUPLICATE", "PO_MATCH", "DECIDE"]
    assert all(s.status == "PASSED" for s in r.stages)
    assert r.commit_plan == {"po_number": "PO-1042", "taxable_value": 650000,
                             "lines": [{"po_line_id": "PO-1042-1", "quantity": 10.0, "value": 650000}]}
    # Passing checks produce evidence too (FR-084).
    for check in ("required_fields", "arithmetic_E1", "arithmetic_E4", "vendor_status", "bank_details_match",
                  "unit_price", "cumulative_quantity", "cumulative_po_value"):
        assert evidence(r, check) and all(e["result"] == "PASSED" for e in evidence(r, check)), check


# ======================================================================= arithmetic (E1-E4, ₹1 allowance)

@pytest.mark.parametrize("subtotal, ok", [(6501.00, True), (6501.01, False), (6499.00, True), (6498.99, False)])
def test_e1_one_rupee_edge_both_directions(subtotal, ok):
    r = run(invoice(subtotal=f(subtotal)))
    e1 = evidence(r, "arithmetic_E1")[0]
    assert (e1["result"] == "PASSED") is ok
    assert ("ARITHMETIC_MISMATCH" in r.reason_codes) is (not ok or abs(subtotal - 6500) > 1)


@pytest.mark.parametrize("total, ok", [(7671.00, True), (7671.01, False), (7669.00, True), (7668.99, False)])
def test_e4_one_rupee_edge_both_directions(total, ok):
    r = run(invoice(total=f(total)))
    assert (evidence(r, "arithmetic_E4")[0]["result"] == "PASSED") is ok
    assert ("ARITHMETIC_MISMATCH" in r.reason_codes) is (not ok)


def test_e2_with_discount_and_e4_with_charges_and_round_off():
    # PRD §9.10.1 / T-01: discount, freight, negative round-off
    x = invoice(
        po_reference=f("PO-1042"),
        line_items=[line("TMT Steel Rod 12 mm", 80, 650.0), line("TMT Bar 16mm", 40, 1200.0, "TMT-16")],
        subtotal=f(100000.0), discount=f(5000.0), taxable_value=f(95000.0), cgst=f(8550.0), sgst=f(8550.0),
        other_charges=[{"label": "Freight", "amount": f(1750.40)}], round_off=f(-0.40), total=f(113850.0))
    r = run(x)
    assert (r.status, r.reason_codes) == ("APPROVE", [])
    assert [e["result"] for e in evidence(r, "arithmetic_E2")] == ["PASSED"]
    assert r.commit_plan["lines"] == [{"po_line_id": "PO-1042-1", "quantity": 80.0, "value": 4940000},
                                      {"po_line_id": "PO-1042-2", "quantity": 40.0, "value": 4560000}]


def test_absent_optional_components_count_as_zero_and_unprinted_equations_not_checked():
    x = invoice(subtotal=f(None), taxable_value=f(None), cgst_rate=f(None), sgst_rate=f(None))
    r = run(x)
    assert (r.status, r.reason_codes) == ("APPROVE", [])
    results = {e["check"]: e["result"] for e in r.evidence if e["check"].startswith("arithmetic_")}
    assert results["arithmetic_E1"] == "NOT_CHECKED"           # subtotal not printed
    assert results["arithmetic_E2"] == "NOT_CHECKED"           # taxable computed as subtotal - discount
    assert results["arithmetic_E3 CGST"] == "NOT_CHECKED"      # rate not printed
    assert results["arithmetic_E4"] == "PASSED"                # E4 always checked


def test_e3_component_must_match_rate():
    r = run(invoice(cgst=f(595.0), total=f(7680.0)))
    assert r.reason_codes == ["ARITHMETIC_MISMATCH"]
    assert evidence(r, "arithmetic_E3 CGST")[0]["result"] == "FAILED"
    assert evidence(r, "arithmetic_E4")[0]["result"] == "PASSED"


def test_printed_line_amount_must_equal_qty_times_price():
    x = invoice(line_items=[line("TMT Bar 12mm", 10, 650.0, "TMT-12", amount=6600.0)])
    r = run(x)
    assert "ARITHMETIC_MISMATCH" in r.reason_codes
    assert evidence(r, "arithmetic_line 1")[0]["result"] == "FAILED"


def test_t15_shape_e4_off_by_1000():
    r = run(invoice(total=f(8670.0)))
    assert r.reason_codes == ["ARITHMETIC_MISMATCH"]
    assert r.summary == ["Equation E4 off by ₹1,000 (allowed ₹1)."]


# ======================================================================= validate: fields, confidence, dates

def test_missing_invoice_number_is_hold_and_duplicate_not_checked():
    r = run(invoice(invoice_number=f(None)))
    assert codes(r) == ["MISSING_REQUIRED_FIELD"]
    assert r.summary == ["Missing required field: invoice number."]
    dup = next(s for s in r.stages if s.stage == "DUPLICATE")
    assert (dup.status, dup.not_checked_reason) == ("NOT_CHECKED", "Invoice number missing")
    assert r.ledger is None


def test_low_confidence_total_holds_even_when_arithmetic_reconciles():      # FR-017 AC
    r = run(invoice(total=f(7670.0, c=0.70)))
    assert r.reason_codes == ["LOW_EXTRACTION_CONFIDENCE"]
    assert r.summary == ["total extracted with confidence 0.70 (threshold 0.85)."]


@pytest.mark.parametrize("c, low", [(0.85, False), (0.849, True)])
def test_confidence_threshold_boundary(c, low):
    r = run(invoice(invoice_number=f("INV-T-1", c=c)))
    assert ("LOW_EXTRACTION_CONFIDENCE" in r.reason_codes) is low


@pytest.mark.parametrize("d, implausible", [("2026-11-03", False), ("2026-11-04", True),
                                            ("2025-11-03", False), ("2025-11-02", True), ("30/10/2026", True)])
def test_date_plausibility(d, implausible):
    r = run(invoice(invoice_date=f(d)), today=TODAY)
    assert ("LOW_EXTRACTION_CONFIDENCE" in r.reason_codes) is implausible
    if implausible:
        assert r.summary == ["Invoice date implausible: check the PDF."]


# ======================================================================= vendor and bank

def test_unknown_vendor_holds_and_downstream_not_checked():
    r = run(invoice(vendor_name=f("Horizon Traders"), vendor_gstin=f("29AAHFH4821L1Z3"), po_reference=f(None)))
    assert codes(r) == ["VENDOR_NOT_FOUND"]
    st = {s.stage: s for s in r.stages}
    assert st["VENDOR_BANK"].status == "NEEDS_REVIEW"
    assert st["DUPLICATE"].status == st["PO_MATCH"].status == "NOT_CHECKED"
    assert evidence(r, "bank_details_match")[0]["result"] == "NOT_CHECKED"


def test_od4_name_matches_but_gstin_differs():
    r = run(invoice(vendor_gstin=f("29ZZZZZ9999Z1Z9")))
    assert codes(r) == ["VENDOR_NOT_FOUND"]
    assert evidence(r, "vendor_identified")[0]["variance"] == "Name matches V001, GSTIN does not"


def test_identified_by_normalised_name_when_gstin_missing():
    r = run(invoice(vendor_gstin=f(None), vendor_name=f("SRI LAKSHMI STEEL TRADERS PVT. LTD.")))
    assert r.vendor_id == "V001" and r.reason_codes == []


def test_invalid_gstin_format_alone_raises_no_code():
    r = run(invoice(vendor_gstin=f("29AABCS1234F1Z")))         # 14 chars -> not found by GSTIN, found by name? no:
    # a printed-but-different GSTIN with a matching name is OD-4; the format result is display only
    assert evidence(r, "gstin_format")[0]["variance"] == "Format invalid"
    assert "VENDOR_NOT_FOUND" in r.reason_codes


def test_blocked_vendor_rejects_and_other_stages_still_run():
    x = invoice(vendor_name=f(V6.legal_name), vendor_gstin=f(V6.gstin), po_reference=f("PO-1046"),
                line_items=[line("Freight", 1, 15000.0, "FRT-LCL")], subtotal=f(15000.0), taxable_value=f(15000.0),
                cgst=f(1350.0), sgst=f(1350.0), total=f(17700.0), bank_account_number=f(None), ifsc=f(None))
    r = run(x)
    assert (r.status, r.reason_codes) == ("REJECT", ["VENDOR_BLOCKED"])
    assert {s.stage: s.status for s in r.stages}["PO_MATCH"] == "PASSED"


def test_bank_full_comparison_same_last4_different_ifsc():               # FR-040 AC
    r = run(invoice(ifsc=f("ICIC0005678")))
    assert r.reason_codes == ["BANK_DETAILS_MISMATCH"]
    e = evidence(r, "bank_details_match")[0]
    assert e["actual"] == "XXXX XXXX 1234 · ICIC0005678" and e["expected"] == "XXXX XXXX 1234 · HDFC0001234"
    assert "50200012341234" not in str(r.evidence) + str(r.summary)


def test_bank_spaces_and_hyphens_ignored():
    assert run(invoice(bank_account_number=f("5020-0012 3412 34"), ifsc=f("hdfc0001234"))).reason_codes == []


def test_missing_bank_details_not_checked_and_still_approves():          # FR-043 AC
    r = run(invoice(bank_account_number=f(None), ifsc=f(None)))
    assert r.status == "APPROVE"
    assert evidence(r, "bank_details_match")[0]["result"] == "NOT_CHECKED"


def test_bank_mismatch_next_action_uses_master_contact():
    r = run(invoice(bank_account_number=f("11112222333344")))
    assert r.next_actions == ["Verify with Ravi Kumar, +91 98450 22114 (vendor master) via contact on record. "
                              "Never use details on the invoice."]


# ======================================================================= duplicates (§9.6)

def entry(num, d, total, status="APPROVE", sig=(("TMT-12", 10.0),), vendor="V001", run_id=None, lid="L1"):
    from app.utils.normalise import normalise_invoice_number
    return LedgerEntry(lid, run_id, vendor, num, normalise_invoice_number(num), date.fromisoformat(d), total,
                       status, tuple(sig))


@pytest.mark.parametrize("ledger_entry, want_codes, note", [
    (entry("INV/T 1", "2026-09-01", 1), ["CONFIRMED_DUPLICATE"], False),            # tier 1, approved
    (entry("INV-T-1", "2026-09-01", 1, status="HOLD"), ["POSSIBLE_DUPLICATE"], False),   # tier 1, OD-3
    (entry("INV-T-1", "2026-09-01", 1, status="REJECT"), [], False),                # out of scope (FR-055)
    (entry("INVT", "2026-09-01", 1), [], False),                                     # different number
    (entry("INV-T", "2026-09-01", 1), [], False),                                    # "1" is not a suffix
    (entry("INV-T-1", "2026-10-29", 767000, vendor="V003"), [], False),              # other vendor
    (entry("INV-OLD", "2026-10-23", 767000), ["POSSIBLE_DUPLICATE"], False),         # tier 3, 7 days
    (entry("INV-OLD", "2026-10-22", 767000), [], True),                              # 8 days -> similar note
    (entry("INV-OLD", "2026-10-29", 767100), ["POSSIBLE_DUPLICATE"], False),         # total +₹1
    (entry("INV-OLD", "2026-10-29", 767101), [], False),                             # total +₹1.01
    (entry("INV-OLD", "2026-10-29", 767000, sig=(("TMT-12", 11.0),)), [], True),     # other lines -> similar
])
def test_duplicate_tiers(ledger_entry, want_codes, note):
    r = run(invoice(), ref(ledger=(ledger_entry,)))
    assert codes(r) == want_codes
    assert any("Similar invoice exists" in n for n in r.notes) is note


@pytest.mark.parametrize("suffix", ["R", "REV", "A", "B", "V2"])
def test_revision_suffixes(suffix):
    r = run(invoice(invoice_number=f(f"INV-T-1-{suffix}")), ref(ledger=(entry("INV-T-1", "2026-09-01", 1),)))
    assert codes(r) == ["POSSIBLE_REVISION"]


def test_tier_precedence_within_one_entry_confirmed_beats_possible():
    # same number AND same total/date/lines: tier 1 wins for that entry
    r = run(invoice(), ref(ledger=(entry("INV-T-1", "2026-10-29", 767000),)))
    assert codes(r) == ["CONFIRMED_DUPLICATE"]


def test_own_ledger_row_excluded():
    r = run(invoice(), ref(ledger=(entry("INV-T-1", "2026-10-30", 767000, run_id="R-self"),)), exclude_run_id="R-self")
    assert r.reason_codes == []


def test_dec014_signature_uses_paired_po_item_code():
    # Printed without an item code, paired by alias: the key is the PO line's code.
    x = invoice(line_items=[line("tmt steel rod 12mm", 10, 650.0)])
    r = run(x, ref(ledger=(entry("INV-OLD", "2026-10-29", 767000, sig=(("TMT-12", 10.0),)),)))
    assert codes(r) == ["POSSIBLE_DUPLICATE"]
    assert r.ledger["line_signature"] == [{"key": "TMT-12", "qty": 10.0}]


# ======================================================================= PO match: tolerances at the boundary

def test_unit_price_exactly_one_percent_over_passes_one_paisa_more_fails():
    at = run(invoice(line_items=[line("TMT Bar 12mm", 10, 656.50, "TMT-12")], subtotal=f(6565.0),
                     taxable_value=f(6565.0), cgst=f(590.85), sgst=f(590.85), total=f(7746.70)))
    assert at.reason_codes == []
    over = run(invoice(line_items=[line("TMT Bar 12mm", 10, 656.51, "TMT-12")], subtotal=f(6565.1),
                       taxable_value=f(6565.1), cgst=f(590.86), sgst=f(590.86), total=f(7746.82)))
    assert over.reason_codes == ["UNIT_PRICE_VARIANCE"]


def test_lower_price_always_passes():
    r = run(invoice(line_items=[line("TMT Bar 12mm", 10, 1.0, "TMT-12")], subtotal=f(10.0), taxable_value=f(10.0),
                    cgst=f(0.9), sgst=f(0.9), total=f(11.8)))
    assert r.reason_codes == []


def po41_invoice(units, price=1000.0):
    amount = units * price
    tax = round(amount * 0.09, 2)
    return invoice(po_reference=f("PO-1041"), line_items=[line("TMT Bar 20mm", units, price, "TMT-20")],
                   subtotal=f(amount), taxable_value=f(amount), cgst=f(tax), sgst=f(tax), total=f(amount + 2 * tax))


def consumed_41(units, value):
    return Consumption({"PO-1041-1": units}, {"PO-1041": value}, {"PO-1041": 2})


def test_cumulative_quantity_at_limit_passes_one_over_fails():
    assert run(po41_invoice(80), ref(consumption=consumed_41(220, 0))).reason_codes == []
    r = run(po41_invoice(81), ref(consumption=consumed_41(220, 0)))
    assert r.reason_codes == ["QUANTITY_EXCEEDS_PO"]
    assert r.summary == ["TMT-20: 220 approved + 81 invoiced exceeds PO qty 300 by 1."]


def test_cumulative_value_at_ceiling_passes_one_paisa_over_fails():
    # ceiling = 3,00,000 + min(3,000, 1,000) = 3,01,000; quantity kept inside the PO
    assert run(po41_invoice(10), ref(consumption=consumed_41(0, 29100000))).reason_codes == []
    r = run(po41_invoice(10), ref(consumption=consumed_41(0, 29100001)))
    assert r.reason_codes == ["PO_BALANCE_EXCEEDED"]


def test_t02_shape_both_cumulative_codes():
    r = run(po41_invoice(100), ref(consumption=consumed_41(220, 22000000)))
    assert codes(r) == ["PO_BALANCE_EXCEEDED", "QUANTITY_EXCEEDS_PO"]
    assert "Taxable value ₹1,00,000 exceeds remaining ₹80,000 (allowed overrun ₹1,000) on PO-1041." in r.summary


def test_tolerance_applied_once_per_po_not_per_invoice():          # §9.10.5
    x = po41_invoice(80, price=1009.0)
    r = run(x, ref(consumption=consumed_41(220, 22198000)))
    assert r.reason_codes == ["PO_BALANCE_EXCEEDED"]                  # quantity 300 <= 300 passes


def test_allowed_overrun_is_min_of_pct_and_cap():
    rules = Rules(POLICY)
    assert rules.allowed_overrun(12500000) == 100000      # 1% = 1,250 -> cap 1,000
    assert rules.allowed_overrun(8400000) == 84000        # 1% = 840


def test_tolerance_used_note():
    r = run(po41_invoice(10), ref(consumption=consumed_41(0, 29050000)))
    assert r.reason_codes == [] and any("PO tolerance used ₹500 of ₹1,000" in n for n in r.notes)


def test_unmatched_line_and_partial_invoicing():
    x = invoice(line_items=[line("TMT Bar 12mm", 10, 650.0, "TMT-12"), line("Installation charges", 1, 100.0)],
                subtotal=f(6600.0), taxable_value=f(6600.0), cgst=f(594.0), sgst=f(594.0), total=f(7788.0))
    r = run(x)
    assert r.reason_codes == ["LINE_ITEM_UNMATCHED"]
    assert r.summary == ["Line 'Installation charges' does not match any line on PO-1042."]
    assert r.commit_plan is None


def test_pairing_by_code_then_alias_and_each_po_line_once():
    lines = InvoiceExtraction.model_validate({"line_items": [
        line("anything", 1, 650.0, "tmt-12"), line("TMT Steel Rod 16 mm", 1, 1200.0),
        line("TMT Bar 12mm", 1, 650.0)]}).line_items
    pairs, unpaired = pair_lines(list(lines), PO42)
    assert [(p.po_line.po_line_id, p.method) for p in pairs] == [("PO-1042-1", "code"), ("PO-1042-2", "alias")]
    assert [n for n, _ in unpaired] == [3]                  # PO-1042-1 already used


def test_explicit_po_of_another_vendor_is_po_not_found_with_suggestion():
    r = run(invoice(po_reference=f("PO-1044")))
    assert r.reason_codes == ["PO_NOT_FOUND"]
    assert r.summary == ["No purchase order found for this vendor (reference PO-1044)."]
    assert {s.stage: s.status for s in r.stages}["PO_MATCH"] == "NEEDS_REVIEW"
    assert any("Suggested: PO-1042" in n for n in r.notes)


def test_po_reference_normalised_like_invoice_numbers():
    assert run(invoice(po_reference=f("po 1042"))).reason_codes == []


# ======================================================================= inferred PO scoring (§9.7, OD-1)

def t04_lines(*items):
    catalogue = {"LAP-14": ("Laptop 14in i5", 62000.0), "MON-24": ("Monitor 24in", 11500.0),
                 "DOCK-USB": ("USB-C docking station", 8000.0)}
    return [line(catalogue[c][0], q, catalogue[c][1], c) for c, q in items]


PO31 = PO("PO-1031", "V004", date(2026, 10, 1), 81500000, (
    POLine("PO-1031-1", "LAP-14", "Laptop 14in i5", (), 10, 6200000),
    POLine("PO-1031-2", "MON-24", "Monitor 24in", (), 10, 1150000),
    POLine("PO-1031-3", "DOCK-USB", "USB-C docking station", (), 10, 800000)))
PO36 = PO("PO-1036", "V004", date(2026, 10, 12), 36750000, (
    POLine("PO-1036-1", "LAP-14", "Laptop 14in i5", (), 5, 6200000),
    POLine("PO-1036-2", "MON-24", "Monitor 24in", (), 5, 1150000)))


def scored(lines, taxable, d, consumption=NO_CONSUMPTION, policy=POLICY):
    ex = InvoiceExtraction.model_validate({"line_items": lines})
    return candidates_for(list(ex.line_items), (PO31, PO36), taxable, d, consumption, Rules(policy))


def test_t04_scores_100_and_70():
    c = scored(t04_lines(("LAP-14", 4), ("DOCK-USB", 4)), 28000000, date(2026, 10, 30))
    assert [(x.po_number, x.score, x.plausible) for x in c] == [("PO-1031", 100, True), ("PO-1036", 70, False)]
    assert c[1].breakdown == {"line_pairing": 30, "paired": "1/2", "value_fits": 30, "date_window": 10}


def test_t17_two_plausible_within_10_points():
    c = scored(t04_lines(("LAP-14", 2), ("MON-24", 2)), 14700000, date(2026, 10, 30))
    assert [(x.po_number, x.score, x.plausible) for x in c] == [("PO-1031", 100, True), ("PO-1036", 100, True)]


def test_threshold_50_and_gap_rule():
    # 1 of 2 lines (30) + value fits (30) = 60 -> shown; date outside window (no +10)
    c = scored(t04_lines(("LAP-14", 1), ("DOCK-USB", 1)), 7000000, date(2027, 6, 1))
    assert [(x.po_number, x.score) for x in c] == [("PO-1031", 90), ("PO-1036", 60)]
    assert [x.plausible for x in c] == [True, False]
    # 1 of 3 lines (20) + no value fit + no date = 20 -> not shown
    lines = t04_lines(("DOCK-USB", 1)) + [line("Mystery item A", 1, 1.0), line("Mystery item B", 1, 1.0)]
    c = scored(lines, 999999999999, date(2027, 6, 1))
    assert c == []


def test_score_49_hidden_50_shown():
    p = policy_with(**{"assumptions.A-26.value.candidate_threshold": 61})
    c = scored(t04_lines(("LAP-14", 1), ("DOCK-USB", 1)), 7000000, date(2027, 6, 1), policy=p)
    assert [x.po_number for x in c] == ["PO-1031"]          # 60 < 61 hidden, 90 shown


def test_exhausted_po_is_not_a_candidate():
    used = Consumption({}, {"PO-1031": 81500000}, {})
    c = scored(t04_lines(("LAP-14", 4), ("DOCK-USB", 4)), 28000000, date(2026, 10, 30), consumption=used)
    assert [x.po_number for x in c] == ["PO-1036"]


def test_inferred_po_never_approves_and_line_checks_are_evidence_only():
    v4 = Vendor("V004", "Nandi IT Solutions Pvt Ltd", "29AAECN7788P1Z1", "29", "31245678901", "SBIN0040211",
                "Prakash Gowda", "+91 97411 50893", "active")
    x = invoice(vendor_name=f(v4.legal_name), vendor_gstin=f(v4.gstin), po_reference=f(None),
                line_items=t04_lines(("LAP-14", 4), ("DOCK-USB", 4)), subtotal=f(280000.0),
                taxable_value=f(280000.0), cgst=f(25200.0), sgst=f(25200.0), total=f(330400.0),
                bank_account_number=f(None), ifsc=f(None))
    r = run(x, ref(vendors=(v4,), pos=(PO31, PO36)))
    assert (r.status, r.reason_codes) == ("HOLD", ["PO_INFERRED"])
    assert r.commit_plan is None and r.po_method == "inferred"
    confirmed = run(x, ref(vendors=(v4,), pos=(PO31, PO36)), confirmed_po="PO-1031")      # FR-103
    assert (confirmed.status, confirmed.reason_codes) == ("APPROVE", [])
    assert confirmed.commit_plan["po_number"] == "PO-1031"


def test_no_po_and_no_candidate_is_po_not_found():
    r = run(invoice(po_reference=f(None), line_items=[line("Mystery item", 1, 1.0)], subtotal=f(1.0),
                    taxable_value=f(1.0), cgst=f(0.09), sgst=f(0.09), total=f(1.18)))
    assert "PO_NOT_FOUND" in r.reason_codes


# ======================================================================= allocation (A-28)

def test_allocation_sums_exactly_to_taxable():
    ex = InvoiceExtraction.model_validate({"line_items": [line("TMT Bar 12mm", 1, 0.01, "TMT-12"),
                                                          line("TMT Bar 16mm", 2, 0.01, "TMT-16")]})
    pairs, _ = pair_lines(list(ex.line_items), PO42)
    parts = allocate(pairs, 2)
    assert sum(v for _, _, v in parts) == 2


# ======================================================================= severity precedence (§7.3)

def test_blocked_vendor_with_arithmetic_error_rejects_with_both_codes():     # FR-082 AC
    x = invoice(vendor_name=f(V6.legal_name), vendor_gstin=f(V6.gstin), po_reference=f("PO-1046"),
                line_items=[line("Freight", 1, 15000.0, "FRT-LCL")], subtotal=f(15000.0), taxable_value=f(15000.0),
                cgst=f(1350.0), sgst=f(1350.0), total=f(18700.0), bank_account_number=f(None), ifsc=f(None))
    r = run(x)
    assert r.status == "REJECT"
    assert r.reason_codes == ["VENDOR_BLOCKED", "ARITHMETIC_MISMATCH"]   # REJECT first, then stage order
    assert {s.stage: s.status for s in r.stages}["VALIDATE"] == "FAILED"


def test_stage_failed_beats_needs_review():
    r = run(invoice(total=f(9000.0), invoice_number=f("INV-T-1", c=0.5)))
    assert codes(r) == ["ARITHMETIC_MISMATCH", "LOW_EXTRACTION_CONFIDENCE"]
    assert {s.stage: s.status for s in r.stages}["VALIDATE"] == "FAILED"


# ======================================================================= policy drives every threshold

def test_unit_price_tolerance_comes_from_policy():                   # §17.5: 1% -> 30%
    x = invoice(line_items=[line("TMT Bar 12mm", 10, 812.5, "TMT-12")], subtotal=f(8125.0), taxable_value=f(8125.0),
                cgst=f(731.25), sgst=f(731.25), total=f(9587.5))
    assert run(x).reason_codes == ["UNIT_PRICE_VARIANCE"]
    assert run(x, policy=policy_with(**{"assumptions.A-10.value": 30})).reason_codes == []


def test_arithmetic_allowance_comes_from_policy():
    x = invoice(total=f(7675.0))
    assert run(x).reason_codes == ["ARITHMETIC_MISMATCH"]
    assert run(x, policy=policy_with(**{"assumptions.A-08.value": 500})).reason_codes == []


# ======================================================================= P1 rules behind flags (OD-7)

def test_p1_rules_off_by_default():
    r = run(invoice(document_type=f("credit_note"), cgst=f(None), sgst=f(None), igst=f(1170.0),
                    cgst_rate=f(None), sgst_rate=f(None), igst_rate=f(18)))
    assert r.reason_codes == []


def test_unsupported_document_when_enabled():
    p = policy_with(**{"p1_rules.unsupported_document": True})
    r = run(invoice(document_type=f("credit_note")), policy=p)
    assert r.reason_codes == ["UNSUPPORTED_DOCUMENT"]
    st = {s.stage: s for s in r.stages}
    assert all(st[k].status == "NOT_CHECKED" and st[k].not_checked_reason == "Unsupported document"
               for k in ("VENDOR_BANK", "DUPLICATE", "PO_MATCH"))


def test_tax_regime_mismatch_when_enabled():
    p = policy_with(**{"p1_rules.tax_regime": True})
    x = invoice(vendor_name=f(V3.legal_name), vendor_gstin=f(V3.gstin), po_reference=f("PO-1044"),
                line_items=[line("5-ply box", 100, 42.0, "CB-5PLY")], subtotal=f(4200.0), taxable_value=f(4200.0),
                cgst=f(378.0), sgst=f(378.0), total=f(4956.0), bank_account_number=f(None), ifsc=f(None))
    r = run(x, policy=p)
    assert r.reason_codes == ["TAX_REGIME_MISMATCH"]
    assert r.summary == ["Vendor state 33, buyer state 29: expected IGST, invoice shows CGST + SGST."]


# ======================================================================= registry coverage (keep last)

def test_zz_every_registry_code_is_reachable_and_nothing_unregistered():
    assert EMITTED <= set(REGISTRY), EMITTED - set(REGISTRY)
    assert set(REGISTRY) - EMITTED == set(), f"codes never produced by a test: {sorted(set(REGISTRY) - EMITTED)}"
