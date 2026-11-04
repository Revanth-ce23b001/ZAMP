"""Day 8 Part B: value flagged for review (DEC-019, proposed by implementation, pending review)."""
import json

from app.services import exposure as ex
from app.services.extraction.schema import InvoiceExtraction
from tests.test_review import VERIFIED, api, approve, run  # noqa: F401  (api is a fixture)
from tests.test_scenarios import SNAPSHOT

RECORDS = {r["file_name"][:4].rstrip("_"): r for r in json.loads(SNAPSHOT.read_text(encoding="utf-8"))["records"]}


def line(po_qty, prev, this, excess, po_price, inv_price):
    return {"quantity": {"po": po_qty, "previously_approved": prev, "this_invoice": this, "excess": excess},
            "unit_price": {"po": po_price, "invoice": inv_price, "variance_pct": 0, "allowed_pct": 1}}


# T-02 shape: PO-1041, 220 of 300 units (₹2,20,000) approved; this invoice 100 units at ₹1,000 (₹1,00,000).
T02_PO = {"previously_approved": 22000000, "this_invoice": 10000000, "ceiling": 30100000,
          "lines": [line(300, 220, 100, 20, 100000, 100000)]}


# ---------- one worked example per code

def test_po_balance_exceeded_is_cumulative_taxable_minus_ceiling():
    # 2,20,000 + 1,00,000 - 3,01,000 = ₹19,000
    assert ex.contributions(["PO_BALANCE_EXCEEDED"], T02_PO, None, 11800000) == {"PO_BALANCE_EXCEEDED": 1900000}


def test_quantity_exceeds_po_is_excess_times_po_unit_price():
    # 220 + 100 - 300 = 20 units over, x ₹1,000 = ₹20,000
    assert ex.contributions(["QUANTITY_EXCEEDS_PO"], T02_PO, None, 11800000) == {"QUANTITY_EXCEEDS_PO": 2000000}


def test_unit_price_variance_counts_positive_terms_only():
    # T-08: 80 coils at ₹1,250 against PO ₹1,000 -> 80 x ₹250 = ₹20,000. A second line priced BELOW
    # the PO (₹900 vs ₹1,000, 10 units) would be -₹1,000 and is not counted.
    po = {"previously_approved": 0, "this_invoice": 10000000, "ceiling": 10100000,
          "lines": [line(100, 0, 80, 0, 100000, 125000), line(100, 0, 10, 0, 100000, 90000)]}
    assert ex.contributions(["UNIT_PRICE_VARIANCE"], po, None, 11800000) == {"UNIT_PRICE_VARIANCE": 2000000}


def test_arithmetic_mismatch_is_printed_minus_computed_total():
    # T-15: printed total ₹8,670; computed ₹6,500 + ₹585 + ₹585 = ₹7,670 -> ₹1,000
    x = InvoiceExtraction.model_validate(RECORDS["T15"]["payload"])
    assert ex.contributions(["ARITHMETIC_MISMATCH"], None, x, 867000) == {"ARITHMETIC_MISMATCH": 100000}


def test_full_total_codes_contribute_the_invoice_total():
    for code in ("CONFIRMED_DUPLICATE", "VENDOR_BLOCKED", "BANK_DETAILS_MISMATCH"):
        assert ex.contributions([code], None, None, 10620000) == {code: 10620000}


def test_every_other_code_contributes_zero():
    other = ["PO_INFERRED", "POSSIBLE_DUPLICATE", "POSSIBLE_REVISION", "VENDOR_NOT_FOUND", "LINE_ITEM_UNMATCHED",
             "LOW_EXTRACTION_CONFIDENCE", "MISSING_REQUIRED_FIELD", "PO_NOT_FOUND", "UNSUPPORTED_DOCUMENT",
             "TAX_REGIME_MISMATCH"]
    assert set(ex.contributions(other, T02_PO, None, 11800000).values()) == {0}
    assert ex.driving(ex.contributions(other, T02_PO, None, 11800000)) == (None, 0)


def test_negative_overrun_is_zero_not_a_credit():
    under = {"previously_approved": 0, "this_invoice": 5000000, "ceiling": 10100000, "lines": []}
    assert ex.po_balance_excess(under) == 0


# ---------- max, never sum

def test_max_not_sum_when_one_invoice_fires_balance_and_quantity():
    contrib = ex.contributions(["QUANTITY_EXCEEDS_PO", "PO_BALANCE_EXCEEDED"], T02_PO, None, 11800000)
    assert contrib == {"QUANTITY_EXCEEDS_PO": 2000000, "PO_BALANCE_EXCEEDED": 1900000}
    assert ex.driving(contrib) == ("QUANTITY_EXCEEDS_PO", 2000000)           # ₹20,000, not ₹39,000


# ---------- two buckets (Day 8B): max-not-sum inside each, never combined

def test_codes_belong_to_exactly_one_bucket():
    assert set(ex.BUCKETS["discrepancy"]) == {"PO_BALANCE_EXCEEDED", "QUANTITY_EXCEEDS_PO", "UNIT_PRICE_VARIANCE",
                                              "ARITHMETIC_MISMATCH"}
    assert set(ex.BUCKETS["held"]) == {"CONFIRMED_DUPLICATE", "VENDOR_BLOCKED", "BANK_DETAILS_MISMATCH"}
    assert not set(ex.BUCKETS["discrepancy"]) & set(ex.BUCKETS["held"])


