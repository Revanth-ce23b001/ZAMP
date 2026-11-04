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
        if pages.method == "vision":                 # the scan (T-05) has no text to match on
            r = next(r for r in self.records if r["ingest_method"] == "vision")
            return ExtractionResult(schema.model_validate(r["payload"]), self.provider, self.model, Usage(calls=1), 1)
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
    STATE["db"] = tmp / "e2e.db"
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
    # Day 5B display pacing: every rules stage now shows Running for at least the frontend minimum
    # (350 ms), while the backend's recorded stage timings stay as fast as they were.
    for s in ["VALIDATE", "VENDOR_BANK", "DUPLICATE", "PO_MATCH", "DECIDE"]:
        ts = [t for t in transitions if t[0] == s]
        assert any(t[1] == "RUNNING" for t in ts), (s, ts)
        held = next(t[2] for t in ts if t[1] == "PASSED") - next(t[2] for t in ts if t[1] == "RUNNING")
        assert held >= 340, (s, held)
    events = httpx.get(f"{API}/api/runs/{run_id}/audit").json()["events"]
    started = {e["payload"]["stage"]: e["created_at"] for e in events if e["type"] == "stage_started"}
    done = {e["payload"]["stage"]: e["created_at"] for e in events if e["type"] == "stage_completed"}
    from datetime import datetime
    for s in ["VALIDATE", "VENDOR_BANK", "DUPLICATE", "PO_MATCH", "DECIDE"]:
        if s in started and s in done:
            gap = (datetime.fromisoformat(done[s]) - datetime.fromisoformat(started[s])).total_seconds()
            assert gap < 0.35, (s, gap)                       # no backend delay was added

    # Expanding a stage shows its evidence (fetched after the decision).
    page.locator('[data-stage="PO_MATCH"] button').click()
    page.get_by_text("cumulative_po_value").wait_for(timeout=10000)
    page.screenshot(path=str(SHOTS / "4_t01_po_match_evidence.png"), full_page=True)

    # The PDF preview is served from the backend.
    assert page.locator("iframe[title='Invoice PDF']").get_attribute("src") == f"{API}/api/runs/{run_id}/file"
    page.close()
    STATE["t01_run_id"] = run_id


def test_same_file_lands_on_existing_run(browser):
    """FR-004 / §10.3: re-uploading a processed file opens its decision screen with the banner."""
    page = browser.new_page()
    page.goto(f"{WEB}/work-queue")
    page.get_by_test_id("upload-input").set_input_files(str(T01))
    page.wait_for_url("**/runs/*?existing=1", timeout=20000)
    run_id = page.url.split("/runs/")[1].split("?")[0]
    assert run_id == STATE["t01_run_id"]
    page.get_by_text("This exact file was already processed").wait_for(timeout=10000)
    page.get_by_test_id("decision-header").wait_for(timeout=20000)
    page.screenshot(path=str(SHOTS / "5_existing_run.png"), full_page=True)
    page.close()


# ======================================================================= decision details (§10.5)

from tests.conftest import fixture_records  # noqa: E402

ACCOUNTS = [v["bank_account_number"] for v in fixture_records("vendors.json")] + ["50100045671234"]
PHONES = [v["trusted_contact_phone"] for v in fixture_records("vendors.json")]
TABS = [("Extracted data", "tab-extracted"), ("Vendor & bank", "tab-vendor"), ("Duplicates", "tab-duplicates"),
        ("PO match", "tab-po"), ("Evidence", "tab-evidence"), ("Audit", "tab-audit")]


def run_sample(browser, prefix):
    """Run a sample from the launcher, let the live screen finish, then open the decision."""
    page = browser.new_page(viewport={"width": 1400, "height": 1100})
    page.goto(f"{WEB}/work-queue")
    page.select_option("#sample", next((BACKEND_DIR / "samples").glob(f"{prefix}_*.pdf")).name)
    page.get_by_role("button", name="Run").click()
    page.wait_for_url("**/runs/*/live", timeout=20000)
    page.get_by_test_id("view-decision").click(timeout=40000)
    page.get_by_test_id("decision-header").wait_for(timeout=20000)
    return page


