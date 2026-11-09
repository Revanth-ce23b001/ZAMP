"""ClearMatch AP backend (FastAPI). Read endpoints, demo reset, upload and stages 1-2.

Run from backend/:  uvicorn app.main:app --env-file .env
"""
import json
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Body, Depends, FastAPI, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse

from app.config import BACKEND_DIR, ChainEntry, ConfigError, Settings, load_settings
from app.db.cache_snapshot import load_snapshot_if_empty
from app.db.connection import connect
from app.db.migrate import migrate
from app.db.seed import FIXTURE_VERSION, is_seeded, seed, table_counts
from app.drafts import DraftError, drafts_for_run
from app.policy import Policy, load_policy
from app.services import audit, review, views
from app.services.review import ReviewError
from app.services.events import queue_position, run_events
from app.services.extraction.base import ExtractionAdapter
from app.services.reset import ResetService
from app.services.upload import PDF_MIME, UploadError, display_number, submit
from app.services.worker import Worker
from app.utils.masking import mask_account_number, masked_bank

PRODUCT_NAME = "ClearMatch AP"
APP_VERSION = "0.1.0"
PRD_VERSION = "1.2.1"


def _init_database(settings: Settings) -> None:
    """Apply migrations; load seed data if the database is empty (NFR-12)."""
    conn = connect(settings.database_path)
    try:
        migrate(conn)
        if not is_seeded(conn):
            seed(conn)
        load_snapshot_if_empty(conn, settings.cache_snapshot)   # DEC-014
    finally:
        conn.close()


def get_conn(request: Request) -> Iterator[sqlite3.Connection]:
    conn = connect(request.app.state.settings.database_path)
    try:
        yield conn
    finally:
        conn.close()


def get_policy(request: Request) -> Policy:
    return request.app.state.policy



