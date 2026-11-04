"""PRD §13 scenario tests: every sample through the full pipeline (upload path, worker, stages
1-7, commit) in the PRD's run order, after a reset, against the shipped extraction cache
(DEC-014). The adapter raises if called, so these cost no provider calls.

Each run must produce exactly the status and reason codes §13 specifies: no more, no fewer.
"""
import dataclasses
import json

import pytest
from fastapi.testclient import TestClient

from app.config import BACKEND_DIR
from app.db.connection import connect
from app.main import create_app

SNAPSHOT = BACKEND_DIR / "seed" / "extraction_cache.json"
FILES = {r["file_name"][:4].rstrip("_"): r["file_name"]
         for r in json.loads(SNAPSHOT.read_text(encoding="utf-8"))["records"]}

# (sample, PRD status, PRD codes, required note fragment) in PRD §13 order
EXPECTED = [
    ("T01", "APPROVE", [], None),
    ("T02", "HOLD", ["QUANTITY_EXCEEDS_PO", "PO_BALANCE_EXCEEDED"], "Similar invoice exists: INV-SL-0467"),
    ("T03", "HOLD", ["BANK_DETAILS_MISMATCH"], None),
    ("T04", "HOLD", ["PO_INFERRED"], None),
    ("T05", "REJECT", ["CONFIRMED_DUPLICATE"], None),
    ("T06", "APPROVE", [], "Similar invoice exists: INV-0912"),
    ("T07", "HOLD", ["POSSIBLE_REVISION"], None),
    ("T08", "HOLD", ["UNIT_PRICE_VARIANCE"], None),
    ("T09", "HOLD", ["VENDOR_NOT_FOUND"], None),
    ("T12", "HOLD", ["LINE_ITEM_UNMATCHED"], None),
    ("T15", "HOLD", ["ARITHMETIC_MISMATCH"], None),
    ("T16", "HOLD", ["POSSIBLE_DUPLICATE"], None),
    ("T17", "HOLD", ["PO_INFERRED"], None),
    ("T18", "REJECT", ["VENDOR_BLOCKED"], None),
    ("T25a", "HOLD", ["BANK_DETAILS_MISMATCH"], None),
    ("T25b", "APPROVE", [], None),
]


class NoProvider:
    provider, model = "none", "none"

    def extract(self, pages, schema=None):
        raise AssertionError("scenario tests must be served from the extraction cache")