def check_decision_screen(page, status, invoice_no, vendor, total, decided_by):
    """Every element PRD §10.5 lists, on one run. Returns the page HTML."""
    header = page.get_by_test_id("decision-header").inner_text()
    assert page.get_by_test_id("decision-status").first.inner_text() == status
    for part in (invoice_no, vendor, total, "R-", f"Decided by {decided_by}", "Policy"):
        assert part in header, (part, header)
    page.get_by_test_id("why-panel").wait_for()
    for label, testid in TABS:
        page.get_by_role("tab", name=label).click()
        page.get_by_test_id(testid).wait_for(timeout=10000)
    page.get_by_role("tab", name="Extracted data").click()
    assert page.get_by_test_id("line-items").locator("tbody tr").count() >= 1
    assert page.get_by_test_id("arithmetic").locator("tbody tr").count() >= 4
    page.get_by_role("tab", name="Evidence").click()
    assert page.get_by_test_id("evidence-row").count() > 5
    page.get_by_role("tab", name="Audit").click()
    assert "Decision:" in page.get_by_test_id("tab-audit").inner_text()
    assert page.locator("iframe[title='Invoice PDF']").count() == 1
    assert (page.get_by_test_id("review-panel").count() == 1) is (status == "HOLD")
    html = page.content()
    for acct in ACCOUNTS:
        assert acct not in html, "full account number in page"
    return html


def test_t01_decision_screen(browser):
    page = browser.new_page(viewport={"width": 1400, "height": 1100})
    page.goto(f"{WEB}/runs/{STATE['t01_run_id']}")
    page.get_by_test_id("decision-header").wait_for(timeout=20000)
    html = check_decision_screen(page, "APPROVE", "SLST/2026/0512", "Sri Lakshmi Steel Traders", "₹1,13,850.00", "Rules")
    assert "All checks passed" in page.get_by_test_id("why-panel").inner_text()
    page.get_by_role("tab", name="Extracted data").click()
    rows = page.get_by_test_id("arithmetic").inner_text()
    assert "E4" in rows and "Failed" not in rows
    assert page.get_by_test_id("security-banner").count() == 0
    assert not any(ph in html for ph in PHONES)                  # no trusted contact on a clean run
    page.get_by_role("tab", name="PO match").click()
    lines = page.get_by_test_id("po-lines").inner_text()
    assert "alias" in lines and "code" in lines                   # T-01 line 1 pairs by alias
    page.screenshot(path=str(SHOTS / "9_t01_decision.png"), full_page=True)
    page.close()


def test_t02_decision_screen_po_tab(browser):
    page = run_sample(browser, "T02")
    check_decision_screen(page, "HOLD", "INV-SL-0489", "Sri Lakshmi Steel Traders", "₹1,18,000.00", "Rules")
    why = page.get_by_test_id("why-panel").inner_text()
    assert "Taxable value ₹1,00,000 exceeds remaining ₹80,000 (allowed overrun ₹1,000) on PO-1041." in why
    assert "TMT-20: 220 approved + 100 invoiced exceeds PO qty 300 by 20." in why
    page.get_by_role("tab", name="PO match").click()
    assert "₹80,000.00" in page.get_by_test_id("po-remaining").inner_text()           # remaining, not 81,000
    assert "₹1,000.00" in page.get_by_test_id("po-allowed-overrun").inner_text()      # separate value
    assert "₹3,00,000.00" in page.get_by_test_id("po-po-total").inner_text()
    assert "₹2,20,000.00" in page.get_by_test_id("po-previously-approved").inner_text()
    assert "300 / 220 / 100 / 20" in page.get_by_test_id("po-lines").inner_text()
    page.get_by_role("tab", name="Duplicates").click()
    assert "Similar invoice exists: INV-SL-0467" in page.get_by_test_id("tab-duplicates").inner_text()
    page.screenshot(path=str(SHOTS / "10_t02_po_match.png"), full_page=True)
    page.close()


