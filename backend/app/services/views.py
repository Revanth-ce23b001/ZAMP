"""Read models for the API (FR-110..FR-117). Money is integer paise plus a display string;
account numbers never appear; trusted-contact phone/email never appear here (FR-044)."""
import json
import sqlite3

from app.policy import Policy
from app.rules.common import Rules, paise
from app.rules.reason_codes import REGISTRY
from app.services.upload import display_number
from app.utils.money import money

DEMO_LABEL = "Demo dataset: synthetic invoices"          # FR-115

# PRD §14.2, verbatim.
DOES_NOT_VERIFY = [
    {"not_verified": "Live GST portal registration or status",
     "instead": "GSTIN format check and match against vendor master"},
    {"not_verified": "Bank account ownership",
     "instead": "Full account + IFSC comparison against vendor master; human verification via trusted contact"},
    {"not_verified": "Goods receipt (GRN)", "instead": 'Two-way PO match only; UI labels it "Two-way match"'},
    {"not_verified": "ERP records", "instead": "Local synthetic vendor, PO and ledger data"},
    {"not_verified": "Real-world payment execution",
     "instead": 'No payments are made; APPROVE means "cleared for payment" in the prototype'},
]


# ------------------------------------------------------------------ PO balances (FR-111)

def po_balances(conn: sqlite3.Connection, policy: Policy) -> list[dict]:
    """Remaining is PO total minus committed approvals. The allowed overrun is a separate field and
    is never folded into remaining (FR-068)."""
    rules = Rules(policy)
    rows = conn.execute(
        """
        SELECT po.po_number, po.vendor_id, v.legal_name AS vendor_name, po.po_date, po.currency, po.po_total,
               (SELECT COUNT(*) FROM po_lines l WHERE l.po_number = po.po_number) AS line_count,
               (SELECT COALESCE(SUM(c.value), 0) FROM po_consumption c JOIN po_lines l ON l.po_line_id = c.po_line_id
                 WHERE l.po_number = po.po_number) AS approved
          FROM purchase_orders po JOIN vendors v ON v.vendor_id = po.vendor_id
         ORDER BY po.po_number
        """).fetchall()
    out = []
    for r in rows:
        allow = rules.allowed_overrun(r["po_total"])
        out.append({
            "po_number": r["po_number"], "vendor_id": r["vendor_id"], "vendor_name": r["vendor_name"],
            "po_date": r["po_date"], "currency": r["currency"], "line_count": r["line_count"],
            "po_total": money(r["po_total"]),
            "approved": money(r["approved"]),                       # committed APPROVE only (FR-069)
            "remaining": money(r["po_total"] - r["approved"]),
            "allowed_overrun": money(allow),
            "ceiling": money(r["po_total"] + allow),
            "pct_used": round(r["approved"] * 100 / r["po_total"], 1) if r["po_total"] else 0.0,
        })
    return out


# ------------------------------------------------------------------ runs (FR-113)

def _run_row(r: sqlite3.Row) -> dict:
    payload = json.loads(r["payload"]) if r["payload"] else {}
    codes = json.loads(r["reason_codes"]) if r["reason_codes"] else []
    total = r["ledger_total"] if r["ledger_total"] is not None else paise((payload.get("total") or {}).get("value"))
    number = r["invoice_number_raw"] or (payload.get("invoice_number") or {}).get("value")
    vendor_name = r["vendor_name"] or (payload.get("vendor_name") or {}).get("value")
    return {
        "run_id": r["run_id"], "run_number": r["run_number"], "run_display": display_number(r["run_number"]),
        "file_name": r["file_name"], "run_status": r["status"],
        # Inbox status: the decision when decided, else the run state (FR-113 filter chips).
        "status": r["decision_status"] or r["status"],
        "vendor_id": r["vendor_id"], "vendor_name": vendor_name, "invoice_number": number,
        "total": money(total) if total is not None else None,
        "reason_codes": codes, "matched_po": r["matched_po"], "decided_by": r["decided_by"],
        "committed": bool(r["committed"]) if r["committed"] is not None else False,
        "created_at": r["created_at"], "completed_at": r["completed_at"], "duration_ms": r["duration_ms"],
        "cached": bool(r["cached"]) if r["cached"] is not None else None,
        "error_message": r["error_message"],
    }


