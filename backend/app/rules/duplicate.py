"""Stage 5 Duplicate (FR-050..FR-055, §9.5, §9.6, OD-3, DEC-014). Pure.

Tiers per ledger entry, evaluated in order, first match per entry wins:
  1 confirmed  same vendor + same normalised number -> CONFIRMED_DUPLICATE if the original is
               APPROVE, POSSIBLE_DUPLICATE if it is HOLD (OD-3)
  2 revision   normalised number = original + recognised suffix -> POSSIBLE_REVISION
  3 possible   same total (+/- allowance), dates within the window, same line signature
               -> POSSIBLE_DUPLICATE
  4 similar    same total only -> informational note, no code
Ledger scope: entries of this vendor with status APPROVE or HOLD (FR-055), excluding this run.
"""
from dataclasses import dataclass, field
from datetime import date

from app.rules.common import Rules, rs
from app.rules.types import (
    DUPLICATE, FAILED, NEEDS_REVIEW, NOT_CHECKED, PASSED, Evidence, EvidenceLog, Hit, LedgerEntry, StageOutcome,
)
from app.utils.normalise import normalise_invoice_number


def _fmt_date(d: date) -> str:
    return f"{d.day} {d.strftime('%b')}"


@dataclass
class DuplicateResult:
    outcome: StageOutcome
    notes: list[str] = field(default_factory=list)
    duplicate_of: str | None = None        # ledger_id of the APPROVE original (CONFIRMED_DUPLICATE)
    matches: list[dict] = field(default_factory=list)


def duplicate(vendor_id: str | None, invoice_number: str | None, invoice_date: date | None, total: int | None,
              signature: tuple, ledger: tuple[LedgerEntry, ...], rules: Rules, log: EvidenceLog,
              unsupported: bool = False, exclude_run_id: str | None = None) -> DuplicateResult:
    out = StageOutcome(DUPLICATE, PASSED, "")
    res = DuplicateResult(out)
    reason = ("Unsupported document" if unsupported else "Vendor not identified" if vendor_id is None
              else "Invoice number missing" if not invoice_number else None)
    if reason:
        out.status, out.not_checked_reason, out.summary = NOT_CHECKED, reason, f"Not checked: {reason.lower()}"
        return res

    def ev(**kw) -> int:
        i = log.add(Evidence(stage=DUPLICATE, **kw))
        out.evidence_ids.append(i)
        return i

    num = normalise_invoice_number(invoice_number)
    scope = [e for e in ledger if e.vendor_id == vendor_id and e.status in rules.dup_scope
             and (exclude_run_id is None or e.run_id != exclude_run_id)]
    for e in scope:
        same_total = total is not None and abs(total - e.total) <= rules.dup_total_allowance
        days = abs((invoice_date - e.invoice_date).days) if invoice_date else None
        orig = {"orig_no": e.invoice_number_raw, "orig_date": _fmt_date(e.invoice_date), "orig_status": e.status}
        link = {"ledger_id": e.ledger_id, "run_id": e.run_id, **orig}
        if num == e.invoice_number_normalised:
            code = "CONFIRMED_DUPLICATE" if e.status == "APPROVE" else "POSSIBLE_DUPLICATE"
            i = ev(check="duplicate_confirmed", source_fields=["vendor", "invoice_number"],
                   expected="no ledger entry with this vendor and normalised number",
                   actual=f"{e.invoice_number_raw} ({e.status})", variance=f"normalised {num}", allowed=None,
                   result=FAILED if code == "CONFIRMED_DUPLICATE" else NEEDS_REVIEW, reason_code=code)
            params = orig if code == "CONFIRMED_DUPLICATE" else {**orig, "total": rs(total), "days": days}
            out.hits.append(Hit(code, params, i))
            if code == "CONFIRMED_DUPLICATE" and res.duplicate_of is None:
                res.duplicate_of = e.ledger_id
            res.matches.append({**link, "tier": "confirmed"})
        elif any(num == e.invoice_number_normalised + s for s in rules.revision_suffixes):
            i = ev(check="duplicate_revision", source_fields=["invoice_number"],
                   expected=f"not {e.invoice_number_normalised} + {'/'.join(rules.revision_suffixes)}",
                   actual=invoice_number, variance=f"revision of {e.invoice_number_raw}", allowed=None,
                   result=NEEDS_REVIEW, reason_code="POSSIBLE_REVISION")
            out.hits.append(Hit("POSSIBLE_REVISION", orig, i))
            res.matches.append({**link, "tier": "revision"})
        elif same_total and days is not None and days <= rules.dup_window_days and signature == e.line_signature:
            i = ev(check="duplicate_possible", source_fields=["total", "invoice_date", "line_items"],
                   expected=f"not (same total, <= {rules.dup_window_days} days, same lines)",
                   actual=f"{e.invoice_number_raw}: {rs(e.total)}, {days} days apart, same lines", variance=None,
                   allowed=None, result=NEEDS_REVIEW, reason_code="POSSIBLE_DUPLICATE")
            out.hits.append(Hit("POSSIBLE_DUPLICATE", {**orig, "total": rs(total), "days": days}, i))
            res.matches.append({**link, "tier": "possible"})
        elif same_total:
            note = f"Similar invoice exists: {e.invoice_number_raw}, {_fmt_date(e.invoice_date)}, {rs(e.total)}"
            ev(check="duplicate_similar", source_fields=["total"], expected=None, actual=note, variance=
               f"{days} days apart" if days is not None else None, allowed=None, result="INFO", reason_code=None)
            res.notes.append(note)
            res.matches.append({**link, "tier": "similar"})
    if not out.hits and not res.notes:
        ev(check="duplicate_search", source_fields=["vendor", "invoice_number", "total", "invoice_date", "line_items"],
           expected="no duplicate", actual=f"{len(scope)} ledger entries compared", variance=None, allowed=None,
           result=PASSED, reason_code=None)

    codes = {h.code for h in out.hits}
    out.status = FAILED if "CONFIRMED_DUPLICATE" in codes else (NEEDS_REVIEW if codes else PASSED)
    out.summary = (", ".join(sorted(codes)) if codes else
                   ("No duplicates; " + "; ".join(res.notes)) if res.notes else "No duplicates")
    return res