def test_t03_decision_screen_and_bank_override(browser):
    """§10.5 security banner and vendor tab, then T-23/T-22 through the §10.6 review panel."""
    page = run_sample(browser, "T03")
    check_decision_screen(page, "HOLD", "CPPL/7781", "Coromandel Packaging Pvt Ltd", "₹49,560.00", "Rules")
    banner = page.get_by_test_id("security-banner").inner_text()
    assert "Verify with Meena Raghavan, +91 98400 11223 (vendor master)" in banner
    assert "Do not use contact details printed on this invoice" in banner
    page.get_by_role("tab", name="Vendor & bank").click()
    vendor = page.get_by_test_id("tab-vendor")
    text = vendor.inner_text()
    assert "ICIC0005678" in text and "HDFC0005512" in text           # IFSC in full (DEC-001)
    assert text.count("XXXX XXXX 4471") == 2                         # masked on both sides
    assert "Format valid" in text and "Matches vendor master" in text and "IFSC differs" in text
    assert vendor.locator("tr[data-mismatch='1']").count() >= 1       # mismatched cell highlighted
    assert "Meena" not in text and "98400" not in text                # contact never in this tab
    page.screenshot(path=str(SHOTS / "11_t03_decision.png"), full_page=True)

    approve = page.get_by_role("button", name="Approve (override)")
    page.get_by_label("Reviewer name").fill("Ashrith")
    page.get_by_label("Reason").fill("Vendor confirmed the new account by phone.")
    page.get_by_test_id("ack-BANK_DETAILS_MISMATCH").check()
    page.get_by_label("Verified with").fill("Meena Raghavan")
    page.get_by_label("Channel").select_option("phone on record")
    page.get_by_label("Outcome").select_option("not verified")
    assert approve.is_disabled()
    page.get_by_role("button", name="Record attempt (stays on HOLD)").click()
    page.get_by_text("Bank verification not confirmed: invoice remains on HOLD").wait_for(timeout=10000)
    assert page.get_by_test_id("decision-status").first.inner_text() == "HOLD"
    page.get_by_label("Outcome").select_option("verified")
    assert approve.is_enabled()
    approve.click()
    page.get_by_test_id("review-panel").wait_for(state="detached", timeout=15000)   # APPROVE: no override controls
    assert page.get_by_test_id("decision-status").first.inner_text() == "APPROVE"
    assert "Reviewer override" in page.get_by_test_id("decision-header").inner_text()
    page.get_by_role("tab", name="Audit").click()
    records = page.get_by_test_id("override-records").inner_text()
    assert "not verified" in records and "committed" in records
    page.screenshot(path=str(SHOTS / "12_t03_override_audit.png"), full_page=True)
    page.close()


def test_t04_decision_screen_and_confirm_po(browser):
    page = run_sample(browser, "T04")
    check_decision_screen(page, "HOLD", "NIT/1123", "Nandi IT Solutions Pvt Ltd", "₹3,30,400.00", "Rules")
    page.get_by_role("tab", name="PO match").click()
    cands = page.get_by_test_id("po-candidates").inner_text()
    assert "PO-1031" in cands and "100" in cands and "PO-1036" in cands and "70" in cands
    assert "(inferred)" in page.get_by_test_id("po-header").inner_text()
    page.get_by_label("Reviewer name").fill("Ashrith")
    page.get_by_label("Reason").fill("Requester confirmed PO-1031 by email.")
    page.get_by_label("PO-1031 (score 100, plausible)").check()
    page.get_by_role("button", name="Confirm PO").click()
    page.get_by_test_id("review-panel").wait_for(state="detached", timeout=15000)
    assert page.get_by_test_id("decision-status").first.inner_text() == "APPROVE"
    assert "Rules after reviewer PO confirmation" in page.get_by_test_id("decision-header").inner_text()
    page.get_by_role("tab", name="PO match").click()
    assert "(confirmed)" in page.get_by_test_id("po-header").inner_text()
    page.screenshot(path=str(SHOTS / "13_t04_confirmed.png"), full_page=True)
    page.close()


def test_t05_decision_screen_duplicates(browser):
    page = run_sample(browser, "T05")
    check_decision_screen(page, "REJECT", "INV-2041", "Godavari Chemicals Ltd", "₹1,06,200.00", "Rules")
    assert "Same vendor and invoice number as INV-2041 (12 Oct, APPROVE)." in page.get_by_test_id("why-panel").inner_text()
    page.get_by_role("tab", name="Duplicates").click()
    dup = page.get_by_test_id("tab-duplicates").inner_text().lower()      # headers are CSS upper-case
    for part in ("matched (confirmed)", "inv2041", "2026-10-12", "₹1,06,200.00", "solv-ipa × 5", "seeded history"):
        assert part in dup, part
    assert dup.count("solv-ipa × 5") == 2                                # this invoice's lines beside the original's
    page.screenshot(path=str(SHOTS / "14_t05_duplicates.png"), full_page=True)
    page.close()


