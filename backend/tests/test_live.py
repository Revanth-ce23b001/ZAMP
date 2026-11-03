"""Day 3B: orchestration outcomes, SSE stream (FR-114), queue position, run detail and file."""
import dataclasses
import json
import threading

import pytest
from fastapi.testclient import TestClient

from app.db.connection import connect
from app.main import create_app
from app.services.extraction.base import ExtractionResult, Usage
from app.services.extraction.schema import InvoiceExtraction
from tests.test_pipeline import pdf_bytes, rows
from tests.test_scenarios import FILES, SNAPSHOT, NoProvider

STAGES = ["INGEST", "EXTRACT", "VALIDATE", "VENDOR_BANK", "DUPLICATE", "PO_MATCH", "DECIDE"]


class Gated:
    """Fake adapter that blocks until released, so tests can observe a run mid-flight."""
    provider, model = "fake", "gated"

    def __init__(self):
        self.release = threading.Event()
        self.entered = threading.Event()
        self.calls = 0

    def extract(self, pages, schema=InvoiceExtraction):
        self.calls += 1
        self.entered.set()
        assert self.release.wait(30), "test never released the adapter"
        ex = InvoiceExtraction.model_validate({"invoice_number": {"value": f"G-{self.calls}", "confidence": 0.9}})
        return ExtractionResult(ex, self.provider, self.model, Usage(calls=1), 1)


class LiveServer:
    """The app on a real uvicorn server in a background thread. Starlette's TestClient buffers a
    streaming response until it completes and cannot signal a client disconnect, so SSE is tested
    against a real socket with a streaming httpx client."""

    def __init__(self, app):
        import socket

        import uvicorn

        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        self.port = sock.getsockname()[1]
        sock.close()
        self.server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=self.port, log_level="warning",
                                                    lifespan="on"))
        self.thread = threading.Thread(target=self.server.run, daemon=True)
        self.app = app

    def __enter__(self):
        import time

        import httpx

        self.thread.start()
        for _ in range(200):
            if self.server.started:
                break
            time.sleep(0.05)
        self.http = httpx.Client(base_url=f"http://127.0.0.1:{self.port}", timeout=60)
        return self

    def __exit__(self, *exc):
        self.http.close()
        self.server.should_exit = True
        self.thread.join(10)

    # the subset of the TestClient interface the tests use
    def get(self, url, **kw):
        return self.http.get(url, **kw)

    def post(self, url, **kw):
        return self.http.post(url, **kw)

    def stream(self, method, url, **kw):
        return self.http.stream(method, url, **kw)


def make(settings, adapter, snapshot=True, live=False):
    s = dataclasses.replace(settings, cache_snapshot=SNAPSHOT if snapshot else None)
    app = create_app(s, adapter_for=lambda e: adapter)
    c = LiveServer(app) if live else TestClient(app)
    c.settings = s
    return c


def sse(client, run_id, last_event_id=None, stop_after=None):
    """Read the stream into [(id, event, data)]; comments are kept as ('', ':', text).
    stop_after(events) ends reading early: the client disconnects."""
    headers = {"Last-Event-ID": str(last_event_id)} if last_event_id is not None else {}
    events, cur = [], {}
    with client.stream("GET", f"/api/runs/{run_id}/events", headers=headers) as r:
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
        for line in r.iter_lines():
            if line.startswith(":"):
                events.append(("", ":", line))
                if stop_after and stop_after(events):
                    break
            elif line.startswith("id: "):
                cur["id"] = int(line[4:])
            elif line.startswith("event: "):
                cur["event"] = line[7:]
            elif line.startswith("data: "):
                cur["data"] = json.loads(line[6:])
            elif line == "" and "event" in cur:
                events.append((cur.get("id"), cur["event"], cur.get("data")))
                cur = {}
                if stop_after and stop_after(events):
                    break
    return events


def names(events):
    return [(e[1], (e[2] or {}).get("stage")) for e in events if e[1] != ":"]


def process(client, sample):
    r = client.post(f"/api/samples/{FILES[sample]}/process")
    assert r.status_code == 201, r.text
    assert client.app.state.worker.wait_idle(60)
    return r.json()["run_id"]