RUNS_SQL = """
SELECT r.*, d.status AS decision_status, d.reason_codes, d.matched_po, d.decided_by, d.committed,
       l.vendor_id, l.invoice_number_raw, l.total AS ledger_total, l.po_number AS ledger_po,
       v.legal_name AS vendor_name, e.payload, e.cached
  FROM runs r
  LEFT JOIN decisions d ON d.run_id = r.run_id
  LEFT JOIN invoice_ledger l ON l.run_id = r.run_id
  LEFT JOIN vendors v ON v.vendor_id = l.vendor_id
  LEFT JOIN extractions e ON e.run_id = r.run_id
"""


def list_runs(conn: sqlite3.Connection, status: str | None = None, reason_code: str | None = None,
              po_number: str | None = None, limit: int | None = None) -> list[dict]:
    rows = [_run_row(r) for r in conn.execute(RUNS_SQL + " ORDER BY r.run_number DESC")]
    if status:
        rows = [r for r in rows if r["status"] == status.upper()]
    if reason_code:
        rows = [r for r in rows if reason_code.upper() in r["reason_codes"]]
    if po_number:
        rows = [r for r in rows if r["matched_po"] == po_number]
    return rows[:limit] if limit else rows


# ------------------------------------------------------------------ run detail (FR-110, PRD §10.5)

def _money_or_none(p):
    return money(p) if isinstance(p, int) else None


def _evidence(evidence: list[dict], check: str) -> dict | None:
    return next((e for e in evidence if e["check"] == check), None)