def test_built_frontend_has_no_forbidden_labels(servers):
    """FR-034: these claims must not appear anywhere in the built frontend."""
    hits = []
    for f in (FRONTEND_DIR / ".next").rglob("*"):
        if f.is_file() and f.suffix in (".js", ".html", ".json", ".rsc", ".txt", ".css", ".body", ""):
            text = f.read_text(encoding="utf-8", errors="ignore").lower()
            for label in ("gst verified", "account verified", "goods received"):
                if label in text:
                    hits.append((str(f.relative_to(FRONTEND_DIR)), label))
    assert hits == []


# ======================================================================= Day 5B screens

def _rows(page):
    page.get_by_test_id("runs-table").wait_for()
    page.wait_for_function("() => !document.querySelector('[data-testid=skeleton-row]')")
    return page.get_by_test_id("run-row")


def _row_count(page, n):
    page.wait_for_function(f"() => document.querySelectorAll('[data-testid=run-row]').length === {n}")


def test_work_queue_kpis_and_filters(browser):
    """FR-113: KPIs, status chips and the reason-code filter agree with GET /api/runs."""
    runs = httpx.get(f"{API}/api/runs").json()
    assert len(runs) >= 5
    page = browser.new_page(viewport={"width": 1400, "height": 1100})
    page.goto(f"{WEB}/work-queue")
    _rows(page)
    _row_count(page, len(runs))
    for testid, status in (("kpi-approved", "APPROVE"), ("kpi-hold", "HOLD"), ("kpi-rejected", "REJECT")):
        assert page.get_by_test_id(testid).inner_text() == str(sum(r["status"] == status for r in runs))
    assert page.get_by_test_id("kpi-total").inner_text() == str(len(runs))
    page.screenshot(path=str(SHOTS / "15_work_queue.png"), full_page=True)

    for status in ("APPROVE", "HOLD", "REJECT"):
        page.get_by_test_id(f"chip-{status}").click()
        page.wait_for_url(f"**status={status}")
        expected = sorted(r["run_id"] for r in runs if r["status"] == status)
        _row_count(page, len(expected))
        rows = page.get_by_test_id("run-row")
        assert sorted(rows.nth(i).get_attribute("data-run-id") for i in range(rows.count())) == expected
    page.get_by_test_id("chip-ALL").click()

    # Reason-code filter, then combined with a status chip that excludes it.
    page.get_by_test_id("reason-filter").select_option("CONFIRMED_DUPLICATE")
    page.wait_for_url("**reason_code=CONFIRMED_DUPLICATE")
    expected = [r["run_id"] for r in runs if "CONFIRMED_DUPLICATE" in r["reason_codes"]]
    assert len(expected) == 1
    _row_count(page, 1)
    assert page.get_by_test_id("run-row").first.get_attribute("data-run-id") == expected[0]
    page.get_by_test_id("chip-APPROVE").click()
    page.get_by_test_id("empty-state").wait_for()
    assert "No runs match these filters" in page.get_by_test_id("empty-state").inner_text()

    # Deep link (as Reports uses), and a row click opens the decision screen.
    page.goto(f"{WEB}/work-queue?reason_code=CONFIRMED_DUPLICATE")
    _rows(page)
    _row_count(page, 1)
    page.get_by_test_id("run-row").first.click()
    page.wait_for_url(f"**/runs/{expected[0]}")
    page.get_by_test_id("decision-header").wait_for(timeout=20000)
    page.close()


def test_reset_shows_empty_queue_and_seed_balances(browser):
    """Reset through the public Next.js route; the queue is empty and PO-1041 shows its four separate
    seed values on both Reports and Purchase Orders."""
    r = httpx.post(f"{WEB}/api/demo/reset", json={"confirm": "RESET", "requester_label": "e2e"}, timeout=60)
    assert r.status_code == 200, r.text
    page = browser.new_page(viewport={"width": 1400, "height": 1100})
    page.goto(f"{WEB}/work-queue")
    page.get_by_test_id("empty-state").wait_for(timeout=20000)
    assert "No invoices processed yet" in page.get_by_test_id("empty-state").inner_text()
    assert page.get_by_test_id("kpi-total").inner_text() == "0"

    want = {"po_total": "₹3,00,000.00", "approved": "₹2,20,000.00", "remaining": "₹80,000.00",
            "allowed_overrun": "₹1,000.00"}
    for path in ("/reports", "/pos"):
        page.goto(f"{WEB}{path}")
        row = page.locator('[data-testid=po-row][data-po="PO-1041"]')
        row.wait_for(timeout=20000)
        got = {k: row.locator(f'[data-col="{k}"]').inner_text() for k in want}
        assert got == want, (path, got)
        page.screenshot(path=str(SHOTS / f"16_{path.strip('/')}_after_reset.png"), full_page=True)
    page.close()