# ---------- orchestration (FR-005, FR-090) ----------

def test_t01_t02_t05_outcomes_and_consumption(settings):
    with make(settings, NoProvider()) as c:
        t01, t02, t05 = process(c, "T01"), process(c, "T02"), process(c, "T05")
        for rid, status in ((t01, "APPROVE"), (t02, "HOLD"), (t05, "REJECT")):
            run = rows(c, "SELECT status FROM runs WHERE run_id = ?", rid)[0]
            assert run["status"] == "DECIDED"
            assert rows(c, "SELECT status FROM decisions WHERE run_id = ?", rid)[0]["status"] == status
        assert sorted(json.loads(rows(c, "SELECT reason_codes FROM decisions WHERE run_id = ?", t02)[0]["reason_codes"])) \
            == ["PO_BALANCE_EXCEEDED", "QUANTITY_EXCEEDS_PO"]
        consumed = {rid: rows(c, "SELECT COUNT(*) AS n FROM po_consumption WHERE run_id = ?", rid)[0]["n"]
                    for rid in (t01, t02, t05)}
        assert consumed == {t01: 2, t02: 0, t05: 0}          # T-01: two PO lines, written once
        assert len(rows(c, "SELECT * FROM audit_events WHERE run_id = ? AND type = 'commit'", t01)) == 1
        assert rows(c, "SELECT * FROM audit_events WHERE run_id IN (?, ?) AND type LIKE 'commit%'", t02, t05) == []


# ---------- SSE ----------

def expected_sequence(final_commit=True):
    seq = [("run", None)]
    for s in STAGES:
        seq += [("stage_started", s), ("stage_completed", s)]
    seq.append(("decision", None))
    if final_commit:
        seq.append(("commit", None))
    return seq + [("end", None)]


def test_finished_run_replays_complete_sequence_then_closes(settings):
    with make(settings, NoProvider()) as c:
        rid = process(c, "T01")
        ev = sse(c, rid)
        assert names(ev) == expected_sequence()
        ids = [e[0] for e in ev if e[0]]
        assert ids == sorted(ids) and len(ids) == len(set(ids))
        data = {e[1]: e[2] for e in ev}
        assert data["decision"]["status"] == "APPROVE" and data["decision"]["reason_codes"] == []
        assert data["commit"]["outcome"] == "committed"
        assert data["commit"]["remaining"] == 3000000 and data["commit"]["remaining_display"] == "₹30,000.00"
        completed = {e[2]["stage"]: e[2] for e in ev if e[1] == "stage_completed"}
        assert all(completed[s]["status"] == "PASSED" for s in STAGES)
        assert completed["INGEST"]["summary"] == "Digital PDF, text layer read (1 page)"
        assert completed["EXTRACT"]["summary"].endswith("(cached)")


def test_hold_and_reject_streams_close_after_decision(settings):
    with make(settings, NoProvider()) as c:
        for sample, status in (("T02", "HOLD"), ("T05", "REJECT")):
            ev = sse(c, process(c, sample))
            assert names(ev) == expected_sequence(final_commit=False)
            assert next(e[2] for e in ev if e[1] == "decision")["status"] == status
        not_checked = sse(c, process(c, "T09"))
        dup = next(e[2] for e in not_checked if e[1] == "stage_completed" and e[2]["stage"] == "DUPLICATE")
        assert dup["status"] == "NOT_CHECKED" and dup["not_checked_reason"] == "Vendor not identified"