def run_detail(conn: sqlite3.Connection, run_id: str) -> dict | None:
    """Everything the decision details screen shows. Account numbers are masked (FR-041), IFSC is
    shown in full (DEC-001), and the trusted contact's phone/email appear only for a run whose
    decision includes BANK_DETAILS_MISMATCH (FR-044)."""
    from app.services.events import queue_position
    from app.utils.masking import mask_account_number

    run = conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
    if run is None:
        return None
    body = {
        "run_id": run_id, "run_number": run["run_number"], "run_display": display_number(run["run_number"]),
        "file_name": run["file_name"], "status": run["status"], "ingest_method": run["ingest_method"],
        "stage_results": json.loads(run["stage_results"]), "error_message": run["error_message"],
        "created_at": run["created_at"], "started_at": run["started_at"],
        "completed_at": run["completed_at"], "duration_ms": run["duration_ms"],
        "queue_position": queue_position(conn, run["run_number"]) if run["status"] == "QUEUED" else None,
        "extraction": None, "decision": None, "invoice": None, "vendor": None,
    }
    payload = {}
    ext = conn.execute("SELECT * FROM extractions WHERE run_id = ?", (run_id,)).fetchone()
    if ext:
        payload = json.loads(ext["payload"])
        acct = payload.get("bank_account_number") or {}
        if acct.get("value"):
            acct["value"] = mask_account_number(acct["value"])
        body["extraction"] = {"provider": ext["provider"], "model": ext["model"], "cached": bool(ext["cached"]),
                              "ingest_method": ext["ingest_method"], "extracted_at": ext["extracted_at"],
                              "fields": payload}
    led = conn.execute("SELECT * FROM invoice_ledger WHERE run_id = ?", (run_id,)).fetchone()
    val = lambda k: (payload.get(k) or {}).get("value")       # noqa: E731
    body["invoice"] = {
        "invoice_number": led["invoice_number_raw"] if led else val("invoice_number"),
        "invoice_number_normalised": led["invoice_number_normalised"] if led else None,
        "invoice_date": led["invoice_date"] if led else val("invoice_date"),
        "vendor_name": val("vendor_name"), "vendor_gstin": val("vendor_gstin"),
        "total": _money_or_none(led["total"] if led else paise(val("total"))),
        "taxable_value": _money_or_none(led["taxable_value"]) if led else None,
        "line_signature": json.loads(led["line_signature"]) if led else None,
    } if (led or payload) else None

    dec = conn.execute("SELECT * FROM decisions WHERE run_id = ?", (run_id,)).fetchone()
    if not dec:
        return body
    codes = json.loads(dec["reason_codes"])
    evidence = json.loads(dec["evidence"])
    body["decision"] = {
        "status": dec["status"], "reason_codes": codes, "summary": json.loads(dec["summary"]),
        "next_actions": json.loads(dec["next_actions"]), "notes": json.loads(dec["notes"]),
        "evidence": evidence, "matched_po": dec["matched_po"], "po_candidates": json.loads(dec["po_candidates"]),
        "decided_by": dec["decided_by"], "policy_version": dec["policy_version"],
        "committed": bool(dec["committed"]), "committed_at": dec["committed_at"],
        "created_at": dec["created_at"], "updated_at": dec["updated_at"],
        "duplicate_matches": _duplicate_matches(conn, json.loads(dec["duplicate_matches"])),
        "po_detail": _po_detail_view(json.loads(dec["po_detail"])) if dec["po_detail"] else None,
    }

    # Vendor & bank: invoice against vendor master (§10.5). No trusted-contact fields here.
    ident = _evidence(evidence, "vendor_identified")
    vendor_id = led["vendor_id"] if led else (ident["expected"] if ident and ident["result"] == "PASSED" else None)
    master = conn.execute("SELECT * FROM vendors WHERE vendor_id = ?", (vendor_id,)).fetchone() if vendor_id else None
    gfmt, gmatch, bank = (_evidence(evidence, c) for c in ("gstin_format", "gstin_matches_master", "bank_details_match"))
    inv_acct = val("bank_account_number")
    body["vendor"] = {
        "identified": master is not None,
        "identification_note": None if master else (ident["variance"] if ident else None),
        "invoice": {"legal_name": val("vendor_name"), "gstin": val("vendor_gstin"),
                    "state_code": (val("vendor_gstin") or "")[:2] or None,
                    "bank_account_masked": inv_acct, "ifsc": val("ifsc")},
        "master": {"vendor_id": master["vendor_id"], "legal_name": master["legal_name"], "gstin": master["gstin"],
                   "state_code": master["state_code"], "state_name": master["state_name"], "status": master["status"],
                   "bank_account_masked": mask_account_number(master["bank_account_number"]),
                   "ifsc": master["ifsc"]} if master else None,
        "labels": {"gstin_format": gfmt["variance"] if gfmt else None,
                   "gstin_master": gmatch["variance"] if gmatch else None,
                   "bank": (bank["variance"] if bank and bank["result"] != "NOT_CHECKED"
                            else (bank["variance"] or "Not checked") if bank else None),
                   "bank_result": bank["result"] if bank else None},
    }
    if "BANK_DETAILS_MISMATCH" in codes and master:
        body["trusted_contact"] = {"name": master["trusted_contact_name"], "phone": master["trusted_contact_phone"],
                                   "email": master["trusted_contact_email"], "source": "vendor master"}
    return body


def _duplicate_matches(conn: sqlite3.Connection, matches: list[dict]) -> list[dict]:
    out = []
    for m in matches:
        row = conn.execute("SELECT * FROM invoice_ledger WHERE ledger_id = ?", (m["ledger_id"],)).fetchone()
        if row is None:
            continue
        out.append({"tier": m["tier"], "ledger_id": row["ledger_id"], "source": row["source"], "run_id": row["run_id"],
                    "run_display": (display_number(conn.execute("SELECT run_number FROM runs WHERE run_id = ?",
                                                                (row["run_id"],)).fetchone()[0]) if row["run_id"] else None),
                    "invoice_number_raw": row["invoice_number_raw"],
                    "invoice_number_normalised": row["invoice_number_normalised"],
                    "invoice_date": row["invoice_date"], "total": money(row["total"]), "status": row["status"],
                    "po_number": row["po_number"], "lines": json.loads(row["line_signature"])})
    return out


