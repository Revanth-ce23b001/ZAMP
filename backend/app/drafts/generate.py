"""Build the resolution drafts for one decided run (Day 10, DEC-021).

Deterministic and read-only: no writes, no audit event, no model call, no network. The drafts are
generated from the stored decision each time they are asked for and are never persisted.

One draft per recipient type. Within a draft the highest-severity code leads and the rest follow as
numbered items, ordered by severity, then the stage order already in the reason-code registry, then
the code name. A placeholder with no value raises DraftError; a draft never prints a blank, a
"None" or a guessed value.
"""
import json
import sqlite3
from string import Formatter

from app.drafts.fields import FILLERS, DraftError, Facts, common
from app.drafts.templates import CLOSING, RECIPIENTS, SIGN_OFF, TEMPLATES, TO_LINE
from app.policy import Policy
from app.rules.common import paise
from app.rules.reason_codes import REGISTRY, Severity

_FORMATTER = Formatter()


def placeholders(text: str) -> set[str]:
    return {name for _, name, _, _ in _FORMATTER.parse(text) if name}


def render(text: str, values: dict, where: str) -> str:
    """Fill a template. Raises rather than print a blank, a "None" or a guessed value."""
    missing = sorted(n for n in placeholders(text)
                     if values.get(n) is None or not str(values[n]).strip())
    if missing:
        raise DraftError("draft_placeholder_missing",
                         f"{where}: no value for {', '.join(missing)}")
    return text.format(**values)


def order_codes(codes: list[str]) -> list[str]:
    """Severity (REJECT first), then the registry's stage order, then the code name."""
    return sorted(codes, key=lambda c: (REGISTRY[c].severity != Severity.REJECT, REGISTRY[c].stage, c))


# ------------------------------------------------------------------ loading the stored decision

def _vendor_id(ledger: sqlite3.Row | None, evidence: list[dict]) -> str | None:
    if ledger is not None and ledger["vendor_id"]:
        return ledger["vendor_id"]
    ident = next((e for e in evidence if e["check"] == "vendor_identified" and e["result"] == "PASSED"), None)
    return ident["expected"] if ident else None


def load_facts(conn: sqlite3.Connection, run_id: str, policy: Policy) -> Facts:
    """Everything the templates may read, from the stored decision. Never selects a trusted-contact
    column, so no draft and no response can carry one (FR-044, DEC-008)."""
    from app.services.upload import display_number
    from app.services.views import _duplicate_matches

    run = conn.execute("SELECT run_number, file_name, status FROM runs WHERE run_id = ?", (run_id,)).fetchone()
    if run is None:
        raise DraftError("run_not_found", "Run not found", 404)
    dec = conn.execute("SELECT * FROM decisions WHERE run_id = ?", (run_id,)).fetchone()
    if dec is None:
        raise DraftError("not_decided",
                         f"Drafts are available once a run is decided (run is {run['status']})", 409)

    evidence = json.loads(dec["evidence"])
    ledger = conn.execute("SELECT vendor_id, invoice_number_raw, invoice_date, total, taxable_value "
                          "FROM invoice_ledger WHERE run_id = ?", (run_id,)).fetchone()
    ext = conn.execute("SELECT payload FROM extractions WHERE run_id = ?", (run_id,)).fetchone()
    payload = json.loads(ext["payload"]) if ext else {}

    def printed(key):
        return (payload.get(key) or {}).get("value")

    vendor_id = _vendor_id(ledger, evidence)
    master = None
    if vendor_id:
        # Explicit whitelist: trusted_contact_name, _phone and _email are never read here.
        master = conn.execute("SELECT vendor_id, legal_name, state_code, state_name, status "
                              "FROM vendors WHERE vendor_id = ?", (vendor_id,)).fetchone()
    total = ledger["total"] if ledger else paise(printed("total"))
    taxable = ledger["taxable_value"] if ledger else None

    return Facts(
        run_display=display_number(run["run_number"]),
        file_name=run["file_name"],
        status=dec["status"],
        reason_codes=json.loads(dec["reason_codes"]),
        evidence=evidence,
        buyer_state=str(policy.assumption("A-03")),
        invoice_number=(ledger["invoice_number_raw"] if ledger else None) or printed("invoice_number"),
        invoice_date=(ledger["invoice_date"] if ledger else None) or printed("invoice_date"),
        total_paise=total,
        taxable_paise=taxable,
        vendor_id=master["vendor_id"] if master else None,
        vendor_name=(master["legal_name"] if master else None) or printed("vendor_name"),
        vendor_state_code=master["state_code"] if master else None,
        vendor_state_name=master["state_name"] if master else None,
        po_detail=json.loads(dec["po_detail"]) if dec["po_detail"] else None,
        po_candidates=json.loads(dec["po_candidates"]),
        duplicates=_duplicate_matches(conn, json.loads(dec["duplicate_matches"])),
    )


# ------------------------------------------------------------------ building the drafts

def _item(code: str, f: Facts) -> tuple[str, str, str]:
    """One code's rendered subject, body and ask."""
    t = TEMPLATES[code]
    values = common(f) | FILLERS[code](f)
    where = f"{f.run_display} {code}"
    return (render(t.subject, values, where), render(t.body, values, where), render(t.ask, values, where))


def build(f: Facts) -> list[dict]:
    """One draft per recipient type, in a fixed recipient order. APPROVE produces none."""
    if f.status == "APPROVE":
        return []
    by_recipient: dict[str, list[str]] = {}
    for code in order_codes([c for c in f.reason_codes if c in TEMPLATES]):
        by_recipient.setdefault(TEMPLATES[code].recipient, []).append(code)

    drafts = []
    for recipient in RECIPIENTS:
        codes = by_recipient.get(recipient)
        if not codes:
            continue
        frame = common(f)
        lead_subject, lead_body, lead_ask = _item(codes[0], f)
        parts = [f"To: {render(TO_LINE[recipient], frame, f'{f.run_display} {recipient}')}",
                 f"Re: {frame['document_ref']}",
                 "",
                 lead_body,
                 "",
                 f"What we need: {lead_ask}"]
        if len(codes) > 1:
            parts += ["", "Also on this invoice:"]
            for n, code in enumerate(codes[1:], start=1):
                _, body, ask = _item(code, f)
                parts += ["", f"{n}. {body}", "", f"   What we need: {ask}"]
        closing = CLOSING.get(f.status)
        if closing is None:
            raise DraftError("draft_status_unsupported", f"{f.run_display}: no draft closing for {f.status}")
        parts += ["", render(closing, frame, f"{f.run_display} closing"), "", SIGN_OFF]
        drafts.append({"recipient_type": recipient, "subject": lead_subject,
                       "body": "\n".join(parts), "codes_covered": codes})
    return drafts


def drafts_for_run(conn: sqlite3.Connection, run_id: str, policy: Policy) -> list[dict]:
    return build(load_facts(conn, run_id, policy))
