"""Browser end-to-end (Day 3 exit condition, PRD §16): the launcher starts T-01 and the live screen
shows the stages arriving over SSE while the run is processing, then the decision and commit; a
re-upload of the same file lands on the existing run.

The backend is the real app (upload path, worker, ingest, rules engine, commit, SSE) on a real
uvicorn server; the frontend is a production Next.js build; Playwright/Chromium drives it.

No provider calls. The extraction cache is switched OFF so every run calls the "provider", and
the provider is a stand-in that returns the sample's real cached extraction (the shipped
snapshot) after ~2 s, about what a live Gemini call takes. That is what lets the browser show
Ingest and Extract running before the rules decide. Nothing in production code is delayed.

Skipped unless pytest is run with --run-e2e (it builds the frontend, ~1 minute).

    python -m pytest tests/test_e2e_browser.py --run-e2e -s
"""
import dataclasses
import json
import shutil
import subprocess
import time
from pathlib import Path

import httpx
import pytest

from app.services.extraction.base import ExtractionResult, Usage
from app.services.extraction.schema import InvoiceExtraction

pytestmark = pytest.mark.e2e
STATE: dict = {}
EXTRACTION_SECONDS = 2.0


class ReplayCached:
    """Stand-in provider: the real cached extraction for the page, after a realistic delay."""
    provider, model = "replay", "snapshot"

    def __init__(self, snapshot: Path):
        self.records = json.loads(snapshot.read_text(encoding="utf-8"))["records"]

    def extract(self, pages, schema=InvoiceExtraction):
        time.sleep(EXTRACTION_SECONDS)
        text = "\n".join(pages.texts)
        for r in self.records:
            number = (r["payload"]["invoice_number"] or {}).get("value")
            if number and number in text:
                return ExtractionResult(schema.model_validate(r["payload"]), self.provider, self.model, Usage(calls=1), 1)
        raise AssertionError("no cached extraction for this page")

BACKEND_DIR = Path(__file__).resolve().parents[1]
FRONTEND_DIR = BACKEND_DIR.parent / "frontend"
API_PORT, WEB_PORT = 8810, 3310
API, WEB = f"http://127.0.0.1:{API_PORT}", f"http://127.0.0.1:{WEB_PORT}"
SHOTS = BACKEND_DIR / "data" / "e2e"
T01 = next((BACKEND_DIR / "samples").glob("T01_*.pdf"))

# Record every status change of every stepper row, from the first page load onwards (client-side
# navigation keeps the same document, so this observer sees the live screen too).
RECORDER = """
window.__transitions = [];
new MutationObserver((muts) => {
  for (const m of muts) {
    const el = m.target;
    if (el.dataset && el.dataset.stage && m.attributeName === 'data-status')
      window.__transitions.push([el.dataset.stage, el.dataset.status, performance.now()]);
    for (const n of m.addedNodes || [])
      if (n.querySelectorAll) n.querySelectorAll('[data-stage]').forEach(e =>
        window.__transitions.push([e.dataset.stage, e.dataset.status, performance.now()]));
  }
}).observe(document, {subtree: true, childList: true, attributes: true, attributeFilter: ['data-status']});
"""


def wait_http(url, seconds=90):
    end = time.time() + seconds
    while time.time() < end:
        try:
            if httpx.get(url, timeout=2).status_code < 500:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.5)
    raise RuntimeError(f"{url} did not come up")


