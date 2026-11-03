"""Human review: override (FR-100..FR-106, OD-5, OD-6) and confirm PO (FR-103).

Every action runs in one BEGIN IMMEDIATE transaction that first re-reads the decision, so two
concurrent requests cannot both act on the same HOLD. An approve override goes through
commit_approval, which rechecks quantity and value and may block it (FR-091, FR-105). Every
action writes an override record (§8.8) and an 'override' audit event.
"""
import json
import sqlite3
import uuid
from dataclasses import dataclass

from app.policy import Policy
from app.rules.engine import evaluate
from app.rules.reason_codes import REGISTRY, Severity
from app.services import audit
from app.services.commit import commit_in_transaction
from app.services.decision import today_ist
from app.services.extraction.schema import InvoiceExtraction
from app.services.refdata import load_reference
from app.utils.timeutil import now_iso

BANK = "BANK_DETAILS_MISMATCH"
CHANNELS = ("phone on record", "email on record")
OUTCOMES = ("verified", "not verified")


class ReviewError(Exception):
    def __init__(self, code: str, message: str, http_status: int = 422):
        super().__init__(message)
        self.code, self.message, self.http_status = code, message, http_status


@dataclass(frozen=True)
class ReviewResult:
    run_id: str
    override_id: str
    action: str
    status: str               # decision status after the action (and any commit attempt)
    commit_result: str        # committed | blocked | not_applicable
    message: str
    reason_codes: list[str]

    def as_dict(self) -> dict:
        return dict(self.__dict__)


# ------------------------------------------------------------------ shared checks

def _reviewer(name, reason, policy: Policy) -> tuple[str, str]:
    name = (name or "").strip()
    reason = (reason or "").strip()
    if not name:
        raise ReviewError("reviewer_name_required", "Reviewer name is required")
    minimum = policy.assumption("A-29")
    if len(reason) < minimum:
        raise ReviewError("reason_too_short", f"Reason must be at least {minimum} characters")
    return name, reason


def _hold_decision(conn: sqlite3.Connection, run_id: str, policy: Policy):
    run = conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
    if run is None:
        raise ReviewError("run_not_found", "Run not found", 404)
    d = conn.execute("SELECT * FROM decisions WHERE run_id = ?", (run_id,)).fetchone()
    if d is None:
        raise ReviewError("not_overridable", f"Run has no decision (status {run['status']})", 409)
    if d["status"] not in policy.assumption("A-31"):         # only HOLD is overridable (OD-6)
        raise ReviewError("not_overridable", f"{d['status']} decisions are final and cannot be overridden", 409)
    return run, d


def _record(conn, run_id, action, original, new, codes, reviewer, reason, bank, confirmed_po, commit_result,
            block_reason) -> str:
    override_id = "O-" + uuid.uuid4().hex[:12]
    conn.execute(
        "INSERT INTO overrides (override_id, run_id, action, original_status, new_status, codes_overridden, "
        "reviewer_name, reason, bank_verification, confirmed_po, commit_result, block_reason, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (override_id, run_id, action, original, new, json.dumps(codes), reviewer, reason,
         json.dumps(bank) if bank else None, confirmed_po, commit_result, block_reason, now_iso()))
    audit.add(conn, run_id, "override", {
        "override_id": override_id, "action": action, "original_status": original, "new_status": new,
        "codes_overridden": codes, "reviewer_name": reviewer, "bank_verification": bank,
        "confirmed_po": confirmed_po, "commit_result": commit_result, "block_reason": block_reason})
    return override_id


def _blocked_message(commit_message: str) -> str:
    return commit_message.replace("Commit blocked", "Override blocked", 1)


# ------------------------------------------------------------------ override (FR-100..FR-106)

