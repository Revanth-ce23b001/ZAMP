"""ClearMatch AP backend (FastAPI). Day 1: read endpoints and demo reset.

Run from backend/:  uvicorn app.main:app --env-file ../.env
"""
import sqlite3
from collections.abc import Iterator

from fastapi import Depends, FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import ConfigError, Settings, load_settings
from app.db.connection import connect
from app.db.migrate import migrate
from app.db.seed import FIXTURE_VERSION, is_seeded, seed, table_counts
from app.policy import Policy, load_policy
from app.services.reset import ResetService
from app.utils.masking import mask_account_number, masked_bank
from app.utils.money import money

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


def allowed_overrun(po_total: int, policy: Policy) -> int:
    """min(pct% of PO total, cap), applied once per PO (FR-068, A-12). Integer paise, floor."""
    tol = policy.assumption("A-12")
    return min(po_total * tol["pct_of_po_total"] // 100, tol["cap_paise"])


def create_app(settings: Settings | None = None) -> FastAPI:
    # Raises ConfigError (and so refuses to start) when DEMO_RESET_TOKEN is missing.
    settings = settings or load_settings(require_reset_token=True)
    if not settings.demo_reset_token:
        raise ConfigError("DEMO_RESET_TOKEN is required")
    policy = load_policy()
    _init_database(settings)
    settings.uploads_dir.mkdir(parents=True, exist_ok=True)

    app = FastAPI(title=PRODUCT_NAME, version=APP_VERSION)
    app.state.settings = settings
    app.state.policy = policy
    # NFR-05: CORS restricted to the frontend origin.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[settings.frontend_origin],
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
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
        rows = conn.execute(
            """
            SELECT po.po_number, po.vendor_id, v.legal_name AS vendor_name, po.po_date,
                   po.currency, po.po_total,
                   (SELECT COUNT(*) FROM po_lines l WHERE l.po_number = po.po_number) AS line_count,
                   (SELECT COALESCE(SUM(c.value), 0)
                      FROM po_consumption c JOIN po_lines l ON l.po_line_id = c.po_line_id
                     WHERE l.po_number = po.po_number) AS approved
              FROM purchase_orders po JOIN vendors v ON v.vendor_id = po.vendor_id
             ORDER BY po.po_number
            """
        ).fetchall()
        out = []
        for r in rows:
            allow = allowed_overrun(r["po_total"], policy)
            out.append({
                "po_number": r["po_number"],
                "vendor_id": r["vendor_id"],
                "vendor_name": r["vendor_name"],
                "po_date": r["po_date"],
                "currency": r["currency"],
                "line_count": r["line_count"],
                "po_total": money(r["po_total"]),
                # "approved" = consumed by committed approvals only (FR-069).
                "approved": money(r["approved"]),
                "remaining": money(r["po_total"] - r["approved"]),
                "allowed_overrun": money(allow),
                "ceiling": money(r["po_total"] + allow),
            })
        return out

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