@pytest.fixture(scope="module")
def outcomes(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("scenarios")
    from tests.conftest import TEST_CHAIN

    from app.config import Settings
    settings = Settings(database_path=tmp / "s.db", uploads_dir=tmp / "u", frontend_origin="http://x",
                        demo_reset_token="t", gemini_api_key="k", text_chain=TEST_CHAIN, vision_chain=TEST_CHAIN,
                        cache_snapshot=SNAPSHOT)
    results = {}
    with TestClient(create_app(settings, adapter_for=lambda e: NoProvider())) as c:
        for sample, *_ in EXPECTED:
            r = c.post(f"/api/samples/{FILES[sample]}/process")
            assert r.status_code == 201, r.text
            assert c.app.state.worker.wait_idle(60)
            conn = connect(settings.database_path)
            run = dict(conn.execute("SELECT * FROM runs WHERE run_id = ?", (r.json()["run_id"],)).fetchone())
            dec = conn.execute("SELECT * FROM decisions WHERE run_id = ?", (run["run_id"],)).fetchone()
            ext = conn.execute("SELECT cached FROM extractions WHERE run_id = ?", (run["run_id"],)).fetchone()
            results[sample] = {"run": run, "decision": dict(dec) if dec else None, "cached": ext and ext[0]}
            conn.close()
        conn = connect(settings.database_path)
        results["_consumption"] = {r[0]: r[1] for r in conn.execute(
            "SELECT l.po_number, SUM(c.value) FROM po_consumption c JOIN po_lines l USING (po_line_id) "
            "WHERE c.run_id IS NOT NULL GROUP BY l.po_number")}
        results["_pos"] = {p["po_number"]: p for p in c.get("/api/pos").json()}
        conn.close()
    return results


@pytest.mark.parametrize("sample, status, codes, note", EXPECTED, ids=[e[0] for e in EXPECTED])
def test_scenario(outcomes, sample, status, codes, note):
    o = outcomes[sample]
    assert o["run"]["status"] == "DECIDED", o["run"]["error_message"]
    assert o["cached"] == 1
    d = o["decision"]
    got = (d["status"], sorted(json.loads(d["reason_codes"])))
    assert got == (status, sorted(codes)), json.loads(d["summary"])
    if note:
        assert any(note in n for n in json.loads(d["notes"])), json.loads(d["notes"])


def test_only_approvals_consume_po_balance(outcomes):
    # T-01 (PO-1042 Rs 95,000), T-06 (PO-1043 Rs 45,000) and T-25b (PO-1045 Rs 50,000) only.
    assert outcomes["_consumption"] == {"PO-1042": 9500000, "PO-1043": 4500000, "PO-1045": 5000000}
    pos = outcomes["_pos"]
    assert pos["PO-1042"]["remaining"]["paise"] == 3000000          # §9.10.1: remaining Rs 30,000
    assert pos["PO-1041"]["remaining"]["paise"] == 8000000          # T-02 HOLD changed nothing
    assert pos["PO-1044"]["remaining"]["paise"] == 8400000          # T-03 HOLD changed nothing


def test_t01_commit_allocates_discount_pro_rata(outcomes):
    plan = json.loads(outcomes["T01"]["decision"]["commit_plan"])
    assert [(l["po_line_id"], l["quantity"], l["value"]) for l in plan["lines"]] == [
        ("PO-1042-1", 80.0, 4940000), ("PO-1042-2", 40.0, 4560000)]          # §9.10.1
    assert outcomes["T01"]["decision"]["committed"] == 1


def test_t02_sentence_matches_fr085(outcomes):
    summary = json.loads(outcomes["T02"]["decision"]["summary"])
    assert "Taxable value ₹1,00,000 exceeds remaining ₹80,000 (allowed overrun ₹1,000) on PO-1041." in summary
    assert "TMT-20: 220 approved + 100 invoiced exceeds PO qty 300 by 20." in summary


def test_t04_candidates(outcomes):
    cands = json.loads(outcomes["T04"]["decision"]["po_candidates"])
    assert [(c["po_number"], c["score"]) for c in cands] == [("PO-1031", 100), ("PO-1036", 70)]


def test_t09_stage_statuses(outcomes):
    stages = {s["stage"]: s for s in json.loads(outcomes["T09"]["run"]["stage_results"])}
    assert stages["VENDOR_BANK"]["status"] == "NEEDS_REVIEW"
    assert stages["DUPLICATE"]["status"] == "NOT_CHECKED"
    assert stages["DUPLICATE"]["not_checked_reason"] == "Vendor not identified"
    assert stages["PO_MATCH"]["status"] == "NOT_CHECKED"


def test_every_check_has_evidence_and_no_full_account_numbers(outcomes):
    from tests.conftest import fixture_records

    accounts = [v["bank_account_number"] for v in fixture_records("vendors.json")] + ["50100045671234"]
    for sample, *_ in EXPECTED:
        d = outcomes[sample]["decision"]
        evidence = json.loads(d["evidence"])
        codes = json.loads(d["reason_codes"])
        for code in codes:
            assert any(e["reason_code"] == code and e["expected"] is not None for e in evidence), (sample, code)
        blob = d["evidence"] + d["summary"]
        for acct in accounts:
            assert acct not in blob, sample


def test_t17_two_plausible_po_candidates_within_10_points(outcomes):
    """OD-1: PO-1031 and PO-1036 both fit; their scores are within 10 points, so both are flagged plausible."""
    cands = {c["po_number"]: c for c in json.loads(outcomes["T17"]["decision"]["po_candidates"])}
    assert set(cands) == {"PO-1031", "PO-1036"}
    assert all(c["plausible"] for c in cands.values())
    assert abs(cands["PO-1031"]["score"] - cands["PO-1036"]["score"]) <= 10
    assert outcomes["T17"]["decision"]["matched_po"] in cands            # the top suggestion, as for T-04
    assert not outcomes["T17"]["decision"]["committed"]                  # inferred: nothing consumed until confirmed
