"""Placeholder values for the draft templates, taken only from the stored decision (Day 10, DEC-021).

Every value here is read from the decision's evidence, PO detail, duplicate matches and candidate
list, or from the vendor master row the decision identified. Money is formatted once, through the
existing Indian-grouping formatter (app.rules.common.rs); quantities through app.rules.common.qty.
Nothing is computed from a figure that is not stored, and nothing is guessed: a value that is not
available is left out of the mapping so app.drafts.generate raises instead of printing a blank.
"""
import re
from dataclasses import dataclass, field
from datetime import date

from app.drafts.templates import (
    CONFIDENCE_CLAUSE, DUPLICATE_CLAUSE, EQUATION_PHRASE, LINE_EQUATION, PO_LOOKUP_CLAUSE,
)
from app.rules.common import qty, rs

MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
_LINE_INDEX = re.compile(r"line_items\[(\d+)\]")
# Confidence checks in the order their clauses are rendered.
CONFIDENCE_CHECKS = ("critical_field_confidence", "invoice_number_corroboration", "invoice_date_plausible")


class DraftError(Exception):
    """A draft cannot be rendered from what the decision stored."""

    def __init__(self, code: str, message: str, http_status: int = 422):
        super().__init__(message)
        self.code, self.message, self.http_status = code, message, http_status


def human_date(iso: str | None) -> str | None:
    """'2026-10-29' -> '29 Oct 2026'. Month names are explicit, so output never depends on locale."""
    if not iso:
        return None
    try:
        d = date.fromisoformat(iso)
    except (TypeError, ValueError):
        return None
    return f"{d.day} {MONTHS[d.month - 1]} {d.year}"


def days_between(a: str | None, b: str | None) -> int | None:
    try:
        return abs((date.fromisoformat(a) - date.fromisoformat(b)).days)
    except (TypeError, ValueError):
        return None


def line_index(source_fields: list[str]) -> int | None:
    """The invoice line a per-line evidence row is about, from the line index it already records."""
    for f in source_fields or ():
        m = _LINE_INDEX.search(f)
        if m:
            return int(m.group(1))
    return None


@dataclass
class Facts:
    """Everything the fillers may read. Assembled by app.drafts.generate from the stored decision."""
    run_display: str
    file_name: str
    status: str
    reason_codes: list[str]
    evidence: list[dict]
    buyer_state: str
    invoice_number: str | None = None
    invoice_date: str | None = None                 # ISO
    total_paise: int | None = None
    taxable_paise: int | None = None
    vendor_id: str | None = None
    vendor_name: str | None = None                  # master legal name, else the printed name
    vendor_state_code: str | None = None
    vendor_state_name: str | None = None
    po_detail: dict | None = None
    po_candidates: list[dict] = field(default_factory=list)
    duplicates: list[dict] = field(default_factory=list)   # enriched, as views._duplicate_matches returns

    # -------------------------------------------------- evidence access
    def rows(self, code: str) -> list[dict]:
        return [e for e in self.evidence if e.get("reason_code") == code]

    def row(self, code: str) -> dict:
        rows = self.rows(code)
        if not rows:
            raise DraftError("draft_evidence_missing", f"{self.run_display}: no evidence row for {code}")
        return rows[0]

    def po_line(self, index: int | None) -> dict | None:
        for line in (self.po_detail or {}).get("lines", ()):
            if line.get("invoice_line") == index:
                return line
        return None

    def duplicate(self, tier: str) -> dict | None:
        return next((m for m in self.duplicates if m.get("tier") == tier), None)


# ------------------------------------------------------------------ common fields

