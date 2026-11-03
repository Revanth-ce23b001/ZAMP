"""Provider fallback chain (DEC-015) and cache snapshot (DEC-013)."""
import dataclasses
import json

import pytest
from fastapi.testclient import TestClient

from app.config import ChainEntry, ConfigError, load_settings, parse_chain
from app.db.connection import connect
from app.main import create_app
from app.services.extraction.base import (
    AVAILABILITY, CONFIG, OTHER, SCHEMA, ExtractionError, ExtractionResult, Pages, Usage,
)
from app.services.extraction.chain import ChainExhausted, run_chain
from app.services.extraction.gemini import classify
from app.services.extraction.schema import InvoiceExtraction

A, B, C = ChainEntry("gemini", "model-a"), ChainEntry("gemini", "model-b"), ChainEntry("anthropic", "model-c")
PAGES = Pages("text", texts=["page"])


class Scripted:
    """Adapter that raises the scripted error kinds, then serves."""

    def __init__(self, entry, error_kind=None):
        self.provider, self.model = entry.provider, entry.model
        self.error_kind = error_kind
        self.calls = 0

    def extract(self, pages, schema=InvoiceExtraction):
        self.calls += 1
        if self.error_kind:
            raise ExtractionError(f"{self.model} failed ({self.error_kind})", Usage(calls=2), kind=self.error_kind)
        ex = InvoiceExtraction.model_validate({"invoice_number": {"value": self.model, "confidence": 0.9}})
        return ExtractionResult(ex, self.provider, self.model, Usage(calls=1), 1)


def chain_of(*specs):
    adapters = {entry: Scripted(entry, kind) for entry, kind in specs}
    return tuple(adapters), adapters


# ---------- run_chain ----------

def test_429_on_first_falls_through_to_second():
    entries, adapters = chain_of((A, AVAILABILITY), (B, None))
    seen = []
    res = run_chain(entries, adapters.__getitem__, PAGES, lambda att, nxt: seen.append((str(att.entry), att.ok, nxt)))
    assert (res.provider, res.model) == ("gemini", "model-b")
    assert seen == [("gemini:model-a", False, B), ("gemini:model-b", True, None)]


def test_schema_failure_does_not_fall_through():
    entries, adapters = chain_of((A, SCHEMA), (B, None))
    with pytest.raises(ExtractionError) as info:
        run_chain(entries, adapters.__getitem__, PAGES)
    assert info.value.kind == SCHEMA
    assert adapters[B].calls == 0


@pytest.mark.parametrize("kind", [OTHER, CONFIG])
def test_other_failures_do_not_fall_through(kind):
    entries, adapters = chain_of((A, kind), (B, None))
    with pytest.raises(ExtractionError):
        run_chain(entries, adapters.__getitem__, PAGES)
    assert adapters[B].calls == 0


def test_chain_exhausted():
    entries, adapters = chain_of((A, AVAILABILITY), (B, AVAILABILITY))
    with pytest.raises(ChainExhausted, match="all 2 providers unavailable"):
        run_chain(entries, adapters.__getitem__, PAGES)


def test_chain_of_one_behaves_like_single_adapter():
    entries, adapters = chain_of((A, None))
    assert run_chain(entries, adapters.__getitem__, PAGES).model == "model-a"
    entries, adapters = chain_of((A, AVAILABILITY))
    with pytest.raises(ExtractionError):
        run_chain(entries, adapters.__getitem__, PAGES)
    assert adapters[A].calls == 1


def test_empty_chain_rejected_by_run_chain():
    with pytest.raises(ExtractionError, match="empty"):
        run_chain((), lambda e: None, PAGES)


# ---------- error classification ----------

class FakeAPIError(Exception):
    def __init__(self, code):
        self.code = code


@pytest.mark.parametrize("exc, kind", [
    (FakeAPIError(429), AVAILABILITY), (FakeAPIError(503), AVAILABILITY), (FakeAPIError(504), AVAILABILITY),
    (FakeAPIError(500), AVAILABILITY), (FakeAPIError(400), OTHER), (FakeAPIError(401), OTHER),
    (FakeAPIError(404), OTHER), (TimeoutError(), AVAILABILITY), (ConnectionError(), AVAILABILITY),
    (type("ReadTimeout", (Exception,), {})(), AVAILABILITY), (type("ConnectError", (Exception,), {})(), AVAILABILITY),
    (RuntimeError("boom"), OTHER),
])
def test_classify(exc, kind):
    assert classify(exc) == kind


# ---------- config ----------

def test_parse_chain():
    assert parse_chain("gemini:a, Gemini:b ,anthropic:c") == (ChainEntry("gemini", "a"), ChainEntry("gemini", "b"),
                                                              ChainEntry("anthropic", "c"))
    assert parse_chain("") == () and parse_chain(None) == ()
    with pytest.raises(ConfigError):
        parse_chain("gemini")