def test_live_follow_and_reconnect_lose_nothing(settings):
    gate = Gated()
    with make(settings, gate, snapshot=False, live=True) as c:
        rid = c.post("/api/invoices", files={"file": ("x.pdf", pdf_bytes(), "application/pdf")}).json()["run_id"]
        assert gate.entered.wait(10)
        # First connection: everything so far, then the client disconnects while extraction is
        # still blocked. The run completes while it is away.
        first = sse(c, rid, stop_after=lambda ev: any(e[1] == "stage_started" and e[2]["stage"] == "EXTRACT" for e in ev))
        assert rows(c, "SELECT status FROM runs WHERE run_id = ?", rid)[0]["status"] == "PROCESSING"
        last_id = max(e[0] for e in first if e[0])
        gate.release.set()
        assert c.app.state.worker.wait_idle(30)
        assert rows(c, "SELECT status FROM runs WHERE run_id = ?", rid)[0]["status"] == "DECIDED"
        # Reconnect with Last-Event-ID: only what happened after, then the stream closes.
        second = sse(c, rid, last_event_id=last_id)
        assert second[-1][1] == "end"
        got = [e for e in first + second if e[0]]
        full = [e for e in sse(c, rid) if e[0]]
        assert [e[0] for e in got] == [e[0] for e in full]            # no gap, no duplicate
        assert names(got) == [n for n in names(full)]


def test_connecting_mid_run_follows_live(settings):
    import time

    gate = Gated()
    with make(settings, gate, snapshot=False, live=True) as c:
        rid = c.post("/api/invoices", files={"file": ("x.pdf", pdf_bytes(), "application/pdf")}).json()["run_id"]
        assert gate.entered.wait(10)
        seen_at = {}

        def stop(ev):                       # record when each event reached the client
            seen_at.setdefault(len(ev), time.monotonic())
            return False

        threading.Timer(1.0, gate.release.set).start()          # releases while the client is attached
        start = time.monotonic()
        ev = sse(c, rid, stop_after=stop)
        assert names(ev)[-2:] == [("decision", None), ("end", None)]
        assert ("stage_completed", "DECIDE") in names(ev)
        # The replayed head arrived immediately; the rest only after the release: truly live.
        assert seen_at[1] - start < 0.9 and max(seen_at.values()) - start >= 1.0


def test_queue_position_for_second_run(settings):
    gate = Gated()
    with make(settings, gate, snapshot=False, live=True) as c:
        r1 = c.post("/api/invoices", files={"file": ("a.pdf", pdf_bytes(1), "application/pdf")}).json()["run_id"]
        r2 = c.post("/api/invoices", files={"file": ("b.pdf", pdf_bytes(2), "application/pdf")}).json()["run_id"]
        assert gate.entered.wait(10)
        assert c.get(f"/api/runs/{r2}").json()["queue_position"] == 1
        head = sse(c, r2, stop_after=lambda ev: any(e[1] == "queued" for e in ev))
        assert next(e[2] for e in head if e[1] == "queued") == {"position": 1}
        assert next(e[2] for e in head if e[1] == "run")["status"] == "QUEUED"
        assert rows(c, "SELECT status FROM runs WHERE run_id = ?", r2)[0]["status"] == "QUEUED"
        gate.release.set()
        assert c.app.state.worker.wait_idle(30)
        assert [r["status"] for r in rows(c, "SELECT status FROM runs ORDER BY run_number")] == ["DECIDED", "DECIDED"]
        a = rows(c, "SELECT started_at, completed_at FROM runs WHERE run_id = ?", r1)[0]
        b = rows(c, "SELECT started_at FROM runs WHERE run_id = ?", r2)[0]
        assert a["completed_at"] <= b["started_at"]              # strictly one after the other
        assert gate.calls == 2


def test_keepalive_comments(settings):
    gate = Gated()
    with make(settings, gate, snapshot=False, live=True) as c:
        c.app.state.sse_keepalive_seconds = 0.1
        rid = c.post("/api/invoices", files={"file": ("x.pdf", pdf_bytes(), "application/pdf")}).json()["run_id"]
        assert gate.entered.wait(10)
        ev = sse(c, rid, stop_after=lambda ev: sum(1 for e in ev if e[1] == ":") >= 2)
        assert [e[2] for e in ev if e[1] == ":"][:2] == [": keepalive", ": keepalive"]
        gate.release.set()
        assert c.app.state.worker.wait_idle(30)
        assert rows(c, "SELECT status FROM runs WHERE run_id = ?", rid)[0]["status"] == "DECIDED"


