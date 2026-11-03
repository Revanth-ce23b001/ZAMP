"""Rules engine: stages 3-7 over one extraction (PRD §5.1, §12.1). Pure: no I/O, no DB, no LLM.

Stage continuation: every stage runs wherever its inputs exist; a stage whose prerequisite is
missing is Not Checked with the reason stated. The final status comes only from severity.
"""
from dataclasses import dataclass, field
from datetime import date

from app.policy import Policy
from app.rules.common import Rules, paise
from app.rules.decide import decide
from app.rules.duplicate import duplicate
from app.rules.po_match import line_signature, po_match, resolve_po
from app.rules.types import EvidenceLog, ReferenceData, StageOutcome
from app.rules.validate import amounts, validate
from app.rules.vendor_bank import vendor_bank
from app.services.extraction.schema import InvoiceExtraction
from app.utils.normalise import normalise_invoice_number


@dataclass
class EngineResult:
    status: str
    reason_codes: list[str]
    summary: list[str]
    next_actions: list[str]
    evidence: list[dict]
    notes: list[str]
    stages: list[StageOutcome]
    vendor_id: str | None
    matched_po: str | None
    po_method: str | None
    po_candidates: list[dict]
    commit_plan: dict | None            # what commit_approval rechecks and writes (explicit/confirmed PO only)
    ledger: dict | None                 # the invoice_ledger row for this run, if it can be recorded
    duplicate_of: str | None
    duplicate_matches: list[dict] = field(default_factory=list)


def evaluate(x: InvoiceExtraction, ref: ReferenceData, policy: Policy, today: date,
             exclude_run_id: str | None = None, confirmed_po: str | None = None) -> EngineResult:
    rules = Rules(policy)
    log = EvidenceLog()

    # Stage 4 first in evaluation order only to know the vendor's state for the P1 regime rule;
    # its evidence is kept in its own stage. Stage 3 runs regardless (continuation).
    vlog = EvidenceLog()
    pre = vendor_bank(x, ref.vendors, rules, vlog)
    vendor_state = pre.vendor.state_code if pre.vendor else (x.vendor_gstin.value or "")[:2] or None

    s3 = validate(x, rules, today, log, vendor_state=vendor_state)
    unsupported = any(h.code == "UNSUPPORTED_DOCUMENT" for h in s3.hits)
    v = vendor_bank(x, ref.vendors, rules, log, unsupported=unsupported)
    vendor = v.vendor
    a = amounts(x)
    try:
        inv_date = date.fromisoformat(x.invoice_date.value) if x.invoice_date.value else None
    except ValueError:
        inv_date = None

    # DEC-015: signature keys canonicalised through the invoice's own (explicit/confirmed) PO.
    po_for_sig = resolve_po(confirmed_po or x.po_reference.value, ref.pos)
    if po_for_sig is not None and (vendor is None or po_for_sig.vendor_id != vendor.vendor_id):
        po_for_sig = None
    signature = line_signature(list(x.line_items), po_for_sig)

    d = duplicate(vendor.vendor_id if vendor else None, x.invoice_number.value, inv_date, a.total, signature,
                  ref.ledger, rules, log, unsupported=unsupported, exclude_run_id=exclude_run_id)
    p = po_match(x, vendor.vendor_id if vendor else None, ref.pos, ref.consumption, a.taxable, inv_date, rules,
                 log, unsupported=unsupported, confirmed_po=confirmed_po)
    stages = [s3, v.outcome, d.outcome, p.outcome]
    dec = decide(stages)

    commit_plan = None
    if p.method in ("explicit", "confirmed") and p.commit_lines:
        commit_plan = {"po_number": p.matched_po, "taxable_value": a.taxable,
                       "lines": [{"po_line_id": lid, "quantity": q, "value": val} for lid, q, val in p.commit_lines]}

    ledger = None
    if vendor is not None and x.invoice_number.value and inv_date and a.total is not None and a.taxable is not None:
        ledger = {
            "vendor_id": vendor.vendor_id,
            "invoice_number_raw": x.invoice_number.value,
            "invoice_number_normalised": normalise_invoice_number(x.invoice_number.value),
            "invoice_date": inv_date.isoformat(),
            "total": a.total, "taxable_value": a.taxable,
            "cgst": paise(x.cgst.value), "sgst": paise(x.sgst.value), "igst": paise(x.igst.value),
            "po_number": p.matched_po if p.method in ("explicit", "confirmed") else None,
            "line_signature": [{"key": k, "qty": q} for k, q in signature],
        }

    return EngineResult(
        status=dec.status, reason_codes=dec.reason_codes, summary=dec.summary, next_actions=dec.next_actions,
        evidence=[e.as_dict() for e in log.items], notes=d.notes + p.notes,
        stages=stages + [dec.outcome], vendor_id=vendor.vendor_id if vendor else None,
        matched_po=p.matched_po, po_method=p.method,
        po_candidates=[{"po_number": c.po_number, "score": c.score, "breakdown": c.breakdown,
                        "plausible": c.plausible} for c in p.candidates],
        commit_plan=commit_plan, ledger=ledger, duplicate_of=d.duplicate_of, duplicate_matches=d.matches,
    )
