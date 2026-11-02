"""Demo reset (PRD FR-116, §8.8, DEC-002).

The backend is the sole enforcement point for the token, the typed confirmation and both
rate limits. Every attempt writes exactly one demo_resets row:
  * success      - inside the reset transaction;
  * rejected / rate_limited / failed - in a separate transaction, after any rollback.
The token is never written to a response, a log line or demo_resets.detail.
"""
import hmac
import json
import logging
import sqlite3
import threading
import time
from collections import defaultdict, deque
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from app.config import Settings
from app.db import seed as seed_module
from app.db.connection import connect
from app.policy import Policy
from app.utils.timeutil import now_iso

log = logging.getLogger("clearmatch.reset")

# Seed state the reset must restore (FR-116 atomicity step "verify seed counts").
EXPECTED_SEED_COUNTS = {"vendors": 7, "purchase_orders": 11, "po_lines": 15, "ledger": 5, "consumption": 5}

# Deletion order respects foreign keys (children first). demo_resets, extraction_cache and
# schema_migrations are deliberately absent: reset never touches them (A-30, §8.8).
RESET_DELETE_ORDER = (
    "audit_events", "overrides", "po_consumption", "invoice_ledger", "decisions",
    "extractions", "runs", "po_lines", "purchase_orders", "vendors",
)

MAX_LABEL_CHARS = 100
UNKNOWN_LABEL = "unknown"


@dataclass(frozen=True)
class ResetResult:
    status_code: int
    body: dict


