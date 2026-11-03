"""commit_approval(run_id) (FR-090..FR-094, §9.9, §12.6).

The only writer of po_consumption at runtime. One BEGIN IMMEDIATE transaction:
  1. decision already committed -> no-op (idempotent);
  2. recompute committed quantity per PO line and value per PO from po_consumption;
  3. recheck this invoice's paired lines (the decision's commit plan) against §9.3;
  4. pass -> insert consumption rows, decision committed, ledger APPROVE, audit 'commit';
     fail -> write nothing, decision becomes HOLD with the failing codes, ledger HOLD,
             audit 'commit_blocked'.
"""
import json
import sqlite3
from dataclasses import dataclass

from app.policy import Policy
from app.rules.common import Rules, inr, qty, rs
from app.rules.reason_codes import REGISTRY
from app.services import audit
from app.utils.timeutil import now_iso


@dataclass(frozen=True)
class CommitResult:
    outcome: str              # "committed" | "already_committed" | "blocked" | "not_applicable"
    codes: tuple[str, ...] = ()
    message: str = ""


def commit_approval(conn: sqlite3.Connection, run_id: str, policy: Policy) -> CommitResult:
    rules = Rules(policy)
    conn.execute("BEGIN IMMEDIATE")
    try:
        result = _commit(conn, run_id, rules)
        conn.execute("COMMIT")
        return result
    except Exception:
        conn.execute("ROLLBACK")
        raise


def commit_in_transaction(conn: sqlite3.Connection, run_id: str, policy: Policy) -> CommitResult:
    """The same commit, for a caller that already holds a BEGIN IMMEDIATE transaction (an override
    or a PO confirmation commits atomically with its own record)."""
    return _commit(conn, run_id, Rules(policy))


def _commit(conn: sqlite3.Connection, run_id: str, rules: Rules) -> CommitResult:
    d = conn.execute("SELECT * FROM decisions WHERE run_id = ?", (run_id,)).fetchone()
    if d is None:
        return CommitResult("not_applicable", message="no decision")
    if d["committed"]:
        return CommitResult("already_committed", message="already committed")
    if d["status"] != "APPROVE" or not d["commit_plan"]:
        return CommitResult("not_applicable", message=f"decision is {d['status']}")
    plan = json.loads(d["commit_plan"])
    ledger = conn.execute("SELECT ledger_id FROM invoice_ledger WHERE run_id = ?", (run_id,)).fetchone()
    if ledger is None:
        return CommitResult("not_applicable", message="no ledger row")
    po = conn.execute("SELECT po_number, po_total FROM purchase_orders WHERE po_number = ?",
                      (plan["po_number"],)).fetchone()

    # Recheck against committed consumption as of now (§9.9).
    failing, details = [], []
    for line in plan["lines"]:
        pl = conn.execute("SELECT item_code, quantity FROM po_lines WHERE po_line_id = ?",
                          (line["po_line_id"],)).fetchone()
        prev = conn.execute("SELECT COALESCE(SUM(quantity), 0) FROM po_consumption WHERE po_line_id = ?",
                            (line["po_line_id"],)).fetchone()[0]
        excess = float(prev) + float(line["quantity"]) - (float(pl["quantity"]) + rules.qty_overrun)
        if excess > 0:
            failing.append(("QUANTITY_EXCEEDS_PO", {"item": pl["item_code"], "prev": qty(prev),
                                                    "this": qty(line["quantity"]), "po_qty": qty(pl["quantity"]),
                                                    "excess": qty(excess)}))
    committed_value = conn.execute(
        "SELECT COALESCE(SUM(c.value), 0) FROM po_consumption c JOIN po_lines l ON l.po_line_id = c.po_line_id "
        "WHERE l.po_number = ?", (po["po_number"],)).fetchone()[0]
    allow = rules.allowed_overrun(po["po_total"])
    taxable = plan["taxable_value"]
    if committed_value + taxable > po["po_total"] + allow:
        failing.append(("PO_BALANCE_EXCEEDED", {"this": inr(taxable), "remaining": inr(po["po_total"] - committed_value),
                                                "allow": inr(allow), "po": po["po_number"]}))
    now = now_iso()

    if failing:
        codes = json.loads(d["reason_codes"])
        summary, actions = json.loads(d["summary"]), json.loads(d["next_actions"])
        for code, params in failing:
            if code not in codes:
                codes.append(code)
                summary.append(REGISTRY[code].template.format(**params))
                actions.append(REGISTRY[code].next_action)
        message = (f"Commit blocked: {po['po_number']} remaining {rs(po['po_total'] - committed_value)} "
                   f"is less than this invoice ({rs(taxable)})" if any(c == "PO_BALANCE_EXCEEDED" for c, _ in failing)
                   else "Commit blocked: quantity exceeds PO")
        conn.execute("UPDATE decisions SET status = 'HOLD', reason_codes = ?, summary = ?, next_actions = ?, "
                     "updated_at = ? WHERE run_id = ?",
                     (json.dumps(codes), json.dumps(summary), json.dumps(actions), now, run_id))
        conn.execute("UPDATE invoice_ledger SET status = 'HOLD' WHERE run_id = ?", (run_id,))
        audit.add(conn, run_id, "commit_blocked", {"codes": [c for c, _ in failing], "message": message,
                                                   "po_number": po["po_number"],
                                                   "remaining": po["po_total"] - committed_value})
        return CommitResult("blocked", tuple(c for c, _ in failing), message)

    for line in plan["lines"]:
        conn.execute("INSERT INTO po_consumption (run_id, ledger_id, po_line_id, quantity, value, committed_at) "
                     "VALUES (?, ?, ?, ?, ?, ?)",
                     (run_id, ledger["ledger_id"], line["po_line_id"], line["quantity"], line["value"], now))
    conn.execute("UPDATE decisions SET committed = 1, committed_at = ?, updated_at = ? WHERE run_id = ?",
                 (now, now, run_id))
    conn.execute("UPDATE invoice_ledger SET status = 'APPROVE' WHERE run_id = ?", (run_id,))
    remaining = po["po_total"] - committed_value - taxable
    message = f"Committed: {po['po_number']} remaining balance {rs(remaining)}"
    audit.add(conn, run_id, "commit", {"po_number": po["po_number"], "lines": plan["lines"],
                                       "remaining": remaining, "message": message})
    return CommitResult("committed", (), message)
