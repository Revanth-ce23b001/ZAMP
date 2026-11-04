"""Free-tier provider chain (DEC-013, DEC-017): primary gemini-3.6-flash, fallback gemini-3.5-flash-lite.

Real GeminiAdapter instances with a fake client, so the adapter's own retry, error classification and
schema validation are exercised exactly as in production. No network calls.
"""
import dataclasses
import json
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient
from google.genai import errors as genai_errors

from app.config import ChainEntry, load_settings
from app.main import create_app
from app.services.extraction.base import AVAILABILITY, SCHEMA, ExtractionError, Pages
from app.services.extraction.chain import run_chain
from app.services.extraction.gemini import GeminiAdapter
from app.services.extraction.schema import InvoiceExtraction
from tests.test_scenarios import SNAPSHOT

PRIMARY, FALLBACK = ChainEntry("gemini", "gemini-3.6-flash"), ChainEntry("gemini", "gemini-3.5-flash-lite")
RECORDS = json.loads(SNAPSHOT.read_text(encoding="utf-8"))["records"]
T01_JSON = json.dumps(next(r for r in RECORDS if r["file_name"].startswith("T01"))["payload"])
T05_JSON = json.dumps(next(r for r in RECORDS if r["ingest_method"] == "vision")["payload"])


def ok(text):
    return SimpleNamespace(text=text, usage_metadata=SimpleNamespace(
        prompt_token_count=800, candidates_token_count=700, thoughts_token_count=0))


def quota_429():
    """The exact shape Google returns when the free-tier daily quota is used up."""
    return genai_errors.ClientError(429, {"error": {
        "code": 429, "status": "RESOURCE_EXHAUSTED", "message": "You exceeded your current quota",
        "details": [{"violations": [{"quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier",
                                     "quotaValue": "20"}]}]}})


class FakeModels:
    def __init__(self, outcomes):
        self.outcomes, self.calls = list(outcomes), []

    def generate_content(self, model, contents, config):
        self.calls.append(model)
        out = self.outcomes.pop(0)
        if isinstance(out, Exception):
            raise out
        return out


def gemini(entry, outcomes):
    a = GeminiAdapter("fake-key", entry.model, retry_pause=0)
    a._client = SimpleNamespace(models=FakeModels(outcomes))
    return a


def chain(primary_outcomes, fallback_outcomes):
    adapters = {PRIMARY: gemini(PRIMARY, primary_outcomes), FALLBACK: gemini(FALLBACK, fallback_outcomes)}
    return adapters, adapters.__getitem__


TEXT = Pages("text", texts=["page"])


def test_primary_success_never_touches_fallback():
    adapters, get = chain([ok(T01_JSON)], [])
    res = run_chain((PRIMARY, FALLBACK), get, TEXT)
    assert res.model == "gemini-3.6-flash" and res.attempts == 1
    assert adapters[FALLBACK]._client.models.calls == []


def test_primary_quota_429_falls_back_to_flash_lite():
    adapters, get = chain([quota_429(), quota_429()], [ok(T01_JSON)])
    seen = []
    res = run_chain((PRIMARY, FALLBACK), get, TEXT, lambda att, nxt: seen.append((att.ok, att.error_kind, nxt)))
    assert res.model == "gemini-3.5-flash-lite"
    assert seen[0] == (False, AVAILABILITY, FALLBACK)
    assert adapters[PRIMARY]._client.models.calls == ["gemini-3.6-flash"] * 2     # its one retry (DEC-013)


@pytest.mark.parametrize("exc", [httpx.ReadTimeout("timed out"), TimeoutError(), httpx.ConnectError("refused"),
                                 genai_errors.ServerError(503, {"error": {"code": 503, "status": "UNAVAILABLE"}})])
def test_timeout_connection_and_overload_fall_back(exc):
    _, get = chain([exc, exc], [ok(T01_JSON)])
    assert run_chain((PRIMARY, FALLBACK), get, TEXT).model == "gemini-3.5-flash-lite"


def test_both_models_return_the_same_schema_and_confidences():
    _, get_a = chain([ok(T01_JSON)], [])
    _, get_b = chain([quota_429(), quota_429()], [ok(T01_JSON)])
    a = run_chain((PRIMARY, FALLBACK), get_a, TEXT).extraction
    b = run_chain((PRIMARY, FALLBACK), get_b, TEXT).extraction
    assert isinstance(a, InvoiceExtraction) and isinstance(b, InvoiceExtraction)
    assert a.model_dump() == b.model_dump()
    assert a.total.confidence == json.loads(T01_JSON)["total"]["confidence"]   # field-level confidence kept


