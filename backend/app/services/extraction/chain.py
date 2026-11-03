"""Ordered provider fallback chain (FR-015 extended; DECISIONS DEC-013).

Each entry is tried with its own adapter (which keeps today's one-retry behaviour). If an entry
finally fails for an AVAILABILITY reason (429, 5xx, timeout, connection), the next entry is
tried. A schema/validation failure or any other error stops the chain: that is a prompt or
configuration problem, and silently retrying elsewhere would hide it. A chain of length one
behaves exactly like a single adapter.
"""
from collections.abc import Callable
from dataclasses import dataclass

from app.config import ChainEntry
from app.services.extraction.base import (
    AVAILABILITY, ExtractionAdapter, ExtractionError, ExtractionResult, Pages, Usage,
)


@dataclass(frozen=True)
class Attempt:
    entry: ChainEntry
    ok: bool
    usage: Usage
    error_kind: str | None = None
    error: str | None = None


class ChainExhausted(ExtractionError):
    pass


def run_chain(entries: tuple[ChainEntry, ...], adapter_for: Callable[[ChainEntry], ExtractionAdapter],
              pages: Pages, on_attempt: Callable[[Attempt, ChainEntry | None], None] | None = None
              ) -> ExtractionResult:
    """Try entries in order. on_attempt(attempt, next_entry) is called after every entry;
    next_entry is set when the chain falls through to it."""
    if not entries:
        raise ExtractionError("extraction chain is empty", kind="config")
    last: ExtractionError | None = None
    for i, entry in enumerate(entries):
        nxt = entries[i + 1] if i + 1 < len(entries) else None
        try:
            result = adapter_for(entry).extract(pages)
        except ExtractionError as exc:
            falls_through = exc.kind == AVAILABILITY and nxt is not None
            if on_attempt:
                on_attempt(Attempt(entry, False, exc.usage, exc.kind, str(exc)), nxt if falls_through else None)
            if exc.kind != AVAILABILITY:
                raise                      # schema / config / other: never fall through
            last = exc
            continue
        if on_attempt:
            on_attempt(Attempt(entry, True, result.usage), None)
        return result
    raise ChainExhausted(f"all {len(entries)} providers unavailable; last error: {last}", kind=AVAILABILITY)