def _db_metrics():
    """The Reports figures, computed straight from SQLite rather than through the API."""
    import sqlite3
    conn = sqlite3.connect(STATE["db"])
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT d.status, d.decided_by, d.reason_codes, l.total, r.duration_ms FROM decisions d "
                        "JOIN runs r ON r.run_id = d.run_id LEFT JOIN invoice_ledger l ON l.run_id = d.run_id "
                        "WHERE r.status = 'DECIDED'").fetchall()
    conn.close()
    n = len(rows)
    count = lambda s: sum(r["status"] == s for r in rows)                       # noqa: E731
    auto = sum(r["status"] == "APPROVE" and r["decided_by"] == "rules" for r in rows)
    reasons: dict = {}
    for r in rows:
        for c in json.loads(r["reason_codes"]):
            reasons[c] = reasons.get(c, 0) + 1
    avg = round(sum(r["duration_ms"] for r in rows) / n)
    return {"n": n, "approve": count("APPROVE"), "hold": count("HOLD"), "reject": count("REJECT"),
            "rate": f"{auto * 100 / n:.1f}%", "hold_paise": sum(r["total"] or 0 for r in rows if r["status"] == "HOLD"),
            "reasons": reasons, "avg_ms": avg}


def test_reports_match_the_database_after_reset(browser):
    """FR-111: after the reset, process T-01, T-03 and T-05; every Reports figure equals what the
    database holds."""
    from app.utils.money import money

    for prefix in ("T01", "T03", "T05"):
        name = next((BACKEND_DIR / "samples").glob(f"{prefix}_*.pdf")).name
        run_id = httpx.post(f"{API}/api/samples/{name}/process", timeout=30).json()["run_id"]
        end = time.time() + 60
        while httpx.get(f"{API}/api/runs/{run_id}").json()["status"] not in ("DECIDED", "ERROR"):
            assert time.time() < end
            time.sleep(0.5)
    db = _db_metrics()
    assert db["n"] == 3

    page = browser.new_page(viewport={"width": 1400, "height": 1100})
    page.goto(f"{WEB}/reports")
    page.get_by_test_id("m-processed").wait_for(timeout=20000)
    text = lambda t: page.get_by_test_id(t).inner_text()                       # noqa: E731
    assert text("demo-label") == "Demo dataset: synthetic invoices"
    assert (text("m-processed"), text("m-approved"), text("m-hold"), text("m-rejected")) == \
           (str(db["n"]), str(db["approve"]), str(db["hold"]), str(db["reject"]))
    assert text("m-auto-rate") == db["rate"]
    assert text("m-value-hold") == money(db["hold_paise"])["display"]
    avg = db["avg_ms"]
    assert text("m-avg-time") == (f"{avg} ms" if avg < 1000 else f"{avg / 1000:.1f} s")
    rows = page.get_by_test_id("exception-row")
    got = {rows.nth(i).get_attribute("data-code"): int(rows.nth(i).get_by_test_id("exception-count").inner_text())
           for i in range(rows.count())}
    assert got == db["reasons"]
    page.screenshot(path=str(SHOTS / "17_reports.png"), full_page=True)

    # A reason opens the Work Queue filtered by it; a PO opens it filtered by that PO.
    code = next(iter(db["reasons"]))
    page.locator(f'[data-testid=exception-row][data-code="{code}"]').click()
    page.wait_for_url(f"**/work-queue?reason_code={code}")
    _rows(page)
    _row_count(page, db["reasons"][code])
    page.goto(f"{WEB}/reports")
    page.locator('[data-testid=po-row][data-po="PO-1042"] a').click()
    page.wait_for_url("**/work-queue?po_number=PO-1042")
    page.get_by_test_id("po-filter").wait_for()
    _rows(page)
    _row_count(page, len(httpx.get(f"{API}/api/runs", params={"po_number": "PO-1042"}).json()))
    page.close()


