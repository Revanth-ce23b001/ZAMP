"""SQLite connections with foreign keys enforced."""
import sqlite3
from pathlib import Path


def connect(db_path: str | Path) -> sqlite3.Connection:
    """Open a connection in autocommit mode (transactions are explicit: BEGIN / BEGIN IMMEDIATE)."""
    if str(db_path) != ":memory:":
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    # check_same_thread=False: FastAPI may open a request's connection (generator dependency) on one
    # threadpool thread and run the handler on another. Each connection still serves exactly one
    # request or one worker job at a time; it is never shared concurrently.
    conn = sqlite3.connect(db_path, isolation_level=None, check_same_thread=False, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn
