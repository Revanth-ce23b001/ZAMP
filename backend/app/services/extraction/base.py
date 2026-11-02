"""Provider-agnostic extraction interface (FR-015)."""
from dataclasses import dataclass, field
from typing import Protocol

from app.services.extraction.schema import InvoiceExtraction


@dataclass(frozen=True)
class Pages:
    """Ingest output handed to the adapter: page text (text path) or PNG images (vision path)."""
    method: str                      # "text" | "vision"
    texts: list[str] = field(default_factory=list)
    images: list[bytes] = field(default_factory=list)


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    thinking_tokens: int = 0
    calls: int = 0


@dataclass(frozen=True)
class ExtractionResult:
    extraction: InvoiceExtraction
    provider: str
    model: str
    usage: Usage
    attempts: int


class ExtractionError(RuntimeError):
    """Technical failure (API error, timeout, malformed output twice). Leads to run ERROR, never
    to a business decision (FR-018)."""

    def __init__(self, message: str, usage: Usage | None = None):
        super().__init__(message)
        self.usage = usage or Usage()


class ExtractionAdapter(Protocol):
    provider: str
    model: str

    def extract(self, pages: Pages, schema: type[InvoiceExtraction] = InvoiceExtraction) -> ExtractionResult: ...