def common(f: Facts) -> dict:
    """Fields every template may use. Only values that exist are returned."""
    out: dict = {"run_display": f.run_display}
    if f.invoice_number:
        out["invoice_number"] = f.invoice_number
    when = human_date(f.invoice_date)
    if when:
        out["invoice_date"] = when
    if f.total_paise is not None:
        out["total"] = rs(f.total_paise)
    if f.vendor_name:
        out["vendor_name"] = f.vendor_name
    if f.vendor_id:
        out["vendor_id"] = f.vendor_id
    if f.vendor_state_name and f.vendor_state_code:
        out["vendor_state"] = f"{f.vendor_state_name} ({f.vendor_state_code})"
    out["buyer_state"] = f.buyer_state
    po = (f.po_detail or {}).get("po_number")
    if po:
        out["po_number"] = po
    out["document_ref"] = document_ref(f)
    out["subject_ref"] = f"Invoice {f.invoice_number}" if f.invoice_number else document_ref(f)
    return out


def document_ref(f: Facts) -> str:
    """How a draft names the document. Invoice number first; the printed date, then the file name,
    only when the number itself is one of the missing fields. Always resolves to something real."""
    if f.invoice_number:
        when = human_date(f.invoice_date)
        return f"Invoice {f.invoice_number}" + (f" dated {when}" if when else "")
    when = human_date(f.invoice_date)
    if when:
        return f"The invoice dated {when}"
    return f"The invoice in {f.file_name}"


# ------------------------------------------------------------------ per-code fillers

def _po_balance(f: Facts) -> dict:
    f.row("PO_BALANCE_EXCEEDED")
    d = f.po_detail or {}
    approved, this = d.get("previously_approved"), d.get("this_invoice")
    total, allow, remaining = d.get("po_total"), d.get("allowed_overrun"), d.get("remaining")
    if None in (approved, this, total, allow, remaining):
        return {}
    ceiling = total + allow
    cumulative = approved + this
    return {"po_total": rs(total), "previously_approved": rs(approved), "remaining": rs(remaining),
            "this_invoice": rs(this), "cumulative": rs(cumulative), "ceiling": rs(ceiling),
            "allowed_overrun": rs(allow), "excess": rs(cumulative - ceiling)}


def _quantity(f: Facts) -> dict:
    f.row("QUANTITY_EXCEEDS_PO")
    items = []
    for row in f.rows("QUANTITY_EXCEEDS_PO"):
        n = line_index(row["source_fields"])
        line = f.po_line(n)
        q = (line or {}).get("quantity") or {}
        if not line or not q:
            return {}
        po_qty, prev, this = q.get("po"), q.get("previously_approved"), q.get("this_invoice")
        excess = q.get("excess")
        if None in (po_qty, prev, this, excess):
            return {}
        items.append(
            f"Line {n} ({line.get('description')}, order item {line.get('po_item_code')}): {qty(this)} "
            f"units invoiced, {qty(prev)} units already approved against order line "
            f"{line.get('po_line_id')}, which covers {qty(po_qty)} units. That is {qty(prev + this)} units "
            f"in total, {qty(excess)} units more than the order allows. Quantity still open on that line: "
            f"{qty(max(po_qty - prev, 0))} units.")
    return {"items": "\n\n".join(items)} if items else {}


def _unit_price(f: Facts) -> dict:
    f.row("UNIT_PRICE_VARIANCE")
    items = []
    for row in f.rows("UNIT_PRICE_VARIANCE"):
        n = line_index(row["source_fields"])
        line = f.po_line(n)
        up = (line or {}).get("unit_price") or {}
        if not line or up.get("po") is None or up.get("invoice") is None:
            return {}
        items.append(
            f"Line {n} ({line.get('description')}, order item {line.get('po_item_code')}): invoiced at "
            f"{rs(up['invoice'])} per unit against the order price of {rs(up['po'])} per unit, "
            f"{row.get('variance')} against a tolerance of {row.get('allowed')}.")
    return {"items": "\n\n".join(items)} if items else {}


def _unmatched_lines(f: Facts) -> dict:
    f.row("LINE_ITEM_UNMATCHED")
    items = []
    for row in f.rows("LINE_ITEM_UNMATCHED"):
        n = line_index(row["source_fields"])
        described = row.get("actual")
        if n is None or not described:
            return {}
        items.append(f"Line {n}: {described}.")
    return {"items": "\n".join(items)} if items else {}


