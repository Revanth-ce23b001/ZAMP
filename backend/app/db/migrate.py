"""Versioned migration runner.

Applies backend/app/db/migrations/NNNN_name.sql files in order. Each file runs in one
transaction together with its schema_migrations row, so a migration is applied fully or
not at all. Usage: python -m app.db.migrate
"""
import sqlite3
from pathlib import Path

from app.db.connection import connect
from app.utils.timeutil import now_iso

MIGRATIONS_DIR = Path(__file__).with_name("migrations")

_BOOKKEEPING = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version     TEXT PRIMARY KEY,
    applied_at  TEXT NOT NULL
)
"""


def available_migrations() -> list[tuple[str, Path]]:
    files = sorted(MIGRATIONS_DIR.glob("[0-9][0-9][0-9][0-9]_*.sql"))
    return [(f.stem, f) for f in files]


def applied_versions(conn: sqlite3.Connection) -> list[str]:
    conn.execute(_BOOKKEEPING)
    return [r[0] for r in conn.execute("SELECT version FROM schema_migrations ORDER BY version")]


def migrate(conn: sqlite3.Connection) -> list[str]:
    """Apply pending migrations; returns the versions applied by this call."""
    done = set(applied_versions(conn))
    newly = []
    for version, path in available_migrations():
        if version in done:
            continue
        sql = path.read_text(encoding="utf-8")
        stamp = now_iso().replace("'", "")
        script = (
            "BEGIN IMMEDIATE;\n"
            f"{sql}\n;\n"
            f"INSERT INTO schema_migrations (version, applied_at) VALUES ('{version}', '{stamp}');\n"
            "COMMIT;"
        )
        try:
            conn.executescript(script)
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        newly.append(version)
    return newly


def main() -> None:
    from app.config import load_settings

    settings = load_settings(require_reset_token=False)
    conn = connect(settings.database_path)
    try:
        applied = migrate(conn)
        print(f"Database: {settings.database_path}")
        print(f"Applied now: {applied or 'none'}; all applied: {applied_versions(conn)}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
