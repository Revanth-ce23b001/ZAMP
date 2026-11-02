"""Upload validation and run creation (FR-001, FR-003, FR-004, FR-005).

validate_pdf runs BEFORE any run exists; every failure has a specific message and creates
nothing. submit() is the single entry point for both uploads and samples (FR-002): same
hashing, same storage, same queue.
"""
import hashlib
import sqlite3
import uuid
from dataclasses import dataclass
from pathlib import Path

import pymupdf

from app.services import audit
from app.utils.timeutil import now_iso

PDF_MIME = "application/pdf"
PDF_MAGIC = b"%PDF"


class UploadError(ValueError):
    def __init__(self, code: str, message: str, http_status: int = 400):
        super().__init__(message)
        self.code, self.message, self.http_status = code, message, http_status


@dataclass(frozen=True)
class SubmitResult:
    run_id: str
    existing: bool
    run_number: int


def validate_pdf(data: bytes, content_type: str | None, max_bytes: int, max_pages: int) -> int:
    """Return the page count, or raise UploadError. Order: type, signature, size, readable,
    not encrypted, page count."""
    mime = (content_type or "").split(";")[0].strip().lower()
    if mime != PDF_MIME:
        raise UploadError("not_pdf", "Only PDF files are supported")
    if not data.startswith(PDF_MAGIC):
        raise UploadError("not_pdf", "Only PDF files are supported (file is not a PDF)")
    if len(data) > max_bytes:
        raise UploadError("too_large", f"File exceeds {max_bytes // (1024 * 1024)} MB", 413)
    try:
        doc = pymupdf.open(stream=data, filetype="pdf")
    except Exception:  # noqa: BLE001
        raise UploadError("unreadable", "The PDF could not be read") from None
    try:
        if doc.needs_pass or doc.is_encrypted or (doc.metadata or {}).get("encryption"):
            raise UploadError("encrypted", "Encrypted PDFs are not supported")
        pages = doc.page_count
    finally:
        doc.close()
    if pages < 1:
        raise UploadError("unreadable", "The PDF has no pages")
    if pages > max_pages:
        raise UploadError("too_many_pages", f"PDF has more than {max_pages} pages")
    return pages


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def submit(conn: sqlite3.Connection, uploads_dir: Path, file_name: str, data: bytes, content_type: str | None,
           max_bytes: int, max_pages: int) -> SubmitResult:
    """Validate, hash, and either return the existing run (no reprocessing) or store the file
    and create a QUEUED run. The caller enqueues new runs."""
    validate_pdf(data, content_type, max_bytes, max_pages)
    file_hash = sha256(data)

    existing = conn.execute("SELECT run_id, run_number FROM runs WHERE file_hash = ?", (file_hash,)).fetchone()
    if existing:
        return SubmitResult(existing["run_id"], True, existing["run_number"])

    uploads_dir.mkdir(parents=True, exist_ok=True)
    path = uploads_dir / f"{file_hash}.pdf"
    if not path.exists():
        path.write_bytes(data)

    run_id = str(uuid.uuid4())
    now = now_iso()
    conn.execute("BEGIN IMMEDIATE")
    try:
        again = conn.execute("SELECT run_id, run_number FROM runs WHERE file_hash = ?", (file_hash,)).fetchone()
        if again:  # same file submitted concurrently
            conn.execute("ROLLBACK")
            return SubmitResult(again["run_id"], True, again["run_number"])
        number = conn.execute("SELECT COALESCE(MAX(run_number), 0) + 1 FROM runs").fetchone()[0]
        conn.execute(
            "INSERT INTO runs (run_id, run_number, file_name, file_hash, file_path, status, created_at) "
            "VALUES (?, ?, ?, ?, ?, 'QUEUED', ?)",
            (run_id, number, file_name, file_hash, str(path), now),
        )
        audit.add(conn, run_id, "run_created", {"run_number": number, "file_name": file_name,
                                                "file_hash": file_hash, "size_bytes": len(data)})
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return SubmitResult(run_id, False, number)


def display_number(run_number: int) -> str:
    return f"R-{run_number:04d}"