def _arithmetic(f: Facts) -> dict:
    f.row("ARITHMETIC_MISMATCH")
    items = []
    for row in f.rows("ARITHMETIC_MISMATCH"):
        check = row.get("check") or ""
        if check in EQUATION_PHRASE:
            phrase = EQUATION_PHRASE[check]
        elif check.startswith("arithmetic_line "):
            phrase = LINE_EQUATION.format(n=check[len("arithmetic_line "):])
        else:
            return {}
        if None in (row.get("expected"), row.get("actual"), row.get("variance"), row.get("allowed")):
            return {}
        items.append(f"On {phrase}: we compute {row['expected']}, the invoice shows {row['actual']}, "
                     f"a difference of {row['variance']} (we allow {row['allowed']}).")
    return {"items": "\n\n".join(items)} if items else {}


def _tax_regime(f: Facts) -> dict:
    row = f.row("TAX_REGIME_MISMATCH")
    return {k: row[k] for k in ("expected", "actual") if row.get(k)}


def _missing_fields(f: Facts) -> dict:
    row = f.row("MISSING_REQUIRED_FIELD")
    actual = row.get("actual") or ""
    prefix = "missing: "
    if not actual.startswith(prefix):
        return {}
    return {"missing_fields": actual[len(prefix):]}


def _original(match: dict | None) -> dict:
    """The fields that name the invoice already on record."""
    if not match:
        return {}
    out = {}
    if match.get("invoice_number_raw"):
        out["orig_no"] = match["invoice_number_raw"]
    when = human_date(match.get("invoice_date"))
    if when:
        out["orig_date"] = when
    total = (match.get("total") or {}).get("paise")
    if total is not None:
        out["orig_total"] = rs(total)
    return out


def _confirmed_duplicate(f: Facts) -> dict:
    f.row("CONFIRMED_DUPLICATE")
    return _original(f.duplicate("confirmed"))


def _possible_duplicate(f: Facts) -> dict:
    row = f.row("POSSIBLE_DUPLICATE")
    check = row.get("check")
    clause = DUPLICATE_CLAUSE.get(check)
    if clause is None:
        return {}
    tier = "confirmed" if check == "duplicate_confirmed" else "possible"
    match = f.duplicate(tier)
    out = _original(match)
    if "{days}" in clause:
        days = days_between(f.invoice_date, (match or {}).get("invoice_date"))
        if days is None:
            return {}
        out["days"] = str(days)
    missing = [k for k in ("orig_no", "orig_date", "orig_total") if "{" + k + "}" in clause and k not in out]
    if missing:
        return {}
    out["match_clause"] = clause.format(**out)
    return out


def _possible_revision(f: Facts) -> dict:
    f.row("POSSIBLE_REVISION")
    return _original(f.duplicate("revision"))


def _vendor_status(f: Facts) -> dict:
    row = f.row("VENDOR_BLOCKED")
    return {k: row[k] for k in ("expected", "actual") if row.get(k)}


def _vendor_not_found(f: Facts) -> dict:
    row = f.row("VENDOR_NOT_FOUND")
    out = {}
    if row.get("actual"):
        out["actual"] = row["actual"]
    if row.get("allowed"):
        out["matching_rule"] = row["allowed"]
    if row.get("variance"):
        out["result"] = row["variance"]
    return out