def _po_detail_view(d: dict) -> dict:
    out = {**d}
    for k in ("po_total", "previously_approved", "this_invoice", "remaining", "allowed_overrun", "ceiling"):
        out[k] = _money_or_none(d.get(k))
    lines = []
    for line in d["lines"]:
        line = {**line}
        if line.get("unit_price"):
            up = line["unit_price"]
            line["unit_price"] = {**up, "po": money(up["po"]), "invoice": money(up["invoice"])}
        lines.append(line)
    out["lines"] = lines
    return out


# ------------------------------------------------------------------ audit (FR-112, FR-102)

def run_audit(conn: sqlite3.Connection, run_id: str) -> dict | None:
    if conn.execute("SELECT 1 FROM runs WHERE run_id = ?", (run_id,)).fetchone() is None:
        return None
    events = [{"event_id": r["event_id"], "type": r["type"], "payload": json.loads(r["payload"]),
               "created_at": r["created_at"]}
              for r in conn.execute("SELECT * FROM audit_events WHERE run_id = ? ORDER BY event_id", (run_id,))]
    overrides = []
    for r in conn.execute("SELECT * FROM overrides WHERE run_id = ? ORDER BY created_at, override_id", (run_id,)):
        o = dict(r)
        o["codes_overridden"] = json.loads(o["codes_overridden"])
        o["bank_verification"] = json.loads(o["bank_verification"]) if o["bank_verification"] else None
        overrides.append(o)
    return {"run_id": run_id, "events": events, "overrides": overrides}


# ------------------------------------------------------------------ dashboard (FR-111, FR-115)

def dashboard(conn: sqlite3.Connection, policy: Policy) -> dict:
    runs = list_runs(conn)
    decided = [r for r in runs if r["run_status"] == "DECIDED"]
    by_status = {s: sum(1 for r in decided if r["status"] == s) for s in ("APPROVE", "HOLD", "REJECT")}
    by_status |= {s: sum(1 for r in runs if r["run_status"] == s) for s in ("ERROR", "QUEUED", "PROCESSING")}
    auto = sum(1 for r in decided if r["status"] == "APPROVE" and r["decided_by"] == "rules")
    rate = (auto * 100 / len(decided)) if decided else 0.0
    on_hold = sum(r["total"]["paise"] for r in decided if r["status"] == "HOLD" and r["total"])
    exceptions: dict[str, int] = {}
    for r in decided:
        for c in r["reason_codes"]:
            exceptions[c] = exceptions.get(c, 0) + 1
    durations = [r["duration_ms"] for r in decided if r["duration_ms"] is not None]
    return {
        "label": DEMO_LABEL,
        "invoices_processed": len(decided),
        "by_status": by_status,
        "auto_approval_rate": {"value": round(rate, 1), "display": f"{rate:.1f}%",
                               "numerator": auto, "denominator": len(decided)},
        "value_on_hold": money(on_hold),
        "exceptions_by_reason": [{"code": c, "count": n, "severity": REGISTRY[c].severity.value}
                                 for c, n in sorted(exceptions.items(), key=lambda t: (-t[1], t[0]))],
        "avg_processing_ms": round(sum(durations) / len(durations)) if durations else None,
        "recent_runs": runs[:10],
        "po_balances": po_balances(conn, policy),
    }


# ------------------------------------------------------------------ policy (FR-117)

def policy_view(policy: Policy) -> dict:
    data = policy.data
    return {
        "policy_version": policy.version,
        "policy_version_short": policy.version_short,
        "assumptions": [{"id": k, **v} for k, v in data["assumptions"].items()],
        "other_assumptions": [{"name": k, **v} for k, v in (data.get("other_assumptions") or {}).items()],
        "p1_rules": data.get("p1_rules", {}),
        "reason_codes": [{"code": rc.code, "severity": rc.severity.value, "stage": rc.stage,
                          "stage_key": rc.stage_key, "stage_status": rc.stage_status.value,
                          "template": rc.template, "next_action": rc.next_action, "priority": rc.priority}
                         for rc in REGISTRY.values()],
        "does_not_verify": DOES_NOT_VERIFY,
        "scope": {"matching": "Two-way match", "currency": data["assumptions"]["A-01"]["value"],
                  "tax_regime": data["assumptions"]["A-02"]["value"]["regime"],
                  "processing": data["assumptions"]["A-22"]["value"]},
    }