class ResetService:
    def __init__(self, settings: Settings, policy: Policy, clock: Callable[[], float] = time.monotonic):
        limits = policy.assumption("A-33")
        self._settings = settings
        self._confirm_phrase = policy.assumption("A-34")
        self._success_interval = limits["success_min_interval_seconds"]
        self._max_rejected = limits["rejected_attempts_per_ip"]
        self._rejected_window = limits["rejected_window_seconds"]
        self._clock = clock
        # In-memory limiter state; valid because there is one backend instance (A-22).
        self._lock = threading.Lock()
        self._last_success: float | None = None
        self._rejected: dict[str, deque[float]] = defaultdict(deque)

    # ------------------------------------------------------------------ entry point

    def handle(self, client_ip: str, token: str | None, body: object) -> ResetResult:
        """Apply the checks in the order fixed by FR-116, then reset. Serialised by a lock."""
        with self._lock:
            now = self._clock()
            label = self._label(body)
            confirm = body.get("confirm") if isinstance(body, dict) else None

            # 1. Too many rejected attempts from this IP -> 429 (before the token check, so the
            #    sixth rejected attempt returns 429, not 401).
            if self._rejected_count(client_ip, now) >= self._max_rejected:
                return self._refuse(429, "rate_limited", "rate_limit_rejected_attempts", label, client_ip,
                                    "Too many rejected reset attempts from this address. Try again later.")
            # 2. Token.
            if not self._token_ok(token):
                self._rejected[client_ip].append(now)
                return self._refuse(401, "rejected", "invalid_token", label, client_ip,
                                    "Missing or invalid reset token.")
            # 3. Typed confirmation (must equal the phrase exactly).
            if confirm != self._confirm_phrase:
                self._rejected[client_ip].append(now)
                return self._refuse(400, "rejected", "confirmation_mismatch", label, client_ip,
                                    f'Confirmation must be exactly "{self._confirm_phrase}".')
            # 3b. requester_label is required (§8.8). Treated as a bad request like 3.
            if label == UNKNOWN_LABEL:
                self._rejected[client_ip].append(now)
                return self._refuse(400, "rejected", "missing_requester_label", label, client_ip,
                                    "requester_label is required.")
            # 4. At most one successful reset per interval, globally.
            if self._last_success is not None and now - self._last_success < self._success_interval:
                wait = int(self._success_interval - (now - self._last_success)) + 1
                return self._refuse(429, "rate_limited", "rate_limit_success_window", label, client_ip,
                                    f"A reset ran less than {self._success_interval} seconds ago. "
                                    f"Try again in {wait} seconds.")
            # 5. Reset.
            result = self._reset(label, client_ip)
            if result.status_code == 200:
                self._last_success = self._clock()
            return result

    # ------------------------------------------------------------------ checks

    def _token_ok(self, token: str | None) -> bool:
        expected = self._settings.demo_reset_token
        if not token or not expected:
            return False
        return hmac.compare_digest(token.encode("utf-8"), expected.encode("utf-8"))

    def _rejected_count(self, ip: str, now: float) -> int:
        q = self._rejected[ip]
        while q and now - q[0] >= self._rejected_window:
            q.popleft()
        return len(q)

    @staticmethod
    def _label(body: object) -> str:
        raw = body.get("requester_label") if isinstance(body, dict) else None
        if not isinstance(raw, str) or not raw.strip():
            return UNKNOWN_LABEL
        return raw.strip()[:MAX_LABEL_CHARS]

    # ------------------------------------------------------------------ reset

    def _reset(self, label: str, client_ip: str) -> ResetResult:
        conn = connect(self._settings.database_path)
        try:
            conn.execute("BEGIN IMMEDIATE")
            try:
                for table in RESET_DELETE_ORDER:
                    conn.execute(f"DELETE FROM {table}")
                fx = seed_module.load_fixtures()
                seed_module.insert_fixtures(conn, fx)
                seed_module.verify_seeded(conn, fx)
                restored = seed_module.table_counts(conn)
                if restored != EXPECTED_SEED_COUNTS:
                    raise RuntimeError(f"seed count check failed: {restored} != {EXPECTED_SEED_COUNTS}")
                reset_id = self._insert_row(conn, "success", label, None, {"restored": restored, "ip": client_ip})
                conn.execute("COMMIT")
            except Exception as exc:  # noqa: BLE001 - any failure must roll back and be recorded
                conn.execute("ROLLBACK")
                reason = f"{type(exc).__name__}: {exc}"[:500] or "reset failed"
                log.exception("demo reset failed and was rolled back")
                return self._refuse(500, "failed", reason, label, client_ip,
                                    "Reset failed. No data was changed.", error_code="reset_failed")
        finally:
            conn.close()

        files_deleted = self._delete_uploads()
        log.info("demo reset %s succeeded (label=%s, files_deleted=%s)", reset_id, label, files_deleted)
        return ResetResult(200, {"reset_id": reset_id, "restored": restored})

    def _delete_uploads(self) -> int:
        """Runs only after commit. Errors are logged and never undo the reset (FR-116)."""
        deleted = 0
        uploads: Path = self._settings.uploads_dir
        if not uploads.is_dir():
            return 0
        for path in uploads.iterdir():
            if path.name == ".gitkeep" or not path.is_file():
                continue
            try:
                path.unlink()
                deleted += 1
            except OSError:
                log.exception("could not delete uploaded file %s after reset", path.name)
        return deleted

    # ------------------------------------------------------------------ logging rows

    @staticmethod
    def _insert_row(conn: sqlite3.Connection, outcome: str, label: str,
                    failure_reason: str | None, detail: dict) -> int:
        cur = conn.execute(
            "INSERT INTO demo_resets (requested_at, outcome, requester_label, failure_reason, detail) "
            "VALUES (?, ?, ?, ?, ?)",
            (now_iso(), outcome, label, failure_reason, json.dumps(detail)),
        )
        return cur.lastrowid

    def _refuse(self, status: int, outcome: str, failure_reason: str, label: str, client_ip: str,
                message: str, error_code: str | None = None) -> ResetResult:
        """Record a non-success attempt in its own transaction, then build the response."""
        try:
            conn = connect(self._settings.database_path)
            try:
                conn.execute("BEGIN IMMEDIATE")
                reset_id = self._insert_row(conn, outcome, label, failure_reason,
                                            {"http_status": status, "ip": client_ip})
                conn.execute("COMMIT")
            finally:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                conn.close()
        except Exception:  # noqa: BLE001
            log.exception("could not write demo_resets row (outcome=%s)", outcome)
            return ResetResult(500, {"error": "reset_log_failed",
                                     "message": "Reset attempt could not be recorded. No data was changed."})
        log.info("demo reset attempt %s: %s (%s)", reset_id, outcome, error_code or failure_reason)
        return ResetResult(status, {"error": error_code or failure_reason, "message": message,
                                    "reset_id": reset_id})
