"""Sequential processing worker (FR-005, FR-093, NFR-11).

One in-process thread consumes a FIFO queue of run_ids, one run at a time; there is no
parallel path. Day 2B runs stages 1 (Ingest) and 2 (Extract) and leaves the run at PROCESSING
for stages 3-7 (Day 3). Technical failures set run status ERROR with a message; they are never
converted into HOLD or REJECT (FR-018, NFR-09).
"""
import json
import logging
import queue
import threading
import time
from collections.abc import Callable
from pathlib import Path

from app.config import Settings
from app.db.connection import connect
from app.policy import Policy
from app.services import audit
from app.services.extraction import adapter_from_settings
from app.services.extraction import cache as extraction_cache
from app.services.extraction.base import ExtractionAdapter, ExtractionError
from app.services.ingest import IngestError, ingest
from app.utils.timeutil import now_iso

log = logging.getLogger("clearmatch.worker")


class Worker:
    def __init__(self, settings: Settings, policy: Policy,
                 adapter_factory: Callable[[], ExtractionAdapter] | None = None):
        self._settings = settings
        self._min_chars = policy.assumption("A-24")
        self._dpi = policy.data["other_assumptions"]["render_dpi"]["value"]
        self._adapter_factory = adapter_factory or (lambda: adapter_from_settings(settings))
        self._queue: queue.Queue[str | None] = queue.Queue()
        self._pending = 0
        self._cond = threading.Condition()
        self._thread: threading.Thread | None = None

    # ------------------------------------------------------------------ lifecycle

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._loop, name="clearmatch-worker", daemon=True)
        self._thread.start()
        conn = connect(self._settings.database_path)
        try:
            for row in conn.execute("SELECT run_id FROM runs WHERE status = 'QUEUED' ORDER BY run_number"):
                self.enqueue(row["run_id"])
        finally:
            conn.close()

    def stop(self, timeout: float = 10) -> None:
        if self._thread and self._thread.is_alive():
            self._queue.put(None)
            self._thread.join(timeout)

    def enqueue(self, run_id: str) -> None:
        with self._cond:
            self._pending += 1
        self._queue.put(run_id)

    def wait_idle(self, timeout: float = 30) -> bool:
        """Block until every enqueued run has been processed (used by tests)."""
        end = time.monotonic() + timeout
        with self._cond:
            while self._pending:
                left = end - time.monotonic()
                if left <= 0:
                    return False
                self._cond.wait(left)
        return True

    def _loop(self) -> None:
        while True:
            run_id = self._queue.get()
            if run_id is None:
                return
            try:
                self.process(run_id)
            except Exception:  # noqa: BLE001 - never let the worker thread die
                log.exception("unexpected worker failure on run %s", run_id)
                self._fail(run_id, "STAGE", "Unexpected processing error")
            finally:
                with self._cond:
                    self._pending -= 1
                    self._cond.notify_all()

    # ------------------------------------------------------------------ processing

    def _redact(self, text: str) -> str:
        for secret in self._settings.secrets():
            text = text.replace(secret, "[redacted]")
        return text

    def _save_stages(self, conn, run_id: str, stages: list[dict]) -> None:
        conn.execute("UPDATE runs SET stage_results = ? WHERE run_id = ?", (json.dumps(stages), run_id))

    def _fail(self, run_id: str, stage: str, message: str, stages: list[dict] | None = None) -> None:
        message = self._redact(message)
        conn = connect(self._settings.database_path)
        try:
            conn.execute("BEGIN IMMEDIATE")
            if stages is not None:
                self._save_stages(conn, run_id, stages)
            conn.execute(
                "UPDATE runs SET status = 'ERROR', error_message = ?, completed_at = ? WHERE run_id = ?",
                (message, now_iso(), run_id),
            )
            audit.add(conn, run_id, "error", {"stage": stage, "message": message})
            conn.execute("COMMIT")
        except Exception:  # noqa: BLE001
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            log.exception("could not record ERROR for run %s", run_id)
        finally:
            conn.close()
        log.warning("run %s ERROR at %s: %s", run_id, stage, message)

    def process(self, run_id: str) -> None:
        conn = connect(self._settings.database_path)
        try:
            run = conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
            if run is None or run["status"] != "QUEUED":
                return
            conn.execute(
                "UPDATE runs SET status = 'PROCESSING', started_at = ?, error_message = NULL, "
                "completed_at = NULL, stage_results = '[]' WHERE run_id = ?",
                (now_iso(), run_id),
            )
            stages: list[dict] = []

            # ---------------- Stage 1: Ingest
            started = now_iso()
            audit.add(conn, run_id, "stage_started", {"stage": "INGEST"})
            try:
                result = ingest(Path(run["file_path"]), self._min_chars, self._dpi)
            except IngestError as exc:
                stages.append(self._stage("INGEST", "ERROR", str(exc), started))
                self._fail(run_id, "INGEST", str(exc), stages)
                return
            stages.append(self._stage("INGEST", "PASSED", result.summary, started))
            conn.execute("UPDATE runs SET ingest_method = ? WHERE run_id = ?", (result.pages.method, run_id))
            self._save_stages(conn, run_id, stages)
            audit.add(conn, run_id, "stage_completed", {"stage": "INGEST", "status": "PASSED",
                                                        "summary": result.summary,
                                                        "avg_chars_per_page": round(result.avg_chars_per_page, 1)})

            # ---------------- Stage 2: Extract
            started = now_iso()
            audit.add(conn, run_id, "stage_started", {"stage": "EXTRACT"})
            file_hash = run["file_hash"]
            hit = extraction_cache.get(conn, file_hash) if self._settings.extraction_cache_enabled else None
            if hit:
                conn.execute("BEGIN IMMEDIATE")
                extraction_cache.put_run_copy(conn, run_id, file_hash, hit.provider, hit.model, hit.ingest_method,
                                              hit.extraction, hit.extracted_at, cached=True)
                audit.add(conn, run_id, "extraction_cached", {"provider": hit.provider, "model": hit.model,
                                                              "extracted_at": hit.extracted_at})
                summary = f"{hit.extraction.field_count()} fields extracted (cached)"
            else:
                try:
                    adapter = self._adapter_factory()
                    res = adapter.extract(result.pages)
                except ExtractionError as exc:
                    audit.add(conn, run_id, "extraction_called",
                              {"ok": False, **self._usage(exc.usage)})
                    stages.append(self._stage("EXTRACT", "ERROR", "Extraction failed", started))
                    self._fail(run_id, "EXTRACT", str(exc), stages)
                    return
                at = now_iso()
                conn.execute("BEGIN IMMEDIATE")
                if self._settings.extraction_cache_enabled:
                    extraction_cache.put(conn, file_hash, res.provider, res.model, result.pages.method,
                                         res.extraction, at)
                extraction_cache.put_run_copy(conn, run_id, file_hash, res.provider, res.model,
                                              result.pages.method, res.extraction, at, cached=False)
                audit.add(conn, run_id, "extraction_called",
                          {"ok": True, "provider": res.provider, "model": res.model, "attempts": res.attempts,
                           "temperature": 0, **self._usage(res.usage)})
                summary = f"{res.extraction.field_count()} fields extracted ({res.provider}/{res.model})"
            stages.append(self._stage("EXTRACT", "PASSED", summary, started))
            self._save_stages(conn, run_id, stages)
            audit.add(conn, run_id, "stage_completed", {"stage": "EXTRACT", "status": "PASSED", "summary": summary})
            conn.execute("COMMIT")
            # Stages 3-7 arrive on Day 3; the run stays PROCESSING until then.
        finally:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            conn.close()

    @staticmethod
    def _stage(stage: str, status: str, summary: str, started_at: str) -> dict:
        return {"stage": stage, "status": status, "summary": summary, "evidence_ids": [],
                "started_at": started_at, "ended_at": now_iso()}

    @staticmethod
    def _usage(u) -> dict:
        return {"calls": u.calls, "input_tokens": u.input_tokens, "output_tokens": u.output_tokens,
                "thinking_tokens": u.thinking_tokens}
