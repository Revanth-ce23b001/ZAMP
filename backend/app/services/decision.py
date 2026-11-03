"""Run stages 3-7 for a run and persist the outcome (decision, ledger row, stage results, audit),
then commit_approval for an APPROVE. The rules themselves are pure (app.rules.engine)."""
import json
import sqlite3
import uuid
from datetime import date, datetime

from app.policy import Policy
from app.rules.engine import EngineResult, evaluate
from app.services import audit
from app.services.commit import CommitResult, commit_approval
from app.services.extraction.schema import InvoiceExtraction
from app.services.refdata import load_reference
from app.utils.timeutil import IST, now_iso


def today_ist() -> date:
    return datetime.now(IST).date()


def _stage_dict(s, started: str, ended: str) -> dict:
    d = {"stage": s.stage, "status": s.status, "summary": s.summary, "evidence_ids": s.evidence_ids,
         "started_at": started, "ended_at": ended}
    if s.not_checked_reason:
        d["not_checked_reason"] = s.not_checked_reason
    return d


def decide_run(conn: sqlite3.Connection, run_id: str, extraction: InvoiceExtraction, policy: Policy,
               prior_stages: list[dict], today: date | None = None) -> tuple[EngineResult, CommitResult | None]:
    run = conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
    ref = load_reference(conn)
    started = now_iso()
    result = evaluate(extraction, ref, policy, today or today_ist(), exclude_run_id=run_id)
    ended = now_iso()
    stages = prior_stages + [_stage_dict(s, started, ended) for s in result.stages]

    conn.execute("BEGIN IMMEDIATE")
    try:
        for s in result.stages:
            audit.add(conn, run_id, "stage_started", {"stage": s.stage})
            audit.add(conn, run_id, "stage_completed", {"stage": s.stage, "status": s.status, "summary": s.summary,
                                                        **({"not_checked_reason": s.not_checked_reason}
                                                           if s.not_checked_reason else {})})
        now = now_iso()
        conn.execute("DELETE FROM decisions WHERE run_id = ?", (run_id,))
        conn.execute(
            "INSERT INTO decisions (decision_id, run_id, status, reason_codes, evidence, notes, summary, next_actions, "
            "matched_po, po_candidates, decided_by, policy_version, committed, committed_at, created_at, updated_at, "
            "commit_plan, duplicate_matches, po_detail) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'rules', ?, 0, NULL, ?, ?, ?, ?, ?)",
            ("D-" + uuid.uuid4().hex[:12], run_id, result.status, json.dumps(result.reason_codes),
             json.dumps(result.evidence), json.dumps(result.notes), json.dumps(result.summary),
             json.dumps(result.next_actions), result.matched_po, json.dumps(result.po_candidates),
             policy.version, now, now, json.dumps(result.commit_plan) if result.commit_plan else None,
             json.dumps(result.duplicate_matches), json.dumps(result.po_detail) if result.po_detail else None))
        if result.ledger is not None:
            # One ledger row per decided invoice (§8.5). An APPROVE row is confirmed by commit.
            conn.execute("DELETE FROM invoice_ledger WHERE run_id = ?", (run_id,))
            row = {**result.ledger, "ledger_id": "L-" + uuid.uuid4().hex[:12], "run_id": run_id, "source": "run",
                   "status": result.status, "duplicate_of": result.duplicate_of,
                   "line_signature": json.dumps(result.ledger["line_signature"])}
            cols = ", ".join(row)
            conn.execute(f"INSERT INTO invoice_ledger ({cols}) VALUES ({', '.join('?' * len(row))})", list(row.values()))
        duration = None
        if run["created_at"]:
            duration = int((datetime.fromisoformat(now) - datetime.fromisoformat(run["created_at"])).total_seconds() * 1000)
        conn.execute("UPDATE runs SET status = 'DECIDED', stage_results = ?, completed_at = ?, duration_ms = ? "
                     "WHERE run_id = ?", (json.dumps(stages), now, max(duration or 0, 0), run_id))
        audit.add(conn, run_id, "decision", {"status": result.status, "reason_codes": result.reason_codes,
                                             "policy_version": policy.version})
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise

    commit = commit_approval(conn, run_id, policy) if result.status == "APPROVE" else None
    return result, commit
