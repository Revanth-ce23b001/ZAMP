"""Value flagged for review (DEC-019, proposed by implementation, pending review).

Per decided run, the rupee value of the discrepancies the rules surfaced, in integer paise:
  PO_BALANCE_EXCEEDED    cumulative taxable value (previously approved + this invoice) minus the PO ceiling
  QUANTITY_EXCEEDS_PO    excess quantity x PO unit price, summed over lines
  UNIT_PRICE_VARIANCE    (invoice unit price - PO unit price) x invoice quantity, positive terms only
  ARITHMETIC_MISMATCH    |printed total - computed total| (the E4 equation)
  CONFIRMED_DUPLICATE, VENDOR_BLOCKED, BANK_DETAILS_MISMATCH    the full invoice total
  every other code       zero
Day 8B: the codes fall into two buckets that are reported separately and never combined:
  discrepancy  (measured discrepancy)   PO_BALANCE_EXCEEDED, QUANTITY_EXCEEDS_PO, UNIT_PRICE_VARIANCE,
                                        ARITHMETIC_MISMATCH: the error the rules computed
  held         (invoice value at risk)  CONFIRMED_DUPLICATE, VENDOR_BLOCKED, BANK_DETAILS_MISMATCH:
                                        the full value of an invoice stopped before payment
Within each bucket an invoice counts once, at its LARGEST single contribution, never the sum, because
several codes can describe one problem. An invoice can appear in both buckets. Codes count as flagged
at decision time: a code a reviewer later overrode still contributes, because the override resolved
it rather than proving it absent. Read-only: computed from stored decisions, extractions, ledger and
override records.
"""
import json
import sqlite3

from app.rules.common import mul_paise, paise
from app.rules.validate import amounts
from app.services.extraction.schema import InvoiceExtraction
from app.services.upload import display_number
from app.utils.money import money

FULL_TOTAL = ("CONFIRMED_DUPLICATE", "VENDOR_BLOCKED", "BANK_DETAILS_MISMATCH")
DISCREPANCY = ("PO_BALANCE_EXCEEDED", "QUANTITY_EXCEEDS_PO", "UNIT_PRICE_VARIANCE", "ARITHMETIC_MISMATCH")
BUCKETS = {"discrepancy": DISCREPANCY, "held": FULL_TOTAL}
# Tie-break when two codes contribute the same amount (deterministic, most severe first).
ORDER = ("CONFIRMED_DUPLICATE", "VENDOR_BLOCKED", "BANK_DETAILS_MISMATCH", "PO_BALANCE_EXCEEDED",
         "QUANTITY_EXCEEDS_PO", "UNIT_PRICE_VARIANCE", "ARITHMETIC_MISMATCH")
RULE = ("Largest single contribution per invoice within each figure, never the sum, because several codes can "
        "describe one problem. An invoice can count in both figures; the two are never added together.")
OVERRIDE_NOTE = ("Counted as flagged at decision time, including codes a reviewer later overrode: an override "
                 "resolves a discrepancy, it does not mean it was never there.")


def po_balance_excess(po_detail: dict | None) -> int:
    if not po_detail or po_detail.get("this_invoice") is None:
        return 0
    return max(0, po_detail["previously_approved"] + po_detail["this_invoice"] - po_detail["ceiling"])


def quantity_excess_value(po_detail: dict | None) -> int:
    total = 0
    for line in (po_detail or {}).get("lines", []):
        q, up = line.get("quantity"), line.get("unit_price")
        if q and up and q.get("excess"):
            total += max(0, mul_paise(q["excess"], up["po"]))
    return total


def unit_price_variance_value(po_detail: dict | None) -> int:
    total = 0
    for line in (po_detail or {}).get("lines", []):
        q, up = line.get("quantity"), line.get("unit_price")
        if q and up:
            term = mul_paise(q["this_invoice"], up["invoice"] - up["po"])
            total += max(0, term)                          # positive terms only
    return total


def arithmetic_difference(x: InvoiceExtraction | None) -> int:
    """|printed total - computed total|, with the computed total built exactly as the E4 equation in
    app.rules.validate.validate builds it: taxable + CGST + SGST + IGST + other charges + round-off."""
    if x is None:
        return 0
    a = amounts(x)
    if a.taxable is None or a.total is None:
        return 0
    others = sum(paise(c.amount.value) or 0 for c in x.other_charges)
    round_off = paise(x.round_off.value) or 0
    return abs(a.total - (a.taxable + a.cgst + a.sgst + a.igst + others + round_off))


