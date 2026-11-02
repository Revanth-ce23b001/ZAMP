"""Day 2B: upload validation, idempotency, worker stages 1-2, ingest, cache, failure and retry.
All offline: the provider is replaced by a fake adapter. Live provider tests are in
test_extraction_live.py."""
import dataclasses
import io
import json
import logging

import pymupdf
import pytest
from fastapi.testclient import TestClient

from app.db.connection import connect
from app.main import create_app
from app.policy import load_policy
from app.services.extraction.base import ExtractionError, ExtractionResult, Usage
from app.services.extraction.schema import InvoiceExtraction
from app.services.ingest import ingest
from tests.conftest import TEST_TOKEN

SAMPLES = sorted((p for p in (__import__("pathlib").Path(__file__).resolve().parents[1] / "samples").glob("*.pdf")))
T01 = next(p for p in SAMPLES if p.name.startswith("T01_"))
T05 = next(p for p in SAMPLES if p.name.startswith("T05_"))
FAKE_KEY = "AIzaFAKE-secret-key-value-1234567890"


class FakeAdapter:
    provider, model = "fake", "fake-model-1"

    def __init__(self, fail_times: int = 0, message: str = "simulated provider outage"):
        self.calls = 0
        self.fail_times = fail_times
        self.message = message

    def extract(self, pages, schema=InvoiceExtraction):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise ExtractionError(self.message, Usage(calls=2))
        ex = InvoiceExtraction.model_validate({
            "invoice_number": {"value": f"FAKE-{len(pages.texts)}-{pages.method}", "confidence": 0.99},
            "total": {"value": 100.0, "confidence": 0.95},
        })
        return ExtractionResult(ex, self.provider, self.model, Usage(input_tokens=10, output_tokens=5, calls=1), 1)


@pytest.fixture
def fake():
    return FakeAdapter()


def make_client(settings, adapter):
    return TestClient(create_app(settings, adapter_factory=lambda: adapter))


@pytest.fixture
def api(settings, fake):
    s = dataclasses.replace(settings, gemini_api_key=FAKE_KEY)
    with make_client(s, fake) as c:
        c.settings, c.fake = s, fake
        yield c


def upload(client, data: bytes, name="x.pdf", ctype="application/pdf"):
    return client.post("/api/invoices", files={"file": (name, data, ctype)})


def rows(client, sql, *args):
    conn = connect(client.settings.database_path)
    try:
        return [dict(r) for r in conn.execute(sql, args)]
    finally:
        conn.close()


def wait(client):
    assert client.app.state.worker.wait_idle(30)


def pdf_bytes(pages=1, encrypt=False) -> bytes:
    doc = pymupdf.open()
    for i in range(pages):
        doc.new_page().insert_text((72, 72), f"Invoice page {i + 1} " + "x" * 80)
    if encrypt:
        out = doc.tobytes(encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="user", owner_pw="owner")
    else:
        out = doc.tobytes()
    doc.close()
    return out


# ---------- upload validation (FR-001) ----------

PNG = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde"
       b"\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82")


@pytest.mark.parametrize("case, data_fn, name, ctype, status, code, message", [
    ("png", lambda: PNG, "scan.png", "image/png", 400, "not_pdf", "Only PDF files are supported"),
    ("renamed txt", lambda: b"Invoice INV-1 total 100\n" * 20, "invoice.pdf", "application/pdf", 400, "not_pdf",
     "file is not a PDF"),
    ("encrypted", lambda: pdf_bytes(encrypt=True), "locked.pdf", "application/pdf", 400, "encrypted",
     "Encrypted PDFs are not supported"),
    ("11 MB", lambda: pdf_bytes() + b"\n%" + b"0" * (11 * 1024 * 1024), "big.pdf", "application/pdf", 413,
     "too_large", "File exceeds 10 MB"),
    ("6 pages", lambda: pdf_bytes(pages=6), "long.pdf", "application/pdf", 400, "too_many_pages",
     "PDF has more than 5 pages"),
])
def test_invalid_upload_rejected_without_run(api, case, data_fn, name, ctype, status, code, message):
    r = upload(api, data_fn(), name, ctype)
    assert r.status_code == status, (case, r.text)
    assert r.json()["error"] == code
    assert message in r.json()["message"]
    assert rows(api, "SELECT COUNT(*) AS n FROM runs")[0]["n"] == 0
    assert [p for p in api.settings.uploads_dir.iterdir() if p.suffix == ".pdf"] == []
    assert api.fake.calls == 0


