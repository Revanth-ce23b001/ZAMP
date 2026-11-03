"""Extraction service: adapters built per chain entry from configuration (FR-015, DEC-013)."""
from collections.abc import Callable

from app.config import ChainEntry, Settings
from app.services.extraction.base import CONFIG, ExtractionAdapter, ExtractionError


def adapter_factory(settings: Settings) -> Callable[[ChainEntry], ExtractionAdapter]:
    """Return adapter_for(entry). Adapters are cached per entry for the process lifetime."""
    cache: dict[ChainEntry, ExtractionAdapter] = {}

    def adapter_for(entry: ChainEntry) -> ExtractionAdapter:
        if entry in cache:
            return cache[entry]
        key = settings.api_key_for(entry.provider)
        if not key:
            raise ExtractionError(f"no API key configured for {entry}", kind=CONFIG)
        if entry.provider == "gemini":
            from app.services.extraction.gemini import GeminiAdapter

            adapter = GeminiAdapter(key, entry.model)
        elif entry.provider == "anthropic":
            raise ExtractionError("the anthropic adapter is P1 and not built yet", kind=CONFIG)
        else:
            raise ExtractionError(f"unknown provider {entry.provider!r}", kind=CONFIG)
        cache[entry] = adapter
        return adapter

    return adapter_for
