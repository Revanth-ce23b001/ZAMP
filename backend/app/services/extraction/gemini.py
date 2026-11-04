"""Gemini adapter (FR-015, FR-016, FR-018).

Temperature 0, fixed seed, JSON output constrained to the §8.4 schema and validated with
Pydantic. Any failure (API error, timeout, malformed or schema-invalid JSON) is retried once;
a second failure raises ExtractionError, which the worker turns into run status ERROR.
"""
import json
import time

from pydantic import ValidationError

from app.services.extraction.base import (
    AVAILABILITY, OTHER, SCHEMA, ExtractionError, ExtractionResult, Pages, Usage,
)
from app.services.extraction.prompt import IMAGE_INPUT, SYSTEM_PROMPT, text_input
from app.services.extraction.schema import InvoiceExtraction

SEED = 0
TIMEOUT_MS = 120_000
RETRY_PAUSE_SECONDS = 2.0
MAX_ATTEMPTS = 2          # first call + one retry (FR-016, FR-018)


class GeminiAdapter:
    provider = "gemini"

    def __init__(self, api_key: str, model: str, retry_pause: float = RETRY_PAUSE_SECONDS,
                 timeout_ms: int = TIMEOUT_MS):
        from google import genai
        from google.genai import types

        self._types = types
        self._client = genai.Client(api_key=api_key, http_options=types.HttpOptions(timeout=timeout_ms))
        self.model = model
        self._retry_pause = retry_pause

    def _config(self, schema: type[InvoiceExtraction]):
        t = self._types
        # Gemini 3 models take a thinking level; older models take a token budget. Keep thinking
        # minimal: transcription needs no reasoning, and less thinking means less variance.
        thinking = (t.ThinkingConfig(thinking_level="LOW") if self.model.startswith("gemini-3")
                    else t.ThinkingConfig(thinking_budget=0))
        return t.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            temperature=0,
            seed=SEED,
            response_mime_type="application/json",
            # Standard JSON Schema (response_json_schema); the older response_schema field
            # rejects additionalProperties, which the strict model emits.
            response_json_schema=schema.model_json_schema(),
            thinking_config=thinking,
        )

    def _contents(self, pages: Pages) -> list:
        if pages.method == "vision":
            parts = [self._types.Part.from_bytes(data=img, mime_type="image/png") for img in pages.images]
            return [IMAGE_INPUT, *parts]
        return [text_input(pages.texts)]

    def extract(self, pages: Pages, schema: type[InvoiceExtraction] = InvoiceExtraction) -> ExtractionResult:
        config, contents = self._config(schema), self._contents(pages)
        usage = Usage()
        last_error, last_kind = "unknown error", OTHER
        failures: list[str] = []
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                resp = self._client.models.generate_content(model=self.model, contents=contents, config=config)
                u = resp.usage_metadata
                usage = Usage(
                    input_tokens=usage.input_tokens + (u.prompt_token_count or 0 if u else 0),
                    output_tokens=usage.output_tokens + (u.candidates_token_count or 0 if u else 0),
                    thinking_tokens=usage.thinking_tokens + (u.thoughts_token_count or 0 if u else 0),
                    calls=usage.calls + 1,
                )
                extraction = schema.model_validate_json(resp.text or "")
                return ExtractionResult(extraction, self.provider, self.model, usage, attempt, failures)
            except (ValidationError, json.JSONDecodeError, ValueError) as exc:
                last_error, last_kind = f"malformed extraction output: {type(exc).__name__}", SCHEMA
            except Exception as exc:  # noqa: BLE001 - API errors, timeouts, network
                last_error = f"provider call failed: {type(exc).__name__}: {str(exc)[:300]}"
                last_kind = classify(exc)
                usage = Usage(usage.input_tokens, usage.output_tokens, usage.thinking_tokens, usage.calls + 1)
            failures.append(f"{last_kind}: {last_error[:200]}")
            if attempt < MAX_ATTEMPTS:
                time.sleep(self._retry_pause)
        raise ExtractionError(f"{last_error} (after {MAX_ATTEMPTS} attempts)", usage, kind=last_kind)


def classify(exc: Exception) -> str:
    """AVAILABILITY for rate limits, server errors, timeouts and connection failures."""
    code = getattr(exc, "code", None)
    if isinstance(code, int):
        return AVAILABILITY if code == 429 or code >= 500 else OTHER
    name = type(exc).__name__.lower()
    if isinstance(exc, (TimeoutError, ConnectionError)) or any(
            k in name for k in ("timeout", "connect", "network", "remoteprotocol")):
        return AVAILABILITY
    return OTHER
