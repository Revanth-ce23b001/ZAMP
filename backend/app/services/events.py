"""Server-sent events for a run (FR-114, NFR-08).

The stream is derived entirely from the stored audit_events (FR-112), never from an in-memory
buffer, so replay survives a server restart. Each SSE message carries the audit event_id as its
`id:`, so a reconnecting browser (which sends Last-Event-ID) resumes exactly where it stopped:
no event is lost and none is repeated. Payloads are the audit payloads, which are already masked.
"""
import asyncio
import json
import sqlite3
import time
from collections.abc import AsyncIterator
from pathlib import Path

from app.db.connection import connect
from app.utils.money import format_inr

POLL_SECONDS = 0.2

# audit type -> SSE event name (FR-114). Other audit types (run_created, extraction_*) are not streamed.
STREAMED = {
    "stage_started": "stage_started",
    "stage_completed": "stage_completed",
    "decision": "decision",
    "commit": "commit",
    "commit_blocked": "commit",
    "error": "error",
    "retry": "retry",
}


def _frame(event: str, data: dict, event_id: int | None = None) -> str:
    head = f"id: {event_id}\n" if event_id is not None else ""
    return f"{head}event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _sse_payload(audit_type: str, payload: dict, at: str) -> dict:
    data = {**payload, "at": at}
    if audit_type in ("commit", "commit_blocked"):
        data["outcome"] = "committed" if audit_type == "commit" else "blocked"
        if isinstance(data.get("remaining"), int):
            data["remaining_display"] = format_inr(data["remaining"])
        data.pop("lines", None)
    return data


def queue_position(conn: sqlite3.Connection, run_number: int) -> int:
    """Runs ahead of this one: QUEUED runs submitted earlier plus any run being processed."""
    ahead = conn.execute("SELECT COUNT(*) FROM runs WHERE status = 'QUEUED' AND run_number < ?",
                         (run_number,)).fetchone()[0]
    busy = conn.execute("SELECT COUNT(*) FROM runs WHERE status = 'PROCESSING'").fetchone()[0]
    return ahead + busy


def _finished(sent: list[str], decision_status: str | None) -> bool:
    """Close after an error, or after the decision plus (for an APPROVE) its commit outcome."""
    last_retry = max((i for i, t in enumerate(sent) if t == "retry"), default=-1)
    tail = sent[last_retry + 1:]
    if "error" in tail:
        return True
    if "decision" not in tail:
        return False
    if decision_status == "APPROVE":
        return "commit" in tail or "commit_blocked" in tail
    return True


async def run_events(db_path: Path, run_id: str, last_event_id: int | None, is_disconnected,
                     keepalive_seconds: float = 15.0) -> AsyncIterator[str]:
    conn = connect(db_path)
    try:
        run = conn.execute("SELECT run_number, status, file_name FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        if run is None:
            yield _frame("error", {"message": "Run not found"})
            return
        yield _frame("run", {"run_id": run_id, "run_number": run["run_number"], "status": run["status"],
                             "file_name": run["file_name"]})
        yield "retry: 3000\n\n"                   # browser reconnect delay

        cursor = last_event_id or 0
        sent: list[str] = [r[0] for r in conn.execute(
            "SELECT type FROM audit_events WHERE run_id = ? AND event_id <= ? ORDER BY event_id", (run_id, cursor))]
        last_ping = time.monotonic()
        last_position = None
        decision_status = None
        while True:
            new = conn.execute("SELECT event_id, type, payload, created_at FROM audit_events "
                               "WHERE run_id = ? AND event_id > ? ORDER BY event_id", (run_id, cursor)).fetchall()
            for r in new:
                cursor = r["event_id"]
                sent.append(r["type"])
                if r["type"] == "decision":
                    decision_status = json.loads(r["payload"]).get("status")
                if r["type"] in STREAMED:
                    yield _frame(STREAMED[r["type"]], _sse_payload(r["type"], json.loads(r["payload"]),
                                                                   r["created_at"]), r["event_id"])
            if decision_status is None:
                d = conn.execute("SELECT payload FROM audit_events WHERE run_id = ? AND type = 'decision' "
                                 "ORDER BY event_id DESC LIMIT 1", (run_id,)).fetchone()
                decision_status = json.loads(d[0]).get("status") if d else None
            if _finished(sent, decision_status):
                yield _frame("end", {"run_id": run_id})
                return

            status = conn.execute("SELECT status FROM runs WHERE run_id = ?", (run_id,)).fetchone()
            if status and status[0] == "QUEUED":
                pos = queue_position(conn, run["run_number"])
                if pos != last_position:
                    last_position = pos
                    yield _frame("queued", {"position": pos})
            if time.monotonic() - last_ping >= keepalive_seconds:
                last_ping = time.monotonic()
                yield ": keepalive\n\n"
            if await is_disconnected():
                return
            await asyncio.sleep(POLL_SECONDS)
    finally:
        conn.close()
