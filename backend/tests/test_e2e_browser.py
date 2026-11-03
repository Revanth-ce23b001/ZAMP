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
