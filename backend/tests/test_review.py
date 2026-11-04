"""Day 4: override and confirm-PO (FR-100..FR-106, OD-5, OD-6) and the read endpoints
(FR-111..FR-117). Scenarios T-22..T-25 run through the real pipeline from the extraction cache."""
import dataclasses
import json
import threading

import pytest
from fastapi.testclient import TestClient

from app.db.connection import connect
from app.main import create_app
from app.policy import load_policy
from app.services import review
from tests.conftest import fixture_records
from tests.test_scenarios import FILES, SNAPSHOT, NoProvider

VERIFIED = {"verified_with": "Meena Raghavan", "channel": "phone on record", "outcome": "verified"}
REASON = "Vendor confirmed the new account by phone; letter on file."


@pytest.fixture
def api(settings):
    s = dataclasses.replace(settings, cache_snapshot=SNAPSHOT)
    with TestClient(create_app(s, adapter_for=lambda e: NoProvider())) as c:
        c.settings = s
        yield c


def run(c, sample) -> str:
    r = c.post(f"/api/samples/{FILES[sample]}/process")
    assert r.status_code == 201, r.text
    assert c.app.state.worker.wait_idle(60)
    return r.json()["run_id"]


def q(c, sql, *args):
    conn = connect(c.settings.database_path)
    try:
        return [dict(r) for r in conn.execute(sql, args)]
    finally:
        conn.close()


def decision(c, rid):
    d = q(c, "SELECT * FROM decisions WHERE run_id = ?", rid)[0]
    d["reason_codes"] = json.loads(d["reason_codes"])
    return d


def consumed(c, po):
    return q(c, "SELECT COALESCE(SUM(c.value), 0) AS v FROM po_consumption c JOIN po_lines l USING (po_line_id) "
                "WHERE l.po_number = ? AND c.run_id IS NOT NULL", po)[0]["v"]


def po(c, number):
    return next(p for p in c.get("/api/pos").json() if p["po_number"] == number)


def approve(c, rid, codes, bank=None, reason=REASON, name="Ashrith"):
    body = {"action": "approve", "reviewer_name": name, "reason": reason, "codes_overridden": codes}
    if bank is not None:
        body["bank_verification"] = bank
    return c.post(f"/api/runs/{rid}/override", json=body)


# ======================================================================= PRD §13 scenarios

def test_t23_then_t22_bank_override(api):
    rid = run(api, "T03")
    assert decision(api, rid)["reason_codes"] == ["BANK_DETAILS_MISMATCH"]

    # T-23: outcome "not verified" -> 422, attempt recorded, still HOLD, nothing committed (OD-5)
    r = approve(api, rid, ["BANK_DETAILS_MISMATCH"], {**VERIFIED, "outcome": "not verified"})
    assert r.status_code == 422 and r.json()["error"] == "bank_not_verified"
    assert "remains on HOLD" in r.json()["message"]
    assert decision(api, rid)["status"] == "HOLD"
    rec = q(api, "SELECT * FROM overrides WHERE run_id = ?", rid)
    assert len(rec) == 1 and rec[0]["new_status"] == "HOLD" and rec[0]["commit_result"] == "not_applicable"
    assert json.loads(rec[0]["bank_verification"])["outcome"] == "not verified"
    assert consumed(api, "PO-1044") == 0

    # bank fields missing entirely -> refused, nothing recorded
    assert approve(api, rid, ["BANK_DETAILS_MISMATCH"]).json()["error"] == "bank_verification_required"
    assert approve(api, rid, ["BANK_DETAILS_MISMATCH"], {**VERIFIED, "channel": "invoice letterhead"}).status_code == 422
    assert len(q(api, "SELECT * FROM overrides WHERE run_id = ?", rid)) == 1

    # T-22: verified -> commits; PO-1044 consumed Rs 42,000, remaining Rs 42,000
    r = approve(api, rid, ["BANK_DETAILS_MISMATCH"], VERIFIED)
    assert r.status_code == 200, r.text
    body = r.json()
    assert (body["status"], body["commit_result"]) == ("APPROVE", "committed")
    assert body["message"] == "Approved and committed: PO-1044 remaining balance ₹42,000"
    d = decision(api, rid)
    assert (d["status"], d["decided_by"], d["committed"]) == ("APPROVE", "reviewer", 1)
    assert consumed(api, "PO-1044") == 4200000
    assert po(api, "PO-1044")["remaining"]["paise"] == 4200000
    rec = q(api, "SELECT * FROM overrides WHERE run_id = ? ORDER BY created_at, rowid", rid)[-1]
    assert (rec["action"], rec["original_status"], rec["new_status"], rec["commit_result"]) == \
           ("approve", "HOLD", "APPROVE", "committed")
    assert json.loads(rec["bank_verification"]) == VERIFIED and json.loads(rec["codes_overridden"]) == ["BANK_DETAILS_MISMATCH"]
    assert rec["reviewer_name"] == "Ashrith" and rec["reason"] == REASON


