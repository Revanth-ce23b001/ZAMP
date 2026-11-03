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
