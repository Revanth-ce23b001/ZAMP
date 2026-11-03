"""Stage 3 Validate (FR-020..FR-027, §9.1, §9.4, §9.8). Pure.

Required fields, critical-field confidence, date plausibility, arithmetic E1-E4 (plus printed
line amounts) with the per-equation allowance, tax components, and the two P1 rules behind
policy flags. Values that fail are reported, never corrected.
"""
from dataclasses import dataclass
from datetime import date, timedelta

from app.rules.common import Rules, inr, mul_paise, paise, pct_of, rs
from app.rules.types import (
    FAILED, NEEDS_REVIEW, NOT_CHECKED, PASSED, VALIDATE, Evidence, EvidenceLog, Hit, StageOutcome,
)
from app.services.extraction.schema import InvoiceExtraction
from app.utils.normalise import normalise_invoice_number


@dataclass
class Amounts:
    """Paise values the later stages use (taxable is computed when not printed, FR-023)."""
    lines_sum: int | None
    taxable: int | None
    total: int | None
    cgst: int
    sgst: int
    igst: int


def amounts(x: InvoiceExtraction) -> Amounts:
    lines = [mul_paise(li.quantity.value, paise(li.unit_price.value)) for li in x.line_items
             if li.quantity.value is not None and li.unit_price.value is not None]
    lines_sum = sum(lines) if lines and len(lines) == len(x.line_items) else None
    subtotal = paise(x.subtotal.value)
    base = subtotal if subtotal is not None else lines_sum
    discount = paise(x.discount.value) or 0
    taxable = paise(x.taxable_value.value)
    if taxable is None and base is not None:
        taxable = base - discount
    return Amounts(lines_sum, taxable, paise(x.total.value),
                   paise(x.cgst.value) or 0, paise(x.sgst.value) or 0, paise(x.igst.value) or 0)


def _vendor_identity_field(x: InvoiceExtraction) -> tuple[str, object]:
    """Vendor identity is critical via the GSTIN when printed, else the name (FR-017)."""
    return ("vendor_gstin", x.vendor_gstin) if x.vendor_gstin.value else ("vendor_name", x.vendor_name)