def test_one_invoice_can_contribute_to_both_buckets():
    # A bank-details mismatch (₹1,18,000 total) on an invoice that also overbills the PO unit price by
    # ₹20,000 counts ₹20,000 as a discrepancy AND ₹1,18,000 as invoice value held: not ₹1,38,000 anywhere.
    po = {"previously_approved": 0, "this_invoice": 10000000, "ceiling": 10100000,
          "lines": [line(100, 0, 80, 0, 100000, 125000)]}
    contrib = ex.contributions(["BANK_DETAILS_MISMATCH", "UNIT_PRICE_VARIANCE"], po, None, 11800000)
    assert ex.by_bucket(contrib) == {"discrepancy": ("UNIT_PRICE_VARIANCE", 2000000),
                                     "held": ("BANK_DETAILS_MISMATCH", 11800000)}


def test_an_invoice_can_contribute_to_only_one_bucket():
    contrib = ex.contributions(["QUANTITY_EXCEEDS_PO", "PO_BALANCE_EXCEEDED"], T02_PO, None, 11800000)
    assert ex.by_bucket(contrib) == {"discrepancy": ("QUANTITY_EXCEEDS_PO", 2000000), "held": (None, 0)}


def test_max_not_sum_applies_within_each_bucket():
    contrib = {"CONFIRMED_DUPLICATE": 500, "BANK_DETAILS_MISMATCH": 700, "UNIT_PRICE_VARIANCE": 300,
               "ARITHMETIC_MISMATCH": 400}
    assert ex.by_bucket(contrib) == {"discrepancy": ("ARITHMETIC_MISMATCH", 400), "held": ("BANK_DETAILS_MISMATCH", 700)}


def test_endpoint_returns_no_combined_total(api):
    run(api, "T02"), run(api, "T03")
    v = api.get("/api/dashboard").json()["value_flagged_for_review"]

    def keys(o, path=""):
        if isinstance(o, dict):
            for k, x in o.items():
                yield f"{path}.{k}"
                yield from keys(x, f"{path}.{k}")
        elif isinstance(o, list):
            for x in o:
                yield from keys(x, path + "[]")
    assert "total" not in v and "invoices_flagged" not in v
    assert {k for k in keys(v) if k.endswith(".total")} == {".discrepancy.total", ".held.total"}
    assert v["discrepancy"]["total"]["paise"] + v["held"]["total"]["paise"] not in (
        [b["amount"]["paise"] for b in v["breakdown"]])                     # no row carries a sum either


def test_ties_break_deterministically_by_severity():
    assert ex.driving({"UNIT_PRICE_VARIANCE": 500, "BANK_DETAILS_MISMATCH": 500}) == ("BANK_DETAILS_MISMATCH", 500)


# ---------- through the pipeline (cached extractions, no model calls)

def test_zero_decided_runs_is_an_empty_state(api):
    v = api.get("/api/dashboard").json()["value_flagged_for_review"]
    assert v["decided_runs"] == 0 and v["breakdown"] == []
    assert v["discrepancy"]["total"]["paise"] == 0 and v["held"]["total"]["paise"] == 0


def test_pipeline_breakdown_and_overridden_invoice_still_counts(api):
    t01, t02, t03 = run(api, "T01"), run(api, "T02"), run(api, "T03")
    before = api.get("/api/dashboard").json()["value_flagged_for_review"]
    rows = {(b["run_id"], b["bucket"]): b for b in before["breakdown"]}
    assert all(run_id != t01 for run_id, _ in rows)                         # clean approval: nothing flagged
    t02_row = rows[(t02, "discrepancy")]                                    # T-02: measured discrepancy only
    assert (t02_row["driving_code"], t02_row["amount"]["paise"]) == ("QUANTITY_EXCEEDS_PO", 2000000)
    assert t02_row["contributions"] == {"QUANTITY_EXCEEDS_PO": 2000000, "PO_BALANCE_EXCEEDED": 1900000}
    assert (t02, "held") not in rows
    t03_row = rows[(t03, "held")]                                           # T-03: invoice value only
    assert (t03_row["driving_code"], t03_row["amount"]["paise"]) == ("BANK_DETAILS_MISMATCH", 4956000)
    assert (t03, "discrepancy") not in rows
    assert before["discrepancy"]["total"]["paise"] == 2000000 and before["held"]["total"]["paise"] == 4956000
    assert before["decided_runs"] == 3

    # T-22: the reviewer verifies the bank change and approves. The discrepancy was resolved, not absent.
    r = approve(api, t03, ["BANK_DETAILS_MISMATCH"], VERIFIED)
    assert r.status_code == 200 and r.json()["status"] == "APPROVE"
    after = api.get("/api/dashboard").json()["value_flagged_for_review"]
    assert {(b["run_id"], b["bucket"]): b["amount"]["paise"] for b in after["breakdown"]}[(t03, "held")] == 4956000
    assert after["held"] == before["held"] and after["discrepancy"] == before["discrepancy"]


def test_values_are_integer_paise_with_a_display_string(api):
    run(api, "T02")
    b = api.get("/api/dashboard").json()["value_flagged_for_review"]["breakdown"][0]
    assert isinstance(b["amount"]["paise"], int) and b["amount"]["display"].startswith("₹")
    assert all(isinstance(v, int) for v in b["contributions"].values())