def test_pdf_mime_with_png_bytes_rejected(api):
    assert upload(api, PNG, "scan.pdf", "application/pdf").json()["error"] == "not_pdf"


def test_five_pages_accepted(api):
    r = upload(api, pdf_bytes(pages=5))
    assert r.status_code == 201 and r.json()["existing"] is False


# ---------- idempotency (FR-003, FR-004) and run creation (FR-005) ----------

def test_same_file_twice_is_one_run(api):
    data = T01.read_bytes()
    first = upload(api, data, T01.name)
    wait(api)
    second = upload(api, data, "renamed-copy.pdf")
    wait(api)
    assert first.status_code == 201 and first.json()["existing"] is False
    assert second.status_code == 200 and second.json() == {**first.json(), "existing": True}
    assert rows(api, "SELECT COUNT(*) AS n FROM runs")[0]["n"] == 1
    assert api.fake.calls == 1                      # no second provider call
    # The sample endpoint goes through the same hashing: same run, not a new one.
    third = api.post(f"/api/samples/{T01.name}/process")
    assert third.json()["run_id"] == first.json()["run_id"] and third.json()["existing"] is True


def test_run_created_queued_with_sequential_numbers(api, monkeypatch):
    monkeypatch.setattr(api.app.state.worker, "enqueue", lambda run_id: None)   # keep runs QUEUED
    one = pdf_bytes(1)          # PyMuPDF writes a random /ID, so build each file once
    a = upload(api, one).json()
    b = upload(api, pdf_bytes(2)).json()
    assert (a["run_number"], a["run_display"], b["run_number"]) == (1, "R-0001", 2)
    run = rows(api, "SELECT * FROM runs WHERE run_id = ?", a["run_id"])[0]
    assert run["status"] == "QUEUED"
    assert run["file_path"].endswith(f"{run['file_hash']}.pdf")
    assert (api.settings.uploads_dir / f"{run['file_hash']}.pdf").read_bytes() == one
    assert rows(api, "SELECT type FROM audit_events WHERE run_id = ?", a["run_id"]) == [{"type": "run_created"}]


def test_samples_listed_and_processed_through_upload_path(api):
    listed = api.get("/api/samples").json()
    assert sorted(s["name"] for s in listed) == [p.name for p in SAMPLES]
    assert len(listed) == 15
    r = api.post(f"/api/samples/{T05.name}/process")
    assert r.status_code == 201 and r.json()["existing"] is False
    wait(api)
    run = rows(api, "SELECT * FROM runs WHERE run_id = ?", r.json()["run_id"])[0]
    assert run["file_name"] == T05.name and run["ingest_method"] == "vision"


@pytest.mark.parametrize("name", ["nope.pdf", "..%2F..%2Fapp%2Fpolicy.yaml", "SHA256SUMS"])
def test_unknown_sample_404(api, name):
    assert api.post(f"/api/samples/{name}/process").status_code == 404


# ---------- worker stages 1-2 ----------

def test_worker_runs_ingest_and_extract_then_stays_processing(api):
    r = upload(api, T01.read_bytes(), T01.name).json()
    wait(api)
    run = rows(api, "SELECT * FROM runs WHERE run_id = ?", r["run_id"])[0]
    assert run["status"] == "PROCESSING"            # stages 3-7 arrive on Day 3
    stages = json.loads(run["stage_results"])
    assert [(s["stage"], s["status"]) for s in stages] == [("INGEST", "PASSED"), ("EXTRACT", "PASSED")]
    assert stages[0]["summary"] == "Digital PDF, text layer read (1 page)"
    assert run["ingest_method"] == "text"
    types = [e["type"] for e in rows(api, "SELECT type FROM audit_events WHERE run_id = ? ORDER BY event_id", r["run_id"])]
    assert types == ["run_created", "stage_started", "stage_completed", "stage_started",
                     "extraction_called", "stage_completed"]
    ext = rows(api, "SELECT * FROM extractions WHERE run_id = ?", r["run_id"])[0]
    assert ext["cached"] == 0 and ext["provider"] == "fake"
    InvoiceExtraction.model_validate_json(ext["payload"])