def test_t24_confirm_suggested_po(api):
    rid = run(api, "T04")
    d = decision(api, rid)
    assert d["reason_codes"] == ["PO_INFERRED"]
    r = api.post(f"/api/runs/{rid}/confirm-po",
                 json={"po_number": "PO-1031", "reviewer_name": "Ashrith", "reason": "Requester confirmed PO-1031 by email."})
    assert r.status_code == 200, r.text
    assert (r.json()["status"], r.json()["commit_result"]) == ("APPROVE", "committed")
    d = decision(api, rid)
    assert (d["status"], d["decided_by"], d["matched_po"], d["committed"]) == ("APPROVE", "rules_after_review", "PO-1031", 1)
    assert d["reason_codes"] == []
    assert consumed(api, "PO-1031") == 28000000                                    # Rs 2,80,000
    lines = {r["po_line_id"]: (r["quantity"], r["value"]) for r in
             q(api, "SELECT po_line_id, quantity, value FROM po_consumption WHERE run_id = ?", rid)}
    assert lines == {"PO-1031-1": (4, 24800000), "PO-1031-3": (4, 3200000)}
    stages = {s["stage"]: s for s in json.loads(q(api, "SELECT stage_results FROM runs WHERE run_id = ?", rid)[0]["stage_results"])}
    assert stages["PO_MATCH"]["status"] == "PASSED" and "(confirmed)" in stages["PO_MATCH"]["summary"]
    assert stages["DECIDE"]["summary"] == "APPROVE"
    rec = q(api, "SELECT * FROM overrides WHERE run_id = ?", rid)[0]
    assert (rec["action"], rec["confirmed_po"], rec["commit_result"]) == ("confirm_po", "PO-1031", "committed")
    assert json.loads(rec["codes_overridden"]) == ["PO_INFERRED"]
    led = q(api, "SELECT po_number, status FROM invoice_ledger WHERE run_id = ?", rid)[0]
    assert led == {"po_number": "PO-1031", "status": "APPROVE"}