@pytest.fixture(scope="module")
def servers(tmp_path_factory):
    import os

    from app.config import Settings
    from app.main import create_app
    from tests.conftest import TEST_CHAIN
    from tests.test_live import LiveServer
    from tests.test_scenarios import SNAPSHOT

    tmp = tmp_path_factory.mktemp("e2e")
    node = shutil.which("node")
    next_bin = FRONTEND_DIR / "node_modules" / "next" / "dist" / "bin" / "next"
    if not node or not next_bin.exists():
        pytest.skip("frontend dependencies not installed")
    web_env = {**os.environ, "NEXT_PUBLIC_BACKEND_ORIGIN": API, "BACKEND_URL": API, "DEMO_RESET_TOKEN": "e2e-token"}
    build = subprocess.run([node, str(next_bin), "build"], cwd=FRONTEND_DIR, env=web_env,
                           capture_output=True, text=True, timeout=600)
    assert build.returncode == 0, build.stdout[-2000:] + build.stderr[-2000:]

    settings = Settings(database_path=tmp / "e2e.db", uploads_dir=tmp / "uploads", frontend_origin=WEB,
                        demo_reset_token="e2e-token", gemini_api_key="unused", text_chain=TEST_CHAIN,
                        vision_chain=TEST_CHAIN, cache_snapshot=None, extraction_cache_enabled=False)
    replay = ReplayCached(SNAPSHOT)
    api = LiveServer(create_app(settings, adapter_for=lambda e: replay))
    api.port = API_PORT
    api.server.config.port = API_PORT
    web = subprocess.Popen([node, str(next_bin), "start", "-p", str(WEB_PORT), "-H", "127.0.0.1"],
                           cwd=FRONTEND_DIR, env=web_env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        with api:
            wait_http(f"{API}/api/health")
            wait_http(f"{WEB}/work-queue")
            yield
    finally:
        web.terminate()
        try:
            web.wait(10)
        except subprocess.TimeoutExpired:
            web.kill()


@pytest.fixture(scope="module")
def browser(servers):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        b = pw.chromium.launch()
        yield b
        b.close()


STAGES = ["INGEST", "EXTRACT", "VALIDATE", "VENDOR_BANK", "DUPLICATE", "PO_MATCH", "DECIDE"]


def test_t01_runs_end_to_end_live_in_the_browser(browser):
    SHOTS.mkdir(parents=True, exist_ok=True)
    page = browser.new_page(viewport={"width": 1400, "height": 1000})
    page.add_init_script(RECORDER)
    page.goto(f"{WEB}/work-queue")
    page.select_option("#sample", T01.name)
    page.screenshot(path=str(SHOTS / "1_work_queue.png"))
    page.get_by_role("button", name="Run").click()

    page.wait_for_url("**/runs/*/live", timeout=20000)
    run_id = page.url.split("/runs/")[1].split("/")[0]
    # Mid-run: extraction is in flight, nothing decided yet.
    page.locator('[data-stage="EXTRACT"][data-status="RUNNING"]').wait_for(timeout=20000)
    assert page.locator('[data-stage="INGEST"]').get_attribute("data-status") == "PASSED"
    assert page.locator('[data-stage="VALIDATE"]').get_attribute("data-status") == "PENDING"
    assert page.get_by_test_id("decision-banner").count() == 0
    page.screenshot(path=str(SHOTS / "2_t01_live_extracting.png"), full_page=True)

    banner = page.get_by_test_id("decision-banner")
    banner.wait_for(timeout=30000)
    page.locator('[data-stage="COMMIT"][data-status="PASSED"]').wait_for(timeout=30000)
    page.screenshot(path=str(SHOTS / "3_t01_live_decided.png"), full_page=True)

    assert banner.inner_text().startswith("APPROVE")
    assert "Committed: PO-1042 remaining balance ₹30,000" in banner.inner_text()
    statuses = {s: page.locator(f'[data-stage="{s}"]').get_attribute("data-status") for s in STAGES}
    assert statuses == {s: "PASSED" for s in STAGES}

    # Live, not after the fact: Extract was shown Running for roughly the extraction time before
    # it turned Passed, and every stage ends Passed. Stages 3-7 are pure rules that finish within
    # milliseconds, so their Running state may be batched into the same render as Passed.
    transitions = page.evaluate("window.__transitions")
    ext = [t for t in transitions if t[0] == "EXTRACT"]
    running_at = next(t[2] for t in ext if t[1] == "RUNNING")
    passed_at = next(t[2] for t in ext if t[1] == "PASSED")
    assert (passed_at - running_at) / 1000 >= EXTRACTION_SECONDS * 0.8
    for s in STAGES:
        seq = [t[1] for t in transitions if t[0] == s]
        assert seq[0] == "PENDING" and seq[-1] == "PASSED", (s, seq)

    # Expanding a stage shows its evidence (fetched after the decision).
    page.locator('[data-stage="PO_MATCH"] button').click()
    page.get_by_text("cumulative_po_value").wait_for(timeout=10000)
    page.screenshot(path=str(SHOTS / "4_t01_po_match_evidence.png"), full_page=True)

    # The PDF preview is served from the backend.
    assert page.locator("iframe[title='Invoice PDF']").get_attribute("src") == f"{API}/api/runs/{run_id}/file"
    page.close()
    STATE["t01_run_id"] = run_id


def test_same_file_lands_on_existing_run(browser):
    page = browser.new_page()
    page.goto(f"{WEB}/work-queue")
    page.get_by_test_id("upload-input").set_input_files(str(T01))
    page.wait_for_url("**/runs/*/live?existing=1", timeout=20000)
    run_id = page.url.split("/runs/")[1].split("/")[0]
    assert run_id == STATE["t01_run_id"]
    page.get_by_text("This exact file was already processed").wait_for(timeout=10000)
    page.get_by_test_id("decision-banner").wait_for(timeout=20000)
    page.screenshot(path=str(SHOTS / "5_existing_run.png"), full_page=True)
    page.close()


def test_hold_run_shows_codes_and_commit_not_applicable(browser):
    page = browser.new_page()
    page.goto(f"{WEB}/work-queue")
    page.select_option("#sample", next((BACKEND_DIR / "samples").glob("T02_*.pdf")).name)
    page.get_by_role("button", name="Run").click()
    page.wait_for_url("**/runs/*/live", timeout=20000)
    banner = page.get_by_test_id("decision-banner")
    banner.wait_for(timeout=30000)
    text = banner.inner_text()
    assert text.startswith("HOLD") and "QUANTITY_EXCEEDS_PO" in text and "PO_BALANCE_EXCEEDED" in text
    assert page.locator('[data-stage="PO_MATCH"]').get_attribute("data-status") == "FAILED"
    assert page.locator('[data-stage="COMMIT"]').get_attribute("data-status") == "NOT_CHECKED"
    page.screenshot(path=str(SHOTS / "6_t02_hold.png"), full_page=True)
    page.close()