def test_single_provider_env_becomes_chain_of_one(monkeypatch):
    for k in ("EXTRACTION_TEXT_CHAIN", "EXTRACTION_VISION_CHAIN"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    monkeypatch.setenv("LLM_MODEL", "gemini-3.6-flash")
    s = load_settings(require_reset_token=False)
    assert s.text_chain == s.vision_chain == (ChainEntry("gemini", "gemini-3.6-flash"),)


def test_explicit_chains_override(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    monkeypatch.setenv("LLM_MODEL", "x")
    monkeypatch.setenv("EXTRACTION_TEXT_CHAIN", "gemini:a,gemini:b")
    monkeypatch.setenv("EXTRACTION_VISION_CHAIN", "gemini:v")
    s = load_settings(require_reset_token=False)
    assert [str(e) for e in s.text_chain] == ["gemini:a", "gemini:b"]
    assert [str(e) for e in s.vision_chain] == ["gemini:v"]


def test_chain_entry_without_credentials_refuses_startup(settings):
    with pytest.raises(ConfigError, match="anthropic:model-c is in the text chain but its API key is not set"):
        create_app(dataclasses.replace(settings, text_chain=(A, C)))


# ---------- worker integration: served provider recorded, fallback audited ----------

def test_worker_records_served_provider_and_audits_fallback(settings):
    from tests.test_pipeline import T01, rows, upload

    s = dataclasses.replace(settings, text_chain=(A, B))
    adapters = {A: Scripted(A, AVAILABILITY), B: Scripted(B, None)}
    with TestClient(create_app(s, adapter_for=adapters.__getitem__)) as c:
        c.settings = s
        r = upload(c, T01.read_bytes()).json()
        assert c.app.state.worker.wait_idle(30)
        ext = rows(c, "SELECT provider, model, cached FROM extractions WHERE run_id = ?", r["run_id"])[0]
        assert ext == {"provider": "gemini", "model": "model-b", "cached": 0}
        events = [json.loads(e["payload"]) for e in rows(
            c, "SELECT payload FROM audit_events WHERE run_id = ? AND type = 'extraction_called' ORDER BY event_id",
            r["run_id"])]
        assert events[0]["ok"] is False and events[0]["fallback"] is True
        assert events[0]["model"] == "model-a" and events[0]["fallback_to"] == "gemini:model-b"
        assert events[0]["error_kind"] == AVAILABILITY
        assert events[1]["ok"] is True and events[1]["model"] == "model-b"


def test_worker_chain_exhausted_is_error_with_retry(settings):
    from tests.test_pipeline import T01, rows, upload

    s = dataclasses.replace(settings, text_chain=(A, B))
    adapters = {A: Scripted(A, AVAILABILITY), B: Scripted(B, AVAILABILITY)}
    with TestClient(create_app(s, adapter_for=adapters.__getitem__)) as c:
        c.settings = s
        r = upload(c, T01.read_bytes()).json()
        assert c.app.state.worker.wait_idle(30)
        run = rows(c, "SELECT status, error_message FROM runs WHERE run_id = ?", r["run_id"])[0]
        assert run["status"] == "ERROR" and "all 2 providers unavailable" in run["error_message"]
        adapters[B].error_kind = None
        assert c.post(f"/api/runs/{r['run_id']}/retry").status_code == 202
        assert c.app.state.worker.wait_idle(30)
        assert rows(c, "SELECT model FROM extractions WHERE run_id = ?", r["run_id"]) == [{"model": "model-b"}]


# ---------- cache snapshot (DEC-013) ----------

def test_snapshot_loads_into_empty_cache_only(settings, tmp_path):
    from app.db.cache_snapshot import load_snapshot_if_empty

    snap = tmp_path / "snap.json"
    payload = InvoiceExtraction.model_validate({"total": {"value": 1.0, "confidence": 0.9}}).model_dump(mode="json")
    snap.write_text(json.dumps({"_meta": {}, "records": [
        {"file_name": "x.pdf", "file_hash": "h1", "provider": "gemini", "model": "m", "ingest_method": "text",
         "extracted_at": "2026-11-03T10:00:00+05:30", "payload": payload}]}), encoding="utf-8")
    s = dataclasses.replace(settings, cache_snapshot=snap)
    with TestClient(create_app(s)):
        pass
    conn = connect(s.database_path)
    assert [r[0] for r in conn.execute("SELECT file_hash FROM extraction_cache")] == ["h1"]
    assert load_snapshot_if_empty(conn, snap) == 0          # not empty: untouched
    conn.close()