def test_fallback_failure_exhausts_chain():
    _, get = chain([quota_429(), quota_429()], [quota_429(), quota_429()])
    with pytest.raises(ExtractionError, match="all 2 providers unavailable") as info:
        run_chain((PRIMARY, FALLBACK), get, TEXT)
    assert info.value.kind == AVAILABILITY


@pytest.mark.parametrize("bad", ["{not json", '{"total": {"value": "abc", "confidence": 0.9}}', ""])
def test_malformed_output_does_not_fall_back(bad):
    """A schema failure is a prompt/data defect, not an availability problem: it must surface."""
    adapters, get = chain([ok(bad), ok(bad)], [ok(T01_JSON)])
    with pytest.raises(ExtractionError) as info:
        run_chain((PRIMARY, FALLBACK), get, TEXT)
    assert info.value.kind == SCHEMA
    assert adapters[FALLBACK]._client.models.calls == []


def test_extraction_schema_cannot_carry_a_decision():
    """The LLM transcribes; it has no field through which to approve, hold or reject."""
    fields = set(InvoiceExtraction.model_fields)
    assert not fields & {"status", "decision", "approve", "approved", "reason_codes", "outcome"}


def test_env_config_real_model_ids_and_timeout(monkeypatch):
    import app.services.extraction.gemini as gem
    from app.services.extraction import adapter_factory

    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
    monkeypatch.setenv("EXTRACTION_TEXT_CHAIN", "gemini:gemini-3.6-flash,gemini:gemini-3.5-flash-lite")
    monkeypatch.setenv("EXTRACTION_VISION_CHAIN", "gemini:gemini-3.6-flash,gemini:gemini-3.5-flash-lite")
    monkeypatch.setenv("EXTRACTION_TIMEOUT_SECONDS", "45")
    s = load_settings(require_reset_token=False)
    assert s.text_chain == s.vision_chain == (PRIMARY, FALLBACK)
    built = []
    monkeypatch.setattr(gem, "GeminiAdapter", lambda key, model, timeout_ms: built.append((model, timeout_ms)) or object())
    adapter_for = adapter_factory(s)
    adapter_for(PRIMARY), adapter_for(FALLBACK)
    assert built == [("gemini-3.6-flash", 45000), ("gemini-3.5-flash-lite", 45000)]


def test_timeout_default_unchanged(monkeypatch):
    monkeypatch.delenv("EXTRACTION_TIMEOUT_SECONDS", raising=False)
    assert load_settings(require_reset_token=False).extraction_timeout_seconds == 120


# ---------- through the worker: scanned invoice on the vision chain ----------

def test_scanned_invoice_primary_429_served_by_fallback(settings):
    """T-05 is a scan: no text layer, so the vision chain runs. The primary is out of quota, flash-lite
    serves it, the fallback is audited, and the rules (not the model) decide."""
    from tests.test_pipeline import rows, upload
    from app.config import BACKEND_DIR

    t05 = next((BACKEND_DIR / "samples").glob("T05_*.pdf"))
    s = dataclasses.replace(settings, text_chain=(PRIMARY, FALLBACK), vision_chain=(PRIMARY, FALLBACK),
                            gemini_api_key="fake-key")
    adapters = {PRIMARY: gemini(PRIMARY, [quota_429(), quota_429()]), FALLBACK: gemini(FALLBACK, [ok(T05_JSON)])}
    with TestClient(create_app(s, adapter_for=adapters.__getitem__)) as c:
        c.settings = s
        r = upload(c, t05.read_bytes()).json()
        assert c.app.state.worker.wait_idle(60)
        ext = rows(c, "SELECT provider, model, ingest_method, cached FROM extractions WHERE run_id = ?", r["run_id"])[0]
        assert ext == {"provider": "gemini", "model": "gemini-3.5-flash-lite", "ingest_method": "vision", "cached": 0}
        sent = adapters[FALLBACK]._client.models.calls
        assert sent == ["gemini-3.5-flash-lite"]
        events = [json.loads(e["payload"]) for e in rows(
            c, "SELECT payload FROM audit_events WHERE run_id = ? AND type = 'extraction_called' ORDER BY event_id",
            r["run_id"])]
        assert events[0]["fallback"] is True and events[0]["model"] == "gemini-3.6-flash"
        assert events[-1]["ok"] is True and events[-1]["model"] == "gemini-3.5-flash-lite"
        dec = rows(c, "SELECT status, reason_codes, decided_by FROM decisions WHERE run_id = ?", r["run_id"])[0]
        assert dec["decided_by"] == "rules"
        assert (dec["status"], json.loads(dec["reason_codes"])) == ("REJECT", ["CONFIRMED_DUPLICATE"])
