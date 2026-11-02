"""Append-only audit events (FR-112). Payloads must already be masked and secret-free."""
import json
import sqlite3

from app.utils.timeutil import now_iso


def add(conn: sqlite3.Connection, run_id: str, event_type: str, payload: dict | None = None) -> None:
    conn.execute(
        "INSERT INTO audit_events (run_id, type, payload, created_at) VALUES (?, ?, ?, ?)",
        (run_id, event_type, json.dumps(payload or {}), now_iso()),
    )