def create_app(settings: Settings | None = None,
               adapter_for: Callable[[ChainEntry], ExtractionAdapter] | None = None) -> FastAPI:
    # Raises ConfigError (and so refuses to start) when DEMO_RESET_TOKEN is missing or an
    # extraction chain is empty or lacks credentials (DEC-013).
    settings = settings or load_settings(require_reset_token=True)
    if not settings.demo_reset_token:
        raise ConfigError("DEMO_RESET_TOKEN is required")
    settings.validate_chains()
    policy = load_policy()
    _init_database(settings)
    settings.uploads_dir.mkdir(parents=True, exist_ok=True)
    worker = Worker(settings, policy, adapter_for)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        worker.start()          # one sequential worker per backend instance (A-22)
        try:
            yield
        finally:
            worker.stop()

    app = FastAPI(title=PRODUCT_NAME, version=APP_VERSION, lifespan=lifespan)
    app.state.settings = settings
    app.state.policy = policy
    app.state.worker = worker
    app.state.sse_keepalive_seconds = 15.0        # proxies (Render, Vercel) drop idle streams
    upload_limits = policy.assumption("A-23")

    def _submit(file_name: str, data: bytes, content_type: str | None) -> JSONResponse:
        """Single path for uploads and samples (FR-001..FR-005, FR-002)."""
        conn = connect(settings.database_path)
        try:
            result = submit(conn, settings.uploads_dir, file_name, data, content_type,
                            upload_limits["max_bytes"], upload_limits["max_pages"])
        except UploadError as exc:
            return JSONResponse(status_code=exc.http_status, content={"error": exc.code, "message": exc.message})
        finally:
            conn.close()
        if not result.existing:
            worker.enqueue(result.run_id)
        return JSONResponse(status_code=201 if not result.existing else 200, content={
            "run_id": result.run_id, "existing": result.existing,
            "run_number": result.run_number, "run_display": display_number(result.run_number),
        })

    @app.post("/api/invoices")
    async def upload_invoice(file: UploadFile) -> JSONResponse:
        # Read at most one byte past the limit so an oversized upload is rejected without
        # holding the whole file in memory.
        data = await file.read(upload_limits["max_bytes"] + 1)
        return await run_in_threadpool(_submit, file.filename or "upload.pdf", data, file.content_type)

    def _sample_files() -> dict[str, Path]:
        return {p.name: p for p in sorted(settings.samples_dir.glob("*.pdf"))}

    @app.get("/api/samples")
    def list_samples() -> list[dict]:
        return [{"name": name, "size_bytes": p.stat().st_size} for name, p in _sample_files().items()]

    @app.post("/api/samples/{name}/process")
    async def process_sample(name: str) -> JSONResponse:
        path = _sample_files().get(name)   # exact listed name only; no path traversal
        if path is None:
            return JSONResponse(status_code=404, content={"error": "sample_not_found", "message": "Sample not found"})
        return await run_in_threadpool(_submit, name, path.read_bytes(), PDF_MIME)

    @app.get("/api/runs/{run_id}/events")
    async def run_events_stream(run_id: str, request: Request, last_event_id: int | None = None) -> StreamingResponse:
        """FR-114 SSE. Replays every stored event (or those after Last-Event-ID), then follows live.
        The browser connects here directly; streams are not proxied through Next.js."""
        header = request.headers.get("last-event-id")
        resume = last_event_id if last_event_id is not None else (int(header) if header and header.isdigit() else None)
        return StreamingResponse(
            run_events(settings.database_path, run_id, resume, request.is_disconnected,
                       keepalive_seconds=request.app.state.sse_keepalive_seconds),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
        )

    @app.get("/api/runs/{run_id}")
    def get_run(run_id: str, conn: sqlite3.Connection = Depends(get_conn)) -> JSONResponse:
        """FR-110: everything the live and decision details screens show. Masked: account numbers
        never leave unmasked (FR-041); trusted-contact phone/email only when BANK_DETAILS_MISMATCH
        is a reason code (FR-044)."""
        body = views.run_detail(conn, run_id)
        if body is None:
            return JSONResponse(status_code=404, content={"error": "run_not_found", "message": "Run not found"})
        return JSONResponse(body)

    @app.get("/api/runs/{run_id}/drafts")
    def get_run_drafts(run_id: str, conn: sqlite3.Connection = Depends(get_conn)) -> JSONResponse:
        """Day 10 (DEC-021): the resolution drafts for a decided run, one per recipient type.
        Generated from the stored decision on every request; nothing is written and nothing is sent.
        APPROVE returns an empty list; a run that is not yet decided returns 409. The trusted
        contact's name, phone and email never appear here (FR-044, DEC-008)."""
        try:
            return JSONResponse(drafts_for_run(conn, run_id, policy))
        except DraftError as exc:
            return JSONResponse(status_code=exc.http_status, content={"error": exc.code, "message": exc.message})

    @app.get("/api/runs/{run_id}/file")
    def get_run_file(run_id: str, conn: sqlite3.Connection = Depends(get_conn)):
        """FR-110: the uploaded PDF for preview."""
        run = conn.execute("SELECT file_path, file_name FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        if run is None or not Path(run["file_path"]).is_file():
            return JSONResponse(status_code=404, content={"error": "file_not_found", "message": "File not found"})
        return FileResponse(run["file_path"], media_type="application/pdf",
                            headers={"Content-Disposition": f'inline; filename="{run["file_name"]}"'})

    @app.post("/api/runs/{run_id}/retry")
    def retry_run(run_id: str) -> JSONResponse:
        """FR-018, FR-094: an ERROR run is re-queued under the same run_id."""
        conn = connect(settings.database_path)
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT status FROM runs WHERE run_id = ?", (run_id,)).fetchone()
            if row is None:
                conn.execute("ROLLBACK")
                return JSONResponse(status_code=404, content={"error": "run_not_found", "message": "Run not found"})
            if row["status"] != "ERROR":
                conn.execute("ROLLBACK")
                return JSONResponse(status_code=409, content={"error": "not_retryable",
                                                              "message": f"Only ERROR runs can be retried (run is {row['status']})"})
            conn.execute("UPDATE runs SET status = 'QUEUED', error_message = NULL WHERE run_id = ?", (run_id,))
            audit.add(conn, run_id, "retry", {})
            conn.execute("COMMIT")
        finally:
            conn.close()
        worker.enqueue(run_id)
        return JSONResponse(status_code=202, content={"run_id": run_id, "status": "QUEUED"})
    # NFR-05: CORS restricted to the frontend origin(s). FRONTEND_ORIGIN may list several, comma-
    # separated; FRONTEND_ORIGIN_REGEX optionally admits the project's own Vercel preview URLs.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[o.strip() for o in settings.frontend_origin.split(",") if o.strip()],
        allow_origin_regex=settings.frontend_origin_regex,
        allow_methods=["GET", "POST"],
        # The browser calls the backend directly for uploads, run data and the SSE stream
        # (streams are not proxied through Next.js); Last-Event-ID is sent on reconnect.
        allow_headers=["Content-Type", "Last-Event-ID"],
    )

    app.state.reset_service = ResetService(settings, policy)

    @app.post("/api/demo/reset")
    async def demo_reset(request: Request) -> JSONResponse:
        """FR-116. Called only by the Next.js server route, which injects the token.
        The body is parsed here (not by FastAPI) so a malformed body is still logged as an attempt."""
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001 - malformed or empty body -> confirmation mismatch
            body = None
        client_ip = request.client.host if request.client else "unknown"
        result = await run_in_threadpool(
            request.app.state.reset_service.handle,
            client_ip, request.headers.get("X-Demo-Reset-Token"), body,
            request.headers.get("X-Client-IP"),   # trusted only with a valid token (DEC-011)
        )
        return JSONResponse(status_code=result.status_code, content=result.body)

    @app.get("/api/health")
    def health() -> dict:
        return {"status": "ok"}

    @app.get("/api/meta")
    def meta(policy: Policy = Depends(get_policy)) -> dict:
        return {
            "product": PRODUCT_NAME,
            "product_version": APP_VERSION,
            "prd_version": PRD_VERSION,
            "policy_version": policy.version,
            "policy_version_short": policy.version_short,
            "fixture_version": FIXTURE_VERSION,
            "scope": {
                "currency": policy.assumption("A-01"),
                "tax_regime": policy.assumption("A-02")["regime"],
                "buyer_state_code": policy.assumption("A-03"),
                "matching": policy.assumption("A-21"),
                "processing": policy.assumption("A-22"),
                "synthetic_data": policy.assumption("A-06"),
            },
        }

    @app.get("/api/seed-status")
    def seed_status(conn: sqlite3.Connection = Depends(get_conn)) -> dict:
        return {"fixture_version": FIXTURE_VERSION, "counts": table_counts(conn)}

    @app.get("/api/pos")
    def list_pos(
        conn: sqlite3.Connection = Depends(get_conn), policy: Policy = Depends(get_policy)
    ) -> list[dict]:
        return views.po_balances(conn, policy)

    # ------------------------------------------------------------ review (FR-100..FR-106, FR-103)

    def _review(fn, run_id: str, body: dict) -> JSONResponse:
        conn = connect(settings.database_path)
        try:
            result = fn(conn, run_id, body, policy)
        except ReviewError as exc:
            return JSONResponse(status_code=exc.http_status, content={"error": exc.code, "message": exc.message})
        finally:
            conn.close()
        return JSONResponse(result.as_dict())

    @app.post("/api/runs/{run_id}/override")
    def override_run(run_id: str, body: dict = Body(...)) -> JSONResponse:
        """{action: approve | reject, reviewer_name, reason, codes_overridden, bank_verification?}"""
        return _review(review.override, run_id, body)

    @app.post("/api/runs/{run_id}/confirm-po")
    def confirm_po_run(run_id: str, body: dict = Body(...)) -> JSONResponse:
        """{po_number, reviewer_name, reason}"""
        return _review(review.confirm_po, run_id, body)

    # ------------------------------------------------------------ read models (FR-111..FR-117)

    @app.get("/api/runs")
    def list_runs(status: str | None = None, reason_code: str | None = None, po_number: str | None = None,
                  conn: sqlite3.Connection = Depends(get_conn)) -> list[dict]:
        return views.list_runs(conn, status, reason_code, po_number)

    @app.get("/api/runs/{run_id}/audit")
    def run_audit(run_id: str, conn: sqlite3.Connection = Depends(get_conn)) -> JSONResponse:
        body = views.run_audit(conn, run_id)
        if body is None:
            return JSONResponse(status_code=404, content={"error": "run_not_found", "message": "Run not found"})
        return JSONResponse(body)

    @app.get("/api/dashboard")
    def dashboard(conn: sqlite3.Connection = Depends(get_conn)) -> dict:
        return views.dashboard(conn, policy)

    @app.get("/api/policy")
    def policy_endpoint() -> dict:
        return views.policy_view(policy)

    @app.get("/api/accuracy")
    def accuracy_endpoint() -> Response:
        """Measured accuracy (DEC-018): the committed backend/seed/extraction_accuracy.json, served
        byte-for-byte. No computation and no database read, so it works on a freshly reset database."""
        path = BACKEND_DIR / "seed" / "extraction_accuracy.json"
        if not path.exists():
            return JSONResponse(status_code=404, content={"error": "not_measured",
                                                          "message": "Run python -m app.tools.measure_extraction_accuracy"})
        return Response(content=path.read_bytes(), media_type="application/json")

    @app.get("/api/vendors")
    def list_vendors(conn: sqlite3.Connection = Depends(get_conn)) -> list[dict]:
        rows = conn.execute(
            "SELECT vendor_id, legal_name, gstin, state_code, state_name, status, "
            "bank_account_number, ifsc FROM vendors ORDER BY vendor_id"
        ).fetchall()
        # Explicit whitelist. The full account number is read only to mask it (FR-041).
        # Trusted-contact fields are excluded entirely (FR-044).
        return [
            {
                "vendor_id": r["vendor_id"],
                "legal_name": r["legal_name"],
                "gstin": r["gstin"],
                "state_code": r["state_code"],
                "state_name": r["state_name"],
                "status": r["status"],
                "bank_account_masked": mask_account_number(r["bank_account_number"]),
                "ifsc": r["ifsc"],
                "bank_display": masked_bank(r["bank_account_number"], r["ifsc"]),
            }
            for r in rows
        ]

    return app


_app: FastAPI | None = None


def __getattr__(name: str):
    """Build `app` lazily so `uvicorn app.main:app` works while importing this module
    (e.g. `from app.main import create_app` in tests) has no side effects."""
    global _app
    if name == "app":
        if _app is None:
            _app = create_app()
        return _app
    raise AttributeError(name)
