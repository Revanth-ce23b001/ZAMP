"""Extraction cache by file hash (FR-014, DEC-005) and the per-run extraction copy (§8.4).

extraction_cache survives demo reset; extractions is per run and is cleared by reset.
"""
import sqlite3
from dataclasses import dataclass

from app.services.extraction.schema import InvoiceExtraction


@dataclass(frozen=True)
class CachedExtraction:
    extraction: InvoiceExtraction
    provider: str
    model: str
    ingest_method: str
    extracted_at: str


def get(conn: sqlite3.Connection, file_hash: str) -> CachedExtraction | None:
    row = conn.execute(
        "SELECT provider, model, ingest_method, payload, extracted_at FROM extraction_cache WHERE file_hash = ?",
        (file_hash,),
    ).fetchone()
    if row is None:
        return None
    return CachedExtraction(InvoiceExtraction.model_validate_json(row["payload"]), row["provider"],
                            row["model"], row["ingest_method"], row["extracted_at"])


def put(conn: sqlite3.Connection, file_hash: str, provider: str, model: str, ingest_method: str,
        extraction: InvoiceExtraction, extracted_at: str) -> None:
    conn.execute(
        "INSERT INTO extraction_cache (file_hash, provider, model, ingest_method, payload, extracted_at) "
        "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT (file_hash) DO UPDATE SET provider = excluded.provider, "
        "model = excluded.model, ingest_method = excluded.ingest_method, payload = excluded.payload, "
        "extracted_at = excluded.extracted_at",
        (file_hash, provider, model, ingest_method, extraction.model_dump_json(), extracted_at),
    )


def put_run_copy(conn: sqlite3.Connection, run_id: str, file_hash: str, provider: str, model: str,
                 ingest_method: str, extraction: InvoiceExtraction, extracted_at: str, cached: bool) -> None:
    conn.execute(
        "INSERT INTO extractions (run_id, file_hash, provider, model, ingest_method, payload, extracted_at, cached) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT (run_id) DO UPDATE SET file_hash = excluded.file_hash, "
        "provider = excluded.provider, model = excluded.model, ingest_method = excluded.ingest_method, "
        "payload = excluded.payload, extracted_at = excluded.extracted_at, cached = excluded.cached",
        (run_id, file_hash, provider, model, ingest_method, extraction.model_dump_json(), extracted_at, int(cached)),
    )