def override(conn: sqlite3.Connection, run_id: str, body: dict, policy: Policy) -> ReviewResult:
    action = body.get("action")
    if action not in ("approve", "reject"):
        raise ReviewError("invalid_action", "action must be 'approve' or 'reject'")
    reviewer, reason = _reviewer(body.get("reviewer_name"), body.get("reason"), policy)
    acknowledged = body.get("codes_overridden")
    if acknowledged is not None and (not isinstance(acknowledged, list) or not all(isinstance(c, str) for c in acknowledged)):
        raise ReviewError("invalid_codes", "codes_overridden must be a list of reason codes")

    conn.execute("BEGIN IMMEDIATE")
    try:
        run, d = _hold_decision(conn, run_id, policy)
        codes = json.loads(d["reason_codes"])
        hold_codes = [c for c in codes if REGISTRY[c].severity == Severity.HOLD]
        if action == "approve":
            missing = [c for c in hold_codes if c not in (acknowledged or [])]
            if missing:
                raise ReviewError("codes_not_acknowledged",
                                  f"Every HOLD code must be acknowledged; missing: {', '.join(missing)}")
        unknown = [c for c in (acknowledged or []) if c not in codes]
        if unknown:
            raise ReviewError("invalid_codes", f"Not reason codes of this run: {', '.join(unknown)}")
        acknowledged = acknowledged if acknowledged is not None else hold_codes

        bank = None
        if action == "approve" and BANK in codes:          # FR-045 / FR-104
            bv = body.get("bank_verification") or {}
            bank = {"verified_with": (bv.get("verified_with") or "").strip(), "channel": bv.get("channel"),
                    "outcome": bv.get("outcome")}
            if not bank["verified_with"] or bank["channel"] not in CHANNELS or bank["outcome"] not in OUTCOMES:
                raise ReviewError("bank_verification_required",
                                  "Bank details differ from the vendor master: verified with, channel "
                                  f"({' / '.join(CHANNELS)}) and outcome ({' / '.join(OUTCOMES)}) are required")

        if action == "reject":
            now = now_iso()
            conn.execute("UPDATE decisions SET status = 'REJECT', decided_by = 'reviewer', updated_at = ? WHERE run_id = ?",
                         (now, run_id))
            conn.execute("UPDATE invoice_ledger SET status = 'REJECT' WHERE run_id = ?", (run_id,))
            oid = _record(conn, run_id, "reject", "HOLD", "REJECT", acknowledged, reviewer, reason, None, None,
                          "not_applicable", None)
            conn.execute("COMMIT")
            return ReviewResult(run_id, oid, "reject", "REJECT", "not_applicable", "Rejected by reviewer", codes)

        if bank and bank["outcome"] != "verified":
            # OD-5: the attempt is recorded; the invoice stays on HOLD; nothing is committed.
            oid = _record(conn, run_id, "approve", "HOLD", "HOLD", acknowledged, reviewer, reason, bank, None,
                          "not_applicable", "Bank verification not confirmed")
            conn.execute("COMMIT")
            raise ReviewError("bank_not_verified",
                              "Bank verification not confirmed: invoice remains on HOLD (attempt recorded as " + oid + ")")

        if not d["commit_plan"]:
            raise ReviewError("nothing_to_commit",
                              "This invoice has no purchase order lines to commit against "
                              "(no explicit or confirmed PO, or an unmatched line). Confirm a PO or reject it.", 409)

        # Approve: through commit_approval, atomically with the override record (FR-105).
        now = now_iso()
        conn.execute("UPDATE decisions SET status = 'APPROVE', decided_by = 'reviewer', updated_at = ? WHERE run_id = ?",
                     (now, run_id))
        commit = commit_in_transaction(conn, run_id, policy)
        if commit.outcome == "committed":
            new, result, message, block = "APPROVE", "committed", commit.message.replace("Committed", "Approved and committed", 1), None
        else:
            new, result, message = "HOLD", "blocked", _blocked_message(commit.message)
            block = message
        oid = _record(conn, run_id, "approve", "HOLD", new, acknowledged, reviewer, reason, bank, None, result, block)
        final_codes = json.loads(conn.execute("SELECT reason_codes FROM decisions WHERE run_id = ?",
                                              (run_id,)).fetchone()[0])
        conn.execute("COMMIT")
        return ReviewResult(run_id, oid, "approve", new, result, message, final_codes)
    except Exception:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise


# ------------------------------------------------------------------ confirm PO (FR-103)

