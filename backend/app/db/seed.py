"""Idempotent seed loader for the fixtures-v1 files in backend/seed/ (DEC-004).

Loads the four fixture files as-is. Keys starting with "_" (provenance annotations) are
ignored; no value is edited, regenerated or recomputed. Running it again inserts nothing
new: inserts use ON CONFLICT DO NOTHING, which skips only key conflicts. Unlike
INSERT OR IGNORE, it never silently drops a row that breaks a CHECK or NOT NULL rule.

Usage: python -m app.db.seed
"""
import json
import sqlite3
from pathlib import Path
from typing import Any

from app.db.connection import connect
from app.db.migrate import migrate

SEED_DIR = Path(__file__).resolve().parents[2] / "seed"
FIXTURE_FILES = {
    "vendors": "vendors.json",
    "purchase_orders": "purchase_orders.json",
    "ledger": "ledger.json",
    "consumption": "consumption.json",
}
FIXTURE_VERSION = "fixtures-v1"

# Tables whose row counts describe the seed state (PRD §12.3 /api/seed-status, FR-116 reset response).
COUNT_TABLES = {
    "vendors": "vendors",
    "purchase_orders": "purchase_orders",
    "po_lines": "po_lines",
    "ledger": "invoice_ledger",
    "consumption": "po_consumption",
}


class FixtureError(ValueError):
    pass


def _strip_private(obj: Any) -> Any:
    """Drop keys starting with '_' at every level."""
    if isinstance(obj, dict):
        return {k: _strip_private(v) for k, v in obj.items() if not k.startswith("_")}
    if isinstance(obj, list):
        return [_strip_private(v) for v in obj]
    return obj


def load_fixtures(seed_dir: Path = SEED_DIR) -> dict[str, list[dict]]:
    out = {}
    for key, name in FIXTURE_FILES.items():
        doc = json.loads((seed_dir / name).read_text(encoding="utf-8"))
        version = doc.get("_meta", {}).get("fixture_version")
        if version != FIXTURE_VERSION:
            raise FixtureError(f"{name}: fixture_version {version!r}, expected {FIXTURE_VERSION!r}")
        out[key] = _strip_private(doc["records"])
    validate_fixtures(out)
    return out


def validate_fixtures(fx: dict[str, list[dict]]) -> None:
    """Read-only consistency checks. A failure aborts the load; nothing is 'fixed'."""
    for po in fx["purchase_orders"]:
        line_sum = sum(l["quantity"] * l["unit_price"] for l in po["lines"])
        if po["po_total"] != line_sum:
            raise FixtureError(
                f"purchase_orders.json {po['po_number']} po_total mismatch: "
                f"{po['po_total']} != sum of lines {line_sum}"
            )
    for rec in fx["ledger"]:
        if rec.get("run_id") is not None or rec.get("source") != "seed":
            raise FixtureError(f"ledger.json {rec['ledger_id']}: seed rows must have source=seed, run_id=null")


def _insert(conn: sqlite3.Connection, table: str, row: dict) -> int:
    cols = list(row)
    sql = (
        f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)}) "
        "ON CONFLICT DO NOTHING"
    )
    return conn.execute(sql, [row[c] for c in cols]).rowcount


def insert_fixtures(conn: sqlite3.Connection, fx: dict[str, list[dict]]) -> dict[str, int]:
    """Insert fixture rows inside the caller's transaction. Returns rows newly inserted per table."""
    inserted = dict.fromkeys(COUNT_TABLES, 0)
    for v in fx["vendors"]:
        inserted["vendors"] += _insert(conn, "vendors", v)
    for po in fx["purchase_orders"]:
        header = {k: v for k, v in po.items() if k != "lines"}
        inserted["purchase_orders"] += _insert(conn, "purchase_orders", header)
        for line in po["lines"]:
            row = {**line, "po_number": po["po_number"], "aliases": json.dumps(line.get("aliases", []))}
            inserted["po_lines"] += _insert(conn, "po_lines", row)
    for rec in fx["ledger"]:
        row = {**rec, "line_signature": json.dumps(rec["line_signature"])}
        inserted["ledger"] += _insert(conn, "invoice_ledger", row)
    for rec in fx["consumption"]:
        inserted["consumption"] += _insert(conn, "po_consumption", rec)
    return inserted


def verify_seeded(conn: sqlite3.Connection, fx: dict[str, list[dict]]) -> None:
    """Every fixture key is present and foreign keys are clean."""
    problems = conn.execute("PRAGMA foreign_key_check").fetchall()
    if problems:
        raise FixtureError(f"foreign_key_check failed: {[tuple(p) for p in problems]}")
    checks = [
        ("vendors", "vendor_id", [v["vendor_id"] for v in fx["vendors"]]),
        ("purchase_orders", "po_number", [p["po_number"] for p in fx["purchase_orders"]]),
        ("po_lines", "po_line_id", [l["po_line_id"] for p in fx["purchase_orders"] for l in p["lines"]]),
        ("invoice_ledger", "ledger_id", [r["ledger_id"] for r in fx["ledger"]]),
    ]
    for table, col, keys in checks:
        found = {r[0] for r in conn.execute(f"SELECT {col} FROM {table}")}
        missing = set(keys) - found
        if missing:
            raise FixtureError(f"{table} missing seed rows {sorted(missing)}")
    pairs = {(r[0], r[1]) for r in conn.execute("SELECT ledger_id, po_line_id FROM po_consumption")}
    missing = {(c["ledger_id"], c["po_line_id"]) for c in fx["consumption"]} - pairs
    if missing:
        raise FixtureError(f"po_consumption missing seed rows {sorted(missing)}")


def table_counts(conn: sqlite3.Connection) -> dict[str, int]:
    return {key: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for key, table in COUNT_TABLES.items()}


def seed(conn: sqlite3.Connection, seed_dir: Path = SEED_DIR) -> dict[str, int]:
    """Load fixtures in one BEGIN IMMEDIATE transaction. Safe to run repeatedly."""
    fx = load_fixtures(seed_dir)
    conn.execute("BEGIN IMMEDIATE")
    try:
        inserted = insert_fixtures(conn, fx)
        verify_seeded(conn, fx)
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return inserted


def is_seeded(conn: sqlite3.Connection) -> bool:
    return conn.execute("SELECT EXISTS (SELECT 1 FROM vendors)").fetchone()[0] == 1


def main() -> None:
    from app.config import load_settings

    settings = load_settings(require_reset_token=False)
    conn = connect(settings.database_path)
    try:
        migrate(conn)
        inserted = seed(conn)
        print(f"Database: {settings.database_path}")
        print(f"Inserted now: {inserted}")
        print(f"Row counts:   {table_counts(conn)}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