def test_t25_override_blocked_by_commit_recheck(api):
    a = run(api, "T25a")
    assert decision(api, a)["reason_codes"] == ["BANK_DETAILS_MISMATCH"]
    b = run(api, "T25b")
    assert decision(api, b)["status"] == "APPROVE" and consumed(api, "PO-1045") == 5000000

    r = approve(api, a, ["BANK_DETAILS_MISMATCH"],
                {"verified_with": "Suresh Kaveriappa", "channel": "phone on record", "outcome": "verified"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert (body["status"], body["commit_result"]) == ("HOLD", "blocked")
    assert body["message"] == "Override blocked: PO-1045 remaining ₹50,000 is less than this invoice (₹60,000)"
    assert set(body["reason_codes"]) == {"BANK_DETAILS_MISMATCH", "QUANTITY_EXCEEDS_PO", "PO_BALANCE_EXCEEDED"}
    d = decision(api, a)
    assert d["status"] == "HOLD" and d["committed"] == 0
    summary = json.loads(d["summary"])
    assert "CBL-25: 50 approved + 60 invoiced exceeds PO qty 100 by 10." in summary
    assert "Taxable value ₹60,000 exceeds remaining ₹50,000 (allowed overrun ₹1,000) on PO-1045." in summary
    assert consumed(api, "PO-1045") == 5000000                                      # (a) wrote nothing
    rec = q(api, "SELECT * FROM overrides WHERE run_id = ?", a)[0]
    assert (rec["new_status"], rec["commit_result"]) == ("HOLD", "blocked")
    assert "remaining ₹50,000" in rec["block_reason"]
    types = [e["type"] for e in api.get(f"/api/runs/{a}/audit").json()["events"]]
    assert "commit_blocked" in types and types.count("override") == 1


# ======================================================================= override rules

@pytest.mark.parametrize("sample, status", [("T01", "APPROVE"), ("T05", "REJECT")])
def test_final_decisions_cannot_be_overridden(api, sample, status):
    rid = run(api, sample)
    before = (decision(api, rid), q(api, "SELECT * FROM po_consumption"))
    for action in ("approve", "reject"):
        r = api.post(f"/api/runs/{rid}/override", json={"action": action, "reviewer_name": "A", "reason": REASON,
                                                         "codes_overridden": []})
        assert r.status_code == 409 and r.json()["error"] == "not_overridable"
    assert (decision(api, rid), q(api, "SELECT * FROM po_consumption")) == before
    assert q(api, "SELECT * FROM overrides") == []


@pytest.mark.parametrize("body, error", [
    ({"reason": "too short"}, "reason_too_short"),                   # 9 characters
    ({"reason": "  x        "}, "reason_too_short"),
    ({"reviewer_name": "  "}, "reviewer_name_required"),
    ({"codes_overridden": []}, "codes_not_acknowledged"),
    ({"codes_overridden": ["BANK_DETAILS_MISMATCH", "VENDOR_BLOCKED"]}, "invalid_codes"),
    ({"action": "delete"}, "invalid_action"),
])
def test_override_validation(api, body, error):
    rid = run(api, "T03")
    base = {"action": "approve", "reviewer_name": "Ashrith", "reason": REASON,
            "codes_overridden": ["BANK_DETAILS_MISMATCH"], "bank_verification": VERIFIED}
    r = api.post(f"/api/runs/{rid}/override", json={**base, **body})
    assert r.status_code == 422 and r.json()["error"] == error
    assert decision(api, rid)["status"] == "HOLD" and q(api, "SELECT * FROM overrides") == []


def test_reason_of_exactly_ten_characters_is_accepted(api):
    rid = run(api, "T08")
    r = api.post(f"/api/runs/{rid}/override", json={"action": "reject", "reviewer_name": "A", "reason": "0123456789"})
    assert r.status_code == 200 and r.json()["status"] == "REJECT"


def test_reject_override(api):
    rid = run(api, "T08")
    r = api.post(f"/api/runs/{rid}/override", json={"action": "reject", "reviewer_name": "Ashrith",
                                                     "reason": "Price not agreed with the buyer."})
    assert r.status_code == 200 and r.json()["commit_result"] == "not_applicable"
    d = decision(api, rid)
    assert (d["status"], d["decided_by"]) == ("REJECT", "reviewer")
    assert q(api, "SELECT status FROM invoice_ledger WHERE run_id = ?", rid) == [{"status": "REJECT"}]
    assert consumed(api, "PO-1045") == 0
    assert api.post(f"/api/runs/{rid}/override", json={"action": "approve", "reviewer_name": "A", "reason": REASON,
                                                        "codes_overridden": ["UNIT_PRICE_VARIANCE"]}).status_code == 409


def test_approve_without_commitable_po_is_refused(api):
    rid = run(api, "T12")                       # LINE_ITEM_UNMATCHED: no commit plan
    r = approve(api, rid, ["LINE_ITEM_UNMATCHED"])
    assert r.status_code == 409 and r.json()["error"] == "nothing_to_commit"
    assert decision(api, rid)["status"] == "HOLD" and q(api, "SELECT * FROM overrides") == []


def test_two_overrides_do_not_double_commit(api):
    rid = run(api, "T03")
    first = approve(api, rid, ["BANK_DETAILS_MISMATCH"], VERIFIED)
    second = approve(api, rid, ["BANK_DETAILS_MISMATCH"], VERIFIED)
    assert first.status_code == 200 and second.status_code == 409
    assert len(q(api, "SELECT * FROM po_consumption WHERE run_id = ?", rid)) == 1
    assert consumed(api, "PO-1044") == 4200000


def test_concurrent_overrides_commit_once(api):
    rid = run(api, "T03")
    policy = load_policy()
    results, barrier = [], threading.Barrier(2)

    def go():
        conn = connect(api.settings.database_path)
        conn.execute("PRAGMA busy_timeout = 10000")
        barrier.wait()
        try:
            results.append(review.override(conn, rid, {"action": "approve", "reviewer_name": "A", "reason": REASON,
                                                       "codes_overridden": ["BANK_DETAILS_MISMATCH"],
                                                       "bank_verification": VERIFIED}, policy).status)
        except review.ReviewError as exc:
            results.append(exc.code)
        finally:
            conn.close()

    threads = [threading.Thread(target=go) for _ in range(2)]
    [t.start() for t in threads]
    [t.join(30) for t in threads]
    assert sorted(results) == ["APPROVE", "not_overridable"]
    assert consumed(api, "PO-1044") == 4200000


# ======================================================================= confirm PO rules

def test_confirm_po_must_be_a_shown_candidate(api):
    rid = run(api, "T04")
    for bad in ("PO-1042", "PO-9999", ""):
        r = api.post(f"/api/runs/{rid}/confirm-po", json={"po_number": bad, "reviewer_name": "A", "reason": REASON})
        assert r.status_code == 422, bad
    assert decision(api, rid)["status"] == "HOLD" and q(api, "SELECT * FROM overrides") == []


def test_confirm_po_only_for_po_codes(api):
    rid = run(api, "T03")
    r = api.post(f"/api/runs/{rid}/confirm-po", json={"po_number": "PO-1044", "reviewer_name": "A", "reason": REASON})
    assert r.status_code == 409 and r.json()["error"] == "confirm_po_not_applicable"


def test_confirm_lower_scoring_candidate_keeps_line_failures(api):
    # T-04 lines include DOCK-USB, which PO-1036 does not have: confirming it re-runs stage 6 as explicit.
    rid = run(api, "T04")
    r = api.post(f"/api/runs/{rid}/confirm-po", json={"po_number": "PO-1036", "reviewer_name": "A", "reason": REASON})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "HOLD" and body["reason_codes"] == ["LINE_ITEM_UNMATCHED"]
    assert body["message"] == "Still on HOLD: LINE_ITEM_UNMATCHED remains after PO confirmation"
    assert consumed(api, "PO-1036") == 0


# ======================================================================= read endpoints

def test_dashboard_po_balance_four_separate_values(api):
    dash = api.get("/api/dashboard").json()
    p41 = next(p for p in dash["po_balances"] if p["po_number"] == "PO-1041")
    assert p41["po_total"] == {"paise": 30000000, "display": "₹3,00,000.00"}
    assert p41["approved"] == {"paise": 22000000, "display": "₹2,20,000.00"}
    assert p41["remaining"] == {"paise": 8000000, "display": "₹80,000.00"}         # not 81,000
    assert p41["allowed_overrun"] == {"paise": 100000, "display": "₹1,000.00"}
    assert dash["label"] == "Demo dataset: synthetic invoices"


def test_dashboard_metrics(api):
    t01, t02, t03 = run(api, "T01"), run(api, "T02"), run(api, "T03")
    t05 = run(api, "T05")
    dash = api.get("/api/dashboard").json()
    assert dash["invoices_processed"] == 4
    assert {k: dash["by_status"][k] for k in ("APPROVE", "HOLD", "REJECT")} == {"APPROVE": 1, "HOLD": 2, "REJECT": 1}
    assert dash["auto_approval_rate"]["display"] == "25.0%"
    assert dash["value_on_hold"]["paise"] == 11800000 + 4956000                   # T-02 + T-03 totals
    codes = {e["code"]: e["count"] for e in dash["exceptions_by_reason"]}
    assert codes == {"QUANTITY_EXCEEDS_PO": 1, "PO_BALANCE_EXCEEDED": 1, "BANK_DETAILS_MISMATCH": 1,
                     "CONFIRMED_DUPLICATE": 1}
    assert dash["avg_processing_ms"] is not None and len(dash["recent_runs"]) == 4
    # an override approval no longer counts as automatic
    approve(api, t03, ["BANK_DETAILS_MISMATCH"], VERIFIED)
    assert api.get("/api/dashboard").json()["auto_approval_rate"]["display"] == "25.0%"


def test_runs_list_and_filters(api):
    t01, t02, t04 = run(api, "T01"), run(api, "T02"), run(api, "T04")
    allruns = api.get("/api/runs").json()
    assert [r["run_id"] for r in allruns] == [t04, t02, t01]                      # newest first
    r01 = next(r for r in allruns if r["run_id"] == t01)
    assert (r01["status"], r01["vendor_id"], r01["invoice_number"]) == ("APPROVE", "V001", "SLST/2026/0512")
    assert r01["total"] == {"paise": 11385000, "display": "₹1,13,850.00"}
    assert [r["run_id"] for r in api.get("/api/runs?status=HOLD").json()] == [t04, t02]
    assert [r["run_id"] for r in api.get("/api/runs?reason_code=PO_INFERRED").json()] == [t04]
    assert [r["run_id"] for r in api.get("/api/runs?po_number=PO-1041").json()] == [t02]


def test_audit_endpoint(api):
    rid = run(api, "T03")
    approve(api, rid, ["BANK_DETAILS_MISMATCH"], VERIFIED)
    body = api.get(f"/api/runs/{rid}/audit").json()
    ids = [e["event_id"] for e in body["events"]]
    assert ids == sorted(ids)
    types = [e["type"] for e in body["events"]]
    assert types[0] == "run_created" and types[-2:] == ["commit", "override"]
    assert len(body["overrides"]) == 1 and body["overrides"][0]["bank_verification"] == VERIFIED
    assert api.get("/api/runs/nope/audit").status_code == 404


def test_policy_endpoint(api):
    p = api.get("/api/policy").json()
    assert len(p["assumptions"]) == 35 and p["assumptions"][0]["id"] == "A-01"
    assert all(a["label"] == "[PA]" for a in p["assumptions"])
    assert len(p["reason_codes"]) == 17
    assert {c["code"] for c in p["reason_codes"] if c["severity"] == "REJECT"} == {"CONFIRMED_DUPLICATE", "VENDOR_BLOCKED"}
    assert len(p["does_not_verify"]) == 5 and p["p1_rules"] == {"unsupported_document": False, "tax_regime": False}
    assert p["policy_version"] == load_policy().version


def test_no_contact_or_account_leaks_in_read_endpoints(api):
    for s in ("T01", "T03", "T25a"):
        run(api, s)
    t03 = api.get("/api/runs?reason_code=BANK_DETAILS_MISMATCH").json()[-1]["run_id"]
    texts = {path: api.get(path).text for path in
             ("/api/vendors", "/api/pos", "/api/runs", "/api/dashboard", "/api/policy", f"/api/runs/{t03}/audit")}
    for v in fixture_records("vendors.json"):
        for path, text in texts.items():
            assert v["bank_account_number"] not in text, path
            assert v["trusted_contact_phone"] not in text, path
            assert v["trusted_contact_email"] not in text, path
    assert "50100045671234" not in "".join(texts.values())                           # T-25a's printed account
    # the run carrying BANK_DETAILS_MISMATCH is the one place the contact appears (FR-044)
    assert api.get(f"/api/runs/{t03}").json()["trusted_contact"]["phone"] in ("+91 98400 11223", "+91 94480 67215")


def test_overrides_in_the_same_second_keep_insertion_order(api, monkeypatch):
    """Day 6 bug: overrides were ordered by created_at (second precision) then by the random
    override_id, so a "not verified" attempt and the approval made in the same second could be
    listed the wrong way round on the Audit tab."""
    import uuid as uuid_mod

    from app.services import review as review_mod

    rid = run(api, "T03")
    monkeypatch.setattr(review_mod, "now_iso", lambda: "2026-11-04T10:00:00+05:30")
    ids = iter(["f" * 32, "0" * 32])                     # the second override sorts first by id
    monkeypatch.setattr(review_mod.uuid, "uuid4", lambda: uuid_mod.UUID(next(ids)))
    not_verified = {**VERIFIED, "outcome": "not verified"}
    approve(api, rid, ["BANK_DETAILS_MISMATCH"], not_verified)
    approve(api, rid, ["BANK_DETAILS_MISMATCH"], VERIFIED)
    overrides = api.get(f"/api/runs/{rid}/audit").json()["overrides"]
    assert [o["created_at"] for o in overrides] == ["2026-11-04T10:00:00+05:30"] * 2
    assert [o["bank_verification"]["outcome"] for o in overrides] == ["not verified", "verified"]
    assert [o["commit_result"] for o in overrides] == ["not_applicable", "committed"]