def contributions(codes: list[str], po_detail: dict | None, extraction: InvoiceExtraction | None,
                  invoice_total: int | None) -> dict[str, int]:
    """Every code's contribution for one invoice, in paise (codes with no formula contribute zero)."""
    out = {}
    for code in dict.fromkeys(codes):                      # de-duplicated, order kept
        if code == "PO_BALANCE_EXCEEDED":
            out[code] = po_balance_excess(po_detail)
        elif code == "QUANTITY_EXCEEDS_PO":
            out[code] = quantity_excess_value(po_detail)
        elif code == "UNIT_PRICE_VARIANCE":
            out[code] = unit_price_variance_value(po_detail)
        elif code == "ARITHMETIC_MISMATCH":
            out[code] = arithmetic_difference(extraction)
        elif code in FULL_TOTAL:
            out[code] = invoice_total or 0
        else:
            out[code] = 0
    return out


def driving(contrib: dict[str, int]) -> tuple[str | None, int]:
    """The single largest contribution (max, never sum) and the code that drove it."""
    best = None
    for code, amount in contrib.items():
        rank = ORDER.index(code) if code in ORDER else len(ORDER)
        if amount > 0 and (best is None or (amount, -rank) > (best[1], -best[2])):
            best = (code, amount, rank)
    return (best[0], best[1]) if best else (None, 0)


def by_bucket(contrib: dict[str, int]) -> dict[str, tuple[str | None, int]]:
    """Max-not-sum applied independently inside each bucket."""
    return {name: driving({c: v for c, v in contrib.items() if c in codes}) for name, codes in BUCKETS.items()}


def flagged_value(conn: sqlite3.Connection) -> dict:
    rows = conn.execute(
        """
        SELECT r.run_id, r.run_number, d.reason_codes, d.po_detail, e.payload,
               l.total AS ledger_total, l.invoice_number_raw
          FROM runs r
          JOIN decisions d ON d.run_id = r.run_id
          LEFT JOIN extractions e ON e.run_id = r.run_id
          LEFT JOIN invoice_ledger l ON l.run_id = r.run_id
         WHERE r.status = 'DECIDED'
         ORDER BY r.run_number
        """).fetchall()
    breakdown = []
    for r in rows:
        codes = list(json.loads(r["reason_codes"]))
        for (o,) in conn.execute("SELECT codes_overridden FROM overrides WHERE run_id = ? ORDER BY created_at, rowid",
                                 (r["run_id"],)):
            codes += [c for c in json.loads(o) if c not in codes]      # overridden codes still count
        payload = json.loads(r["payload"]) if r["payload"] else None
        x = InvoiceExtraction.model_validate(payload) if payload else None
        total = r["ledger_total"] if r["ledger_total"] is not None else (paise(x.total.value) if x else None)
        contrib = contributions(codes, json.loads(r["po_detail"]) if r["po_detail"] else None, x, total)
        for bucket, (code, amount) in by_bucket(contrib).items():
            if amount > 0:
                breakdown.append({
                    "run_id": r["run_id"], "run_display": display_number(r["run_number"]),
                    "invoice_number": r["invoice_number_raw"] or (x.invoice_number.value if x else None),
                    "bucket": bucket, "driving_code": code, "amount": money(amount),
                    "contributions": {k: v for k, v in contrib.items() if v > 0 and k in BUCKETS[bucket]},
                })
    breakdown.sort(key=lambda b: (b["bucket"] != "discrepancy", -b["amount"]["paise"], b["run_display"]))

    def figure(bucket: str) -> dict:
        rows_ = [b for b in breakdown if b["bucket"] == bucket]
        return {"total": money(sum(b["amount"]["paise"] for b in rows_)), "invoices": len(rows_),
                "codes": list(BUCKETS[bucket])}

    # Deliberately no combined total: the two figures measure different things (DEC-019).
    return {
        "decided_runs": len(rows),
        "discrepancy": figure("discrepancy"),
        "held": figure("held"),
        "breakdown": breakdown,
        "rule": RULE,
        "override_note": OVERRIDE_NOTE,
    }