def test_vendors_page_and_response_have_no_contact_details(browser):
    vendors = fixture_records("vendors.json")
    secrets = PHONES + ACCOUNTS + [v["trusted_contact_name"] for v in vendors] + \
        [v["trusted_contact_email"] for v in vendors if v.get("trusted_contact_email")]
    body = httpx.get(f"{API}/api/vendors").text
    for s in secrets:
        assert s not in body
    for key in ("phone", "email", "trusted_contact"):
        assert key not in body

    page = browser.new_page(viewport={"width": 1400, "height": 1000})
    page.goto(f"{WEB}/vendors")
    page.get_by_test_id("vendor-row").first.wait_for(timeout=20000)
    assert page.get_by_test_id("vendor-row").count() == len(vendors)
    caption = page.get_by_test_id("no-contact-caption")
    assert caption.is_visible() and "No trusted-contact details here" in caption.inner_text()
    html = page.content()
    for s in secrets:
        assert s not in html
    page.screenshot(path=str(SHOTS / "18_vendors.png"), full_page=True)
    page.close()


def test_policy_page_renders_every_key_and_code(browser):
    import yaml

    from app.rules.reason_codes import REGISTRY
    data = yaml.safe_load((BACKEND_DIR / "app" / "policy.yaml").read_text(encoding="utf-8"))
    keys = list(data["assumptions"]) + list(data["other_assumptions"]) + [f"p1_rules.{k}" for k in data["p1_rules"]]
    assert len(REGISTRY) == 17

    page = browser.new_page(viewport={"width": 1400, "height": 1000})
    page.goto(f"{WEB}/policy")
    page.get_by_test_id("assumptions-table").wait_for(timeout=20000)
    shown = page.get_by_test_id("policy-key")
    assert sorted(shown.nth(i).get_attribute("data-key") for i in range(shown.count())) == sorted(keys)
    for k, entry in data["assumptions"].items():
        row = page.locator(f'[data-testid=policy-key][data-key="{k}"]').inner_text()
        assert entry["meaning"] in row and "[PA]" in row, k
    codes = page.get_by_test_id("reason-code")
    assert sorted(codes.nth(i).get_attribute("data-code") for i in range(codes.count())) == sorted(REGISTRY)
    for code, rc in REGISTRY.items():
        row = page.locator(f'[data-testid=reason-code][data-code="{code}"]').inner_text()
        assert rc.severity.value in row and f"{rc.stage} · " in row, code
    assert page.get_by_test_id("not-verified").locator("tbody tr").count() == 5
    scope = page.get_by_test_id("scope").inner_text()
    for part in ("Two-way match", "INR", "GST", "Sequential"):
        assert part in scope
    assert page.get_by_test_id("policy-version").inner_text() == httpx.get(f"{API}/api/meta").json()["policy_version"]
    assert "DEC-016" in page.get_by_test_id("reset-history-note").inner_text()
    assert "350 ms" in page.get_by_test_id("stepper-note").inner_text()
    page.screenshot(path=str(SHOTS / "19_policy.png"), full_page=True)
    page.close()


# ======================================================================= Day 8: measured accuracy

def test_accuracy_page_renders_both_tables_and_footer(browser):
    body = httpx.get(f"{API}/api/accuracy").json()
    page = browser.new_page(viewport={"width": 1400, "height": 1000})
    page.goto(f"{WEB}/accuracy")
    page.get_by_test_id("accuracy-fields").wait_for(timeout=20000)
    assert page.get_by_role("heading", name="Measured accuracy").count() == 1
    assert page.get_by_test_id("accuracy-field").count() == len(body["field_accuracy"])
    assert page.get_by_test_id("accuracy-decision").count() == body["decision_accuracy"]["total"]
    totals = body["field_totals"]
    assert page.get_by_test_id("acc-fields-headline").inner_text() == f"{totals['correct']}/{totals['total']}"
    d = body["decision_accuracy"]
    assert page.get_by_test_id("acc-decisions-headline").inner_text() == f"{d['correct']}/{d['total']}"
    footer = page.get_by_test_id("accuracy-footer").inner_text().lower()
    for part in (body["_meta"]["policy_version"][:12], f"{body['_meta']['sample_size']} samples", "gemini-3.6-flash",
                 "synthetic demo dataset"):
        assert part in footer, part
    if totals["unresolved"]:
        assert page.get_by_test_id("accuracy-unresolved").is_visible()          # unresolved shown, not hidden
    page.screenshot(path=str(SHOTS / "20_accuracy.png"), full_page=True)
    page.close()