def test_runs_are_processed_one_at_a_time_in_order(settings):
    import threading

    active, peak, order = 0, 0, []
    lock = threading.Lock()

    class Slow(FakeAdapter):
        def extract(self, pages, schema=InvoiceExtraction):
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
            __import__("time").sleep(0.05)
            order.append(len(pages.texts))
            with lock:
                active -= 1
            return super().extract(pages, schema)

    with make_client(settings, Slow()) as c:
        for n in (1, 2, 3, 4):
            c.post("/api/invoices", files={"file": ("x.pdf", pdf_bytes(n), "application/pdf")})
        assert c.app.state.worker.wait_idle(30)
    assert peak == 1 and order == [1, 2, 3, 4]


# ---------- ingest classification (FR-010, FR-011, A-24) ----------

def test_only_t05_is_classified_as_scan():
    policy = load_policy()
    methods = {p.name: ingest(p, policy.assumption("A-24"), 200).pages.method for p in SAMPLES}
    assert len(methods) == 15
    assert [n for n, m in methods.items() if m == "vision"] == [T05.name]
    assert all(m == "text" for n, m in methods.items() if n != T05.name)


def test_scan_rendered_at_200_dpi():
    res = ingest(T05, 50, 200)
    assert res.summary == "Scanned document, sent to vision model (1 page)"
    pix = pymupdf.Pixmap(res.pages.images[0])
    page = pymupdf.open(T05)[0]
    assert abs(pix.width - page.rect.width * 200 / 72) <= 2


def test_ingest_failure_is_error(api, monkeypatch):
    from app.services import worker as worker_mod
    from app.services.ingest import IngestError

    def boom(*a, **k):
        raise IngestError("File could not be read: simulated")

    monkeypatch.setattr(worker_mod, "ingest", boom)
    r = upload(api, pdf_bytes()).json()
    wait(api)
    run = rows(api, "SELECT * FROM runs WHERE run_id = ?", r["run_id"])[0]
    assert run["status"] == "ERROR" and "could not be read" in run["error_message"]
    assert rows(api, "SELECT COUNT(*) AS n FROM decisions")[0]["n"] == 0


# ---------- cache (FR-014, DEC-005) ----------

def reset(client):
    return client.post("/api/demo/reset", json={"confirm": "RESET", "requester_label": "pytest"},
                       headers={"X-Demo-Reset-Token": TEST_TOKEN})


def test_cache_hit_after_reset_makes_no_provider_call(api):
    data = T01.read_bytes()
    first = upload(api, data, T01.name).json()
    wait(api)
    assert api.fake.calls == 1
    assert rows(api, "SELECT COUNT(*) AS n FROM extraction_cache")[0]["n"] == 1

    assert reset(api).status_code == 200            # runs deleted, cache kept
    assert rows(api, "SELECT COUNT(*) AS n FROM runs")[0]["n"] == 0
    second = upload(api, data, T01.name).json()
    wait(api)
    assert second["existing"] is False and second["run_id"] != first["run_id"]
    assert api.fake.calls == 1                      # no provider call
    ext = rows(api, "SELECT * FROM extractions WHERE run_id = ?", second["run_id"])[0]
    assert ext["cached"] == 1 and ext["provider"] == "fake"
    stages = json.loads(rows(api, "SELECT stage_results FROM runs WHERE run_id = ?", second["run_id"])[0]["stage_results"])
    assert stages[1]["summary"].endswith("(cached)")
    types = [e["type"] for e in rows(api, "SELECT type FROM audit_events WHERE run_id = ?", second["run_id"])]
    assert "extraction_cached" in types and "extraction_called" not in types


def test_cache_disabled_always_calls_provider_and_writes_no_cache(settings, fake):
    s = dataclasses.replace(settings, extraction_cache_enabled=False)
    with make_client(s, fake) as c:
        c.settings = s
        data = T01.read_bytes()
        upload(c, data)
        assert c.app.state.worker.wait_idle(30)
        reset(c)
        upload(c, data)
        assert c.app.state.worker.wait_idle(30)
        assert fake.calls == 2
        assert rows(c, "SELECT COUNT(*) AS n FROM extraction_cache")[0]["n"] == 0