def _confidence(f: Facts) -> dict:
    f.row("LOW_EXTRACTION_CONFIDENCE")
    clauses = []
    for check in CONFIDENCE_CHECKS:
        rows = [r for r in f.rows("LOW_EXTRACTION_CONFIDENCE") if r.get("check") == check]
        if not rows:
            continue
        clause = CONFIDENCE_CLAUSE[check]
        values = {"field": ", ".join(str(r["source_fields"][0]) for r in rows if r.get("source_fields"))}
        if check == "critical_field_confidence":
            observed = min((r["actual"] for r in rows if isinstance(r.get("actual"), (int, float))), default=None)
            if observed is None:
                return {}
            values["observed"] = f"{float(observed):.2f}"
            values["threshold"] = str(rows[0].get("expected") or "")
        else:
            values["observed"] = str(rows[0].get("actual") or "")
            values["threshold"] = str(rows[0].get("expected") or "")
            values["problem"] = str(rows[0].get("variance") or "")
        if not all(values.get(k) for k in ("field", "observed", "threshold")):
            return {}
        if "{problem}" in clause and not values.get("problem"):
            return {}
        clauses.append(clause.format(**values))
    return {"confidence_clause": " ".join(clauses)} if clauses else {}


def _unsupported(f: Facts) -> dict:
    row = f.row("UNSUPPORTED_DOCUMENT")
    return {k: row[k] for k in ("expected", "actual") if row.get(k)}


def _candidate_list(f: Facts) -> str:
    parts = []
    for c in f.po_candidates:
        plausible = ", plausible" if c.get("plausible") else ""
        parts.append(f"{c['po_number']} (score {c['score']}{plausible})")
    return ", ".join(parts)


def _po_not_found(f: Facts) -> dict:
    row = f.row("PO_NOT_FOUND")
    out = {}
    if row.get("variance"):
        clause, values = PO_LOOKUP_CLAUSE["reference_not_this_vendor"], {"reference": row.get("actual"),
                                                                        "vendor_id": f.vendor_id}
    else:
        clause, values = PO_LOOKUP_CLAUSE["no_reference_no_candidate"], {"vendor_id": f.vendor_id,
                                                                        "threshold": row.get("expected")}
    if not all(values.values()):
        return {}
    out["lookup_clause"] = clause.format(**values)
    listed = _candidate_list(f)
    out["candidate_clause"] = (f"Closest candidates by deterministic score: {listed}."
                               if listed else
                               "No open purchase order of this vendor scored high enough to suggest.")
    return out


def _po_inferred(f: Facts) -> dict:
    f.row("PO_INFERRED")
    listed = _candidate_list(f)
    return {"candidate_clause": listed + "."} if listed else {}


def _bank(f: Facts) -> dict:
    row = f.row("BANK_DETAILS_MISMATCH")
    out = {}
    # Both sides come from the evidence the rule recorded, which carries the account number masked
    # to its last four digits and the IFSC in full (FR-041, DEC-001).
    if row.get("expected"):
        out["master_bank"] = row["expected"]
    if row.get("actual"):
        out["invoice_bank"] = row["actual"]
    if row.get("variance"):
        out["difference"] = row["variance"]
    return out


FILLERS = {
    "PO_BALANCE_EXCEEDED": _po_balance,
    "QUANTITY_EXCEEDS_PO": _quantity,
    "UNIT_PRICE_VARIANCE": _unit_price,
    "LINE_ITEM_UNMATCHED": _unmatched_lines,
    "ARITHMETIC_MISMATCH": _arithmetic,
    "TAX_REGIME_MISMATCH": _tax_regime,
    "MISSING_REQUIRED_FIELD": _missing_fields,
    "CONFIRMED_DUPLICATE": _confirmed_duplicate,
    "POSSIBLE_DUPLICATE": _possible_duplicate,
    "POSSIBLE_REVISION": _possible_revision,
    "VENDOR_BLOCKED": _vendor_status,
    "VENDOR_NOT_FOUND": _vendor_not_found,
    "LOW_EXTRACTION_CONFIDENCE": _confidence,
    "UNSUPPORTED_DOCUMENT": _unsupported,
    "PO_NOT_FOUND": _po_not_found,
    "PO_INFERRED": _po_inferred,
    "BANK_DETAILS_MISMATCH": _bank,
}
