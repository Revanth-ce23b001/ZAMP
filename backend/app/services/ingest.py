"""Stage 1 Ingest (PRD §5.3, FR-010, FR-011, A-24).

Text first with pdfplumber. If the average non-whitespace characters per page is below the
policy threshold, the document is treated as a scan and its pages are rendered to PNG at the
policy DPI for the vision path.
"""
import re
from dataclasses import dataclass
from pathlib import Path

import pdfplumber
import pymupdf

from app.services.extraction.base import Pages

_WS = re.compile(r"\s")


class IngestError(RuntimeError):
    """Technical failure reading the file: the run goes to ERROR (never a business decision)."""


@dataclass(frozen=True)
class IngestResult:
    pages: Pages
    page_count: int
    avg_chars_per_page: float
    summary: str


def ingest(path: Path, min_chars_per_page: int, render_dpi: int) -> IngestResult:
    try:
        with pdfplumber.open(path) as pdf:
            texts = [page.extract_text() or "" for page in pdf.pages]
    except Exception as exc:  # noqa: BLE001 - any parse failure is a technical error
        raise IngestError(f"File could not be read: {type(exc).__name__}") from exc
    if not texts:
        raise IngestError("File could not be read: no pages")

    count = len(texts)
    avg = sum(len(_WS.sub("", t)) for t in texts) / count
    noun = "page" if count == 1 else "pages"
    if avg >= min_chars_per_page:
        return IngestResult(Pages("text", texts=texts), count, avg,
                            f"Digital PDF, text layer read ({count} {noun})")
    try:
        doc = pymupdf.open(path)
        try:
            images = [page.get_pixmap(dpi=render_dpi, alpha=False).tobytes("png") for page in doc]
        finally:
            doc.close()
    except Exception as exc:  # noqa: BLE001
        raise IngestError(f"File could not be read: page rendering failed ({type(exc).__name__})") from exc
    return IngestResult(Pages("vision", images=images), count, avg,
                        f"Scanned document, sent to vision model ({count} {noun})")