def test_cache_env_flag(monkeypatch):
    from app.config import load_settings

    monkeypatch.setenv("DEMO_RESET_TOKEN", "t")
    for value, enabled in (("off", False), ("0", False), ("false", False), ("on", True), (None, True)):
        if value is None:
            monkeypatch.delenv("EXTRACTION_CACHE", raising=False)
        else:
            monkeypatch.setenv("EXTRACTION_CACHE", value)
        assert load_settings().extraction_cache_enabled is enabled


def test_dummy_cache_row_survives_reset(api):
    conn = connect(api.settings.database_path)
    conn.execute("INSERT INTO extraction_cache VALUES ('dummy', 'p', 'm', 'text', '{}', '2026-11-02T10:00:00+05:30')")
    conn.close()
    assert reset(api).status_code == 200
    assert rows(api, "SELECT file_hash FROM extraction_cache") == [{"file_hash": "dummy"}]


# ---------- failure and retry (FR-018, FR-094) ----------

def test_provider_failure_is_error_and_retry_reuses_run(settings):
    flaky = FakeAdapter(fail_times=1)
    with make_client(settings, flaky) as c:
        c.settings = settings
        r = upload(c, T01.read_bytes()).json()
        assert c.app.state.worker.wait_idle(30)
        run = rows(c, "SELECT * FROM runs WHERE run_id = ?", r["run_id"])[0]
        assert run["status"] == "ERROR"
        assert "simulated provider outage" in run["error_message"]
        assert rows(c, "SELECT COUNT(*) AS n FROM decisions")[0]["n"] == 0
        assert rows(c, "SELECT COUNT(*) AS n FROM extractions")[0]["n"] == 0

        rr = c.post(f"/api/runs/{r['run_id']}/retry")
        assert rr.status_code == 202 and rr.json()["run_id"] == r["run_id"]
        assert c.app.state.worker.wait_idle(30)
        run = rows(c, "SELECT * FROM runs WHERE run_id = ?", r["run_id"])[0]
        assert run["status"] == "PROCESSING" and run["error_message"] is None
        assert rows(c, "SELECT COUNT(*) AS n FROM runs")[0]["n"] == 1
        assert len(rows(c, "SELECT * FROM extractions WHERE run_id = ?", r["run_id"])) == 1
        types = [e["type"] for e in rows(c, "SELECT type FROM audit_events WHERE run_id = ? ORDER BY event_id", r["run_id"])]
        assert "error" in types and "retry" in types
        assert flaky.calls == 2


def test_retry_only_for_error_runs(api):
    r = upload(api, T01.read_bytes()).json()
    wait(api)
    assert api.post(f"/api/runs/{r['run_id']}/retry").status_code == 409
    assert api.post("/api/runs/no-such-run/retry").status_code == 404


def test_unconfigured_provider_is_error_not_crash(settings):
    s = dataclasses.replace(settings, llm_provider=None, llm_model=None)
    with TestClient(create_app(s)) as c:
        c.settings = s
        r = upload(c, T01.read_bytes()).json()
        assert c.app.state.worker.wait_idle(30)
        run = rows(c, "SELECT status, error_message FROM runs WHERE run_id = ?", r["run_id"])[0]
        assert run == {"status": "ERROR", "error_message": "LLM provider is not configured (set LLM_PROVIDER and LLM_MODEL)"}


# ---------- secrets ----------

def test_api_key_never_in_responses_logs_or_audit(settings, caplog):
    caplog.set_level(logging.DEBUG)
    s = dataclasses.replace(settings, gemini_api_key=FAKE_KEY, llm_provider="gemini", llm_model="m")
    leaky = FakeAdapter(fail_times=99, message=f"401 bad key {FAKE_KEY} rejected")
    with make_client(s, leaky) as c:
        c.settings = s
        bodies = [upload(c, T01.read_bytes()).text, c.get("/api/samples").text, c.get("/api/meta").text]
        assert c.app.state.worker.wait_idle(30)
        run_id = json.loads(bodies[0])["run_id"]
        bodies.append(c.post(f"/api/runs/{run_id}/retry").text)
        assert c.app.state.worker.wait_idle(30)
        run = rows(c, "SELECT error_message FROM runs WHERE run_id = ?", run_id)[0]
        assert "[redacted]" in run["error_message"]
        db_dump = json.dumps(rows(c, "SELECT * FROM audit_events") + rows(c, "SELECT * FROM runs"))
    for text in bodies + [caplog.text, db_dump]:
        assert FAKE_KEY not in text
