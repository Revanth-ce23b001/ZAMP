"""Extraction service: adapter selection by configuration (FR-015)."""
from app.config import Settings
from app.services.extraction.base import ExtractionAdapter, ExtractionError


def adapter_from_settings(settings: Settings) -> ExtractionAdapter:
    """Build the configured adapter. Misconfiguration raises ExtractionError so the run goes to
    ERROR (a technical failure), never to a business decision."""
    provider = (settings.llm_provider or "").lower()
    if not provider or not settings.llm_model:
        raise ExtractionError("LLM provider is not configured (set LLM_PROVIDER and LLM_MODEL)")
    if provider == "gemini":
        if not settings.gemini_api_key:
            raise ExtractionError("GEMINI_API_KEY is not set")
        from app.services.extraction.gemini import GeminiAdapter

        return GeminiAdapter(settings.gemini_api_key, settings.llm_model)
    if provider == "anthropic":
        raise ExtractionError("the anthropic adapter is P1 and not built yet")
    raise ExtractionError(f"unknown LLM_PROVIDER {provider!r}")