def confirm_po(conn: sqlite3.Connection, run_id: str, body: dict, policy: Policy) -> ReviewResult:
    reviewer, reason = _reviewer(body.get("reviewer_name"), body.get("reason"), policy)
    po_number = (body.get("po_number") or "").strip()
    if not po_number:
        raise ReviewError("po_number_required", "po_number is required")

    conn.execute("BEGIN IMMEDIATE")
    try:
        run, d = _hold_decision(conn, run_id, policy)
        codes = json.loads(d["reason_codes"])
        candidates = [c["po_number"] for c in json.loads(d["po_candidates"])]
        po_codes = [c for c in codes if c in ("PO_INFERRED", "PO_NOT_FOUND")]
        if not po_codes or not candidates:
            raise ReviewError("confirm_po_not_applicable",
                              "Confirm PO applies only to a run with PO_INFERRED, or PO_NOT_FOUND with suggestions", 409)
        if po_number not in candidates:
            raise ReviewError("po_not_a_candidate", f"{po_number} was not among the suggested POs: {', '.join(candidates)}")

        ext = conn.execute("SELECT payload FROM extractions WHERE run_id = ?", (run_id,)).fetchone()
        x = InvoiceExtraction.model_validate_json(ext["payload"])
        fresh = evaluate(x, load_reference(conn), policy, today_ist(), exclude_run_id=run_id, confirmed_po=po_number)

        # Re-run stages 6 and 7 only (FR-103): keep the original stage 3-5 codes, sentences and
        # evidence; replace stage 6 with the confirmed-PO result; decide again.
        old_codes = json.loads(d["reason_codes"])
        old_summary, old_actions = json.loads(d["summary"]), json.loads(d["next_actions"])
        kept = [(c, s, a) for c, s, a in zip(old_codes, old_summary, old_actions) if REGISTRY[c].stage != 6]
        new6 = [(c, s, a) for c, s, a in zip(fresh.reason_codes, fresh.summary, fresh.next_actions)
                if REGISTRY[c].stage == 6]
        combined = sorted(kept + new6, key=lambda t: (REGISTRY[t[0]].severity != Severity.REJECT, REGISTRY[t[0]].stage))
        severities = {REGISTRY[c].severity for c, _, _ in combined}
        status = "REJECT" if Severity.REJECT in severities else "HOLD" if Severity.HOLD in severities else "APPROVE"

        old_evidence = [e for e in json.loads(d["evidence"]) if e["stage"] != "PO_MATCH"]
        new_evidence = [e for e in fresh.evidence if e["stage"] == "PO_MATCH"]
        evidence = old_evidence + new_evidence
        old_notes = json.loads(d["notes"])
        notes = [n for n in old_notes if n.startswith("Similar invoice")] + fresh.po_notes

        stage6 = next(s for s in fresh.stages if s.stage == "PO_MATCH")
        decide_text = status if not combined else f"{status}: {', '.join(c for c, _, _ in combined)}"
        now = now_iso()
        stages = json.loads(run["stage_results"])
        by_stage = {}
        for i, e in enumerate(evidence):
            by_stage.setdefault(e["stage"], []).append(i)
        for s in stages:
            if s["stage"] in by_stage:
                s["evidence_ids"] = by_stage[s["stage"]]
            if s["stage"] == "PO_MATCH":
                s.update(status=stage6.status, summary=stage6.summary, started_at=now, ended_at=now,
                         evidence_ids=by_stage.get("PO_MATCH", []))
                s.pop("not_checked_reason", None)
            if s["stage"] == "DECIDE":
                s.update(status="PASSED", summary=decide_text, started_at=now, ended_at=now)
        conn.execute("UPDATE runs SET stage_results = ? WHERE run_id = ?", (json.dumps(stages), run_id))
        for st, summary, status_ in (("PO_MATCH", stage6.summary, stage6.status), ("DECIDE", decide_text, "PASSED")):
            audit.add(conn, run_id, "stage_started", {"stage": st, "after_review": True})
            audit.add(conn, run_id, "stage_completed", {"stage": st, "status": status_, "summary": summary,
                                                        "after_review": True})

        conn.execute(
            "UPDATE decisions SET status = ?, reason_codes = ?, summary = ?, next_actions = ?, evidence = ?, "
            "notes = ?, matched_po = ?, decided_by = 'rules_after_review', commit_plan = ?, updated_at = ? "
            "WHERE run_id = ?",
            (status, json.dumps([c for c, _, _ in combined]), json.dumps([s for _, s, _ in combined]),
             json.dumps([a for _, _, a in combined]), json.dumps(evidence), json.dumps(notes), po_number,
             json.dumps(fresh.commit_plan) if fresh.commit_plan else None, now, run_id))
        if fresh.ledger:
            conn.execute("UPDATE invoice_ledger SET po_number = ?, line_signature = ?, status = ? WHERE run_id = ?",
                         (po_number, json.dumps(fresh.ledger["line_signature"]), status, run_id))
        audit.add(conn, run_id, "decision", {"status": status, "reason_codes": [c for c, _, _ in combined],
                                             "policy_version": policy.version, "decided_by": "rules_after_review"})

        remaining_codes = ", ".join(c for c, _, _ in combined)
        commit_result, block, new_status = "not_applicable", None, status
        if status == "HOLD":
            message = f"Still on HOLD: {remaining_codes} remains after PO confirmation"
        elif status == "REJECT":
            message = f"REJECT: {remaining_codes}"
        else:
            message = "APPROVE"
        if status == "APPROVE":
            commit = commit_in_transaction(conn, run_id, policy)
            if commit.outcome == "committed":
                commit_result, message = "committed", commit.message.replace("Committed", "Approved and committed", 1)
            else:
                commit_result, new_status = "blocked", "HOLD"
                message = block = _blocked_message(commit.message)
        oid = _record(conn, run_id, "confirm_po", "HOLD", new_status, po_codes, reviewer, reason, None, po_number,
                      commit_result, block)
        final_codes = json.loads(conn.execute("SELECT reason_codes FROM decisions WHERE run_id = ?",
                                              (run_id,)).fetchone()[0])
        conn.execute("COMMIT")
        return ReviewResult(run_id, oid, "confirm_po", new_status, commit_result, message, final_codes)
    except Exception:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