def test_error_run_streams_error_then_retry_continues(settings):
    from tests.test_pipeline import FakeAdapter

    flaky = FakeAdapter(fail_times=1)
    with make(settings, flaky, snapshot=False) as c:
        rid = c.post("/api/invoices", files={"file": ("x.pdf", pdf_bytes(), "application/pdf")}).json()["run_id"]
        assert c.app.state.worker.wait_idle(30)
        ev = sse(c, rid)
        assert names(ev)[-2:] == [("error", "EXTRACT"), ("end", None)]      # the error names its stage
        assert "simulated provider outage" in next(e[2] for e in ev if e[1] == "error")["message"]
        assert c.post(f"/api/runs/{rid}/retry").status_code == 202
        assert c.app.state.worker.wait_idle(30)
        ev = sse(c, rid)
        n = names(ev)
        assert ("retry", None) in n and n[-2:] == [("decision", None), ("end", None)]


def test_unknown_run_events_and_detail(settings):
    with make(settings, NoProvider()) as c:
        ev = sse(c, "no-such-run")
        assert ev[0][1] == "error"
        assert c.get("/api/runs/no-such-run").status_code == 404


# ---------- run detail and file (FR-110, FR-041, FR-044) ----------

def test_run_detail_masks_and_gates_trusted_contact(settings):
    from tests.conftest import fixture_records

    with make(settings, NoProvider()) as c:
        t01, t03 = process(c, "T01"), process(c, "T03")
        d1, d3 = c.get(f"/api/runs/{t01}").json(), c.get(f"/api/runs/{t03}").json()
        assert d1["status"] == "DECIDED" and d1["decision"]["status"] == "APPROVE" and d1["decision"]["committed"]
        assert d1["extraction"]["cached"] is True
        assert d1["extraction"]["fields"]["bank_account_number"]["value"] == "XXXX XXXX 1234"
        assert "trusted_contact" not in d1
        assert d3["decision"]["reason_codes"] == ["BANK_DETAILS_MISMATCH"]
        assert d3["trusted_contact"] == {"name": "Meena Raghavan", "phone": "+91 98400 11223",
                                         "email": "accounts@coromandelpack.example", "source": "vendor master"}
        text = c.get(f"/api/runs/{t01}").text + c.get(f"/api/runs/{t03}").text
        for v in fixture_records("vendors.json"):
            assert v["bank_account_number"] not in text
        assert "+91 98450 22114" not in c.get(f"/api/runs/{t01}").text      # V001 contact never exposed


def test_run_file_streams_pdf(settings):
    with make(settings, NoProvider()) as c:
        rid = process(c, "T01")
        r = c.get(f"/api/runs/{rid}/file")
        assert r.status_code == 200 and r.headers["content-type"] == "application/pdf"
        assert r.content.startswith(b"%PDF")
        assert c.get("/api/runs/nope/file").status_code == 404


def test_parallel_requests_on_a_real_server(settings):
    """Regression: a request's DB connection may be created on one threadpool thread and used on
    another; concurrent browser requests (run detail + audit + PDF) must not fail."""
    from concurrent.futures import ThreadPoolExecutor

    with make(settings, NoProvider(), live=True) as c:
        rid = process(c, "T01")
        paths = [f"/api/runs/{rid}", f"/api/runs/{rid}/audit", "/api/pos", "/api/vendors", "/api/dashboard",
                 f"/api/runs/{rid}/file", "/api/runs", "/api/policy"] * 6
        with ThreadPoolExecutor(max_workers=12) as pool:
            codes = list(pool.map(lambda p: c.get(p).status_code, paths))
        assert codes == [200] * len(paths)


def test_cors_allows_frontend_origin_for_sse(settings):
    with make(settings, NoProvider()) as c:
        rid = process(c, "T01")
        with c.stream("GET", f"/api/runs/{rid}/events", headers={"Origin": "http://localhost:3000"}) as r:
            assert r.headers["access-control-allow-origin"] == "http://localhost:3000"
        pre = c.options("/api/invoices", headers={"Origin": "http://localhost:3000",
                                                   "Access-Control-Request-Method": "POST"})
        assert pre.headers["access-control-allow-origin"] == "http://localhost:3000"
        other = c.get("/api/health", headers={"Origin": "http://evil.example"})
        assert "access-control-allow-origin" not in other.headers