def validate(x: InvoiceExtraction, rules: Rules, today: date, log: EvidenceLog,
             vendor_state: str | None = None) -> StageOutcome:
    out = StageOutcome(VALIDATE, PASSED, "")

    def ev(**kw) -> int:
        i = log.add(Evidence(stage=VALIDATE, **kw))
        out.evidence_ids.append(i)
        return i

    # ---- (a) required fields (FR-020, A-07)
    missing = []
    if not (x.invoice_number.value or "").strip():
        missing.append(("invoice number", "invoice_number"))
    if not x.invoice_date.value:
        missing.append(("invoice date", "invoice_date"))
    if not (x.vendor_gstin.value or x.vendor_name.value):
        missing.append(("vendor GSTIN or name", "vendor_gstin"))
    if x.total.value is None:
        missing.append(("total", "total"))
    if not x.line_items:
        missing.append(("line items", "line_items"))
    for n, li in enumerate(x.line_items, start=1):
        for f in ("quantity", "unit_price"):
            if getattr(li, f).value is None:
                missing.append((f"line {n} {f.replace('_', ' ')}", f"line_items[{n}].{f}"))
    i = ev(check="required_fields", source_fields=[f for _, f in missing] or ["invoice_number", "invoice_date",
           "vendor_gstin|vendor_name", "total", "line_items"], expected="all present",
           actual="missing: " + ", ".join(m for m, _ in missing) if missing else "all present",
           variance=None, allowed=None, result=FAILED if missing else PASSED,
           reason_code="MISSING_REQUIRED_FIELD" if missing else None)
    if missing:
        out.hits.append(Hit("MISSING_REQUIRED_FIELD", {"field": ", ".join(m for m, _ in missing)}, i))

    # ---- (b) critical-field confidence (FR-017, A-17); only for values that are present
    vid_name, vid = _vendor_identity_field(x)
    critical = [("invoice_number", x.invoice_number), ("invoice_date", x.invoice_date), (vid_name, vid),
                ("total", x.total)]
    if x.po_reference.value:
        critical.append(("po_reference", x.po_reference))
    for n, li in enumerate(x.line_items, start=1):
        critical += [(f"line_items[{n}].quantity", li.quantity), (f"line_items[{n}].unit_price", li.unit_price)]
    low = []
    for name, f in critical:
        if f.value is None:
            continue
        ok = f.confidence >= rules.confidence
        i = ev(check="critical_field_confidence", source_fields=[name], expected=f">= {rules.confidence}",
               actual=f.confidence, variance=None, allowed=f">= {rules.confidence}",
               result=PASSED if ok else NEEDS_REVIEW, reason_code=None if ok else "LOW_EXTRACTION_CONFIDENCE")
        if not ok:
            low.append((name, f.confidence, i))
    if low:
        worst = min(low, key=lambda t: t[1])
        out.hits.append(Hit("LOW_EXTRACTION_CONFIDENCE",
                            {"field": ", ".join(n for n, _, _ in low), "c": f"{worst[1]:.2f}"}, worst[2]))

    # ---- (c) invoice number corroboration (§9.8): non-empty after normalisation
    if x.invoice_number.value and not normalise_invoice_number(x.invoice_number.value):
        i = ev(check="invoice_number_corroboration", source_fields=["invoice_number"], expected="non-empty",
               actual=x.invoice_number.value, variance=None, allowed=None, result=NEEDS_REVIEW,
               reason_code="LOW_EXTRACTION_CONFIDENCE")
        out.hits.append(Hit("LOW_EXTRACTION_CONFIDENCE", {"field": "invoice_number",
                                                          "c": f"{x.invoice_number.confidence:.2f}"}, i))

    # ---- (d) date plausibility (FR-021, A-25): failure is LOW_EXTRACTION_CONFIDENCE
    if x.invoice_date.value:
        rule = rules.date_rule
        try:
            d = date.fromisoformat(x.invoice_date.value)
            problem = None
            if not rule["allow_future"] and d > today:
                problem = "in the future"
            elif (today - d) > timedelta(days=rule["max_age_days"]):
                problem = f"more than {rule['max_age_days']} days old"
        except ValueError:
            problem = "does not parse"
        i = ev(check="invoice_date_plausible", source_fields=["invoice_date"],
               expected=f"not future, <= {rule['max_age_days']} days old (processing date {today.isoformat()})",
               actual=x.invoice_date.value, variance=problem, allowed=None,
               result=NEEDS_REVIEW if problem else PASSED,
               reason_code=rule["failure_code"] if problem else None)
        if problem:
            out.hits.append(Hit(rule["failure_code"], {"field": "invoice_date",
                                                       "c": f"{x.invoice_date.confidence:.2f}"},
                                i, sentence=rule["failure_message"] + "."))

    # ---- (e) arithmetic (FR-023, FR-024, FR-025, §9.1)
    a = amounts(x)
    allow = rules.allowance
    arith = []

    def equation(n: str, fields: list[str], expected: int | None, actual: int | None, skip: str | None):
        if skip:
            ev(check=f"arithmetic_{n}", source_fields=fields, expected=rs(expected), actual=rs(actual),
               variance=None, allowed=rs(allow), result=NOT_CHECKED, reason_code=None)
            return
        var = actual - expected
        ok = abs(var) <= allow
        i = ev(check=f"arithmetic_{n}", source_fields=fields, expected=rs(expected), actual=rs(actual),
               variance=rs(var), allowed=f"±{rs(allow)}", result=PASSED if ok else FAILED,
               reason_code=None if ok else "ARITHMETIC_MISMATCH")
        if not ok:
            arith.append(Hit("ARITHMETIC_MISMATCH", {"n": n, "variance": inr(abs(var))}, i))

    subtotal = paise(x.subtotal.value)
    discount = paise(x.discount.value) or 0
    # Printed line amounts corroborate quantity x unit price (§9.8).
    for n, li in enumerate(x.line_items, start=1):
        if None not in (li.amount.value, li.quantity.value, li.unit_price.value):
            equation(f"line {n}", [f"line_items[{n}].quantity", f"line_items[{n}].unit_price",
                                   f"line_items[{n}].amount"],
                     mul_paise(li.quantity.value, paise(li.unit_price.value)), paise(li.amount.value), None)
    equation("E1", ["line_items", "subtotal"], a.lines_sum, subtotal,
             "subtotal not printed" if subtotal is None else ("line quantity or price missing" if a.lines_sum is None else None))
    base = subtotal if subtotal is not None else a.lines_sum
    printed_taxable = paise(x.taxable_value.value)
    equation("E2", ["subtotal", "discount", "taxable_value"],
             None if base is None else base - discount, printed_taxable,
             "taxable value not printed (computed as subtotal - discount)" if printed_taxable is None
             else ("subtotal and line amounts unavailable" if base is None else None))
    for comp in ("cgst", "sgst", "igst"):
        rate = getattr(x, f"{comp}_rate").value
        amount = paise(getattr(x, comp).value) or 0
        if rate is None or a.taxable is None:
            equation(f"E3 {comp.upper()}", [comp, f"{comp}_rate", "taxable_value"], None, amount,
                     "rate not printed" if rate is None else "taxable value unavailable")
        else:
            equation(f"E3 {comp.upper()}", [comp, f"{comp}_rate", "taxable_value"], pct_of(a.taxable, rate), amount, None)
    others = sum(paise(c.amount.value) or 0 for c in x.other_charges)
    round_off = paise(x.round_off.value) or 0
    e4_expected = None if a.taxable is None else a.taxable + a.cgst + a.sgst + a.igst + others + round_off
    equation("E4", ["taxable_value", "cgst", "sgst", "igst", "other_charges", "round_off", "total"],
             e4_expected, a.total,
             "total not printed" if a.total is None else ("taxable value unavailable" if e4_expected is None else None))
    if arith:
        out.hits.append(arith[0])          # one code; every failing equation is in the evidence
        out.hits[-1].params["all"] = [h.params["n"] for h in arith]

    # ---- (f) P1 rules behind policy flags (OD-7, FR-022, FR-027)
    if rules.rule_unsupported_document:
        dt, cur = x.document_type.value, x.currency.value
        bad = dt != "tax_invoice" or (cur or "").upper() != rules.currency
        i = ev(check="document_supported", source_fields=["document_type", "currency"],
               expected=f"tax_invoice / {rules.currency}", actual=f"{dt} / {cur}", variance=None, allowed=None,
               result=FAILED if bad else PASSED, reason_code="UNSUPPORTED_DOCUMENT" if bad else None)
        if bad:
            out.hits.append(Hit("UNSUPPORTED_DOCUMENT", {"type": dt, "cur": cur}, i))
    if rules.rule_tax_regime and vendor_state:
        intra = vendor_state == rules.buyer_state
        expected = "CGST + SGST" if intra else "IGST"
        shown = " + ".join(c.upper() for c, v in (("cgst", a.cgst), ("sgst", a.sgst), ("igst", a.igst)) if v) or "no tax"
        bad = (a.igst != 0 or not (a.cgst and a.sgst)) if intra else (a.cgst != 0 or a.sgst != 0 or not a.igst)
        i = ev(check="tax_regime", source_fields=["vendor_gstin", "cgst", "sgst", "igst"],
               expected=expected, actual=shown, variance=None, allowed=None,
               result=FAILED if bad else PASSED, reason_code="TAX_REGIME_MISMATCH" if bad else None)
        if bad:
            out.hits.append(Hit("TAX_REGIME_MISMATCH", {"vs": vendor_state, "expected": expected, "actual": shown}, i))

    codes = {h.code for h in out.hits}
    failed = codes & {"MISSING_REQUIRED_FIELD", "ARITHMETIC_MISMATCH", "UNSUPPORTED_DOCUMENT", "TAX_REGIME_MISMATCH"}
    out.status = FAILED if failed else (NEEDS_REVIEW if codes else PASSED)
    out.summary = ("All checks pass" if not codes else ", ".join(sorted(codes)))
    if not codes:
        checked = [e for e in (log.items[j] for j in out.evidence_ids) if e.check.startswith("arithmetic_E")
                   and e.result == PASSED]
        out.summary = f"{len(checked)} equations reconcile; required fields present and confident"
    return out
