"""POST /api/demo/reset (PRD FR-116, DEC-002)."""
import json
import logging

import pytest
from fastapi.testclient import TestClient

from app.db import seed as seed_module
from app.db.connection import connect
from app.main import create_app
from app.policy import load_policy
from app.services.reset import ResetService
from tests.conftest import TEST_TOKEN

URL = "/api/demo/reset"
GOOD = {"confirm": "RESET", "requester_label": "pytest"}
SEED_COUNTS = {"vendors": 7, "purchase_orders": 11, "po_lines": 15, "ledger": 5, "consumption": 5}
TS = "2026-11-02T10:00:00+05:30"


class FakeClock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def rc(settings, clock):
    """Client whose reset service uses a controllable clock."""
    app = create_app(settings)
    app.state.reset_service = ResetService(settings, load_policy(), clock=clock)
    with TestClient(app) as c:
        c.db_path = settings.database_path
        c.uploads = settings.uploads_dir
        yield c


def post(client, body=GOOD, token=TEST_TOKEN):
    headers = {} if token is None else {"X-Demo-Reset-Token": token}
    return client.post(URL, json=body, headers=headers)


# ---------- helpers ----------

STATE_TABLES = ("vendors", "purchase_orders", "po_lines", "runs", "extraction_cache", "extractions",
                "decisions", "invoice_ledger", "po_consumption", "overrides", "audit_events")


def snapshot(db_path):
    """All business data. po_consumption.consumption_id is an AUTOINCREMENT surrogate key that
    grows across reloads by design, so it is left out."""
    conn = connect(db_path)
    select = {"po_consumption": "SELECT run_id, ledger_id, po_line_id, quantity, value, committed_at FROM po_consumption"}
    try:
        return {t: sorted((tuple(r) for r in conn.execute(select.get(t, f"SELECT * FROM {t}"))), key=repr)
                for t in STATE_TABLES}
    finally:
        conn.close()


def reset_rows(db_path):
    conn = connect(db_path)
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM demo_resets ORDER BY reset_id")]
    finally:
        conn.close()


def add_runtime_data(db_path):
    """A run with extraction, decision, override, audit event, ledger row and consumption on PO-1041,
    plus an extraction_cache row. Changes PO-1041 remaining from Rs 80,000 to Rs 30,000."""
    conn = connect(db_path)
    conn.execute("BEGIN")
    conn.execute("INSERT INTO runs (run_id, run_number, file_name, file_hash, file_path, status, created_at) "
                  "VALUES ('R-0001', 1, 'x.pdf', 'hash-x', 'x.pdf', 'DECIDED', ?)", (TS,))
    conn.execute("INSERT INTO extraction_cache VALUES ('hash-x', 'anthropic', 'm', 'text', '{}', ?)", (TS,))
    conn.execute("INSERT INTO extractions VALUES ('R-0001', 'hash-x', 'anthropic', 'm', 'text', '{}', ?, 1)", (TS,))
    conn.execute("INSERT INTO decisions (decision_id, run_id, status, decided_by, policy_version, committed, "
                  "committed_at, created_at, updated_at) VALUES ('D-0001', 'R-0001', 'APPROVE', 'reviewer', 'v', 1, ?, ?, ?)",
                  (TS, TS, TS))
    conn.execute("INSERT INTO overrides (override_id, run_id, action, original_status, new_status, reviewer_name, "
                  "reason, commit_result, created_at) VALUES ('O-0001', 'R-0001', 'approve', 'HOLD', 'APPROVE', "
                  "'Tester', 'reason long enough', 'committed', ?)", (TS,))
    conn.execute("INSERT INTO audit_events (run_id, type, payload, created_at) VALUES ('R-0001', 'decision', '{}', ?)", (TS,))
    conn.execute("INSERT INTO invoice_ledger (ledger_id, run_id, source, vendor_id, invoice_number_raw, "
                  "invoice_number_normalised, invoice_date, total, taxable_value, po_number, status) "
                  "VALUES ('L-0001', 'R-0001', 'run', 'V001', 'INV-X', 'INVX', '2026-10-20', 5900000, 5000000, 'PO-1041', 'APPROVE')")
    conn.execute("INSERT INTO po_consumption (run_id, ledger_id, po_line_id, quantity, value, committed_at) "
                  "VALUES ('R-0001', 'L-0001', 'PO-1041-1', 50, 5000000, ?)", (TS,))
    conn.execute("COMMIT")
    conn.close()


def remaining(client, po):
    return next(p for p in client.get("/api/pos").json() if p["po_number"] == po)["remaining"]["paise"]


def count(db_path, table):
    conn = connect(db_path)
    try:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    finally:
        conn.close()


# ---------- success ----------

def test_successful_reset_restores_seed(rc):
    add_runtime_data(rc.db_path)
    assert remaining(rc, "PO-1041") == 3000000

    r = post(rc)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body == {"reset_id": body["reset_id"], "restored": SEED_COUNTS}

    for t in ("runs", "decisions", "overrides", "audit_events", "extractions"):
        assert count(rc.db_path, t) == 0, t
    assert rc.get("/api/seed-status").json()["counts"] == SEED_COUNTS
    assert remaining(rc, "PO-1041") == 8000000

    rows = reset_rows(rc.db_path)
    assert [(x["outcome"], x["failure_reason"], x["requester_label"]) for x in rows] == [("success", None, "pytest")]
    assert rows[0]["reset_id"] == body["reset_id"]


def test_extraction_cache_survives_reset(rc):
    add_runtime_data(rc.db_path)
    assert post(rc).status_code == 200
    conn = connect(rc.db_path)
    assert [tuple(r)[:2] for r in conn.execute("SELECT file_hash, provider FROM extraction_cache")] == [("hash-x", "anthropic")]
    conn.close()


def test_reset_on_clean_seed_is_identical(rc):
    before = snapshot(rc.db_path)
    assert post(rc).status_code == 200
    assert snapshot(rc.db_path) == before


# ---------- token ----------

@pytest.mark.parametrize("token", [None, "", "wrong-token", TEST_TOKEN + "x"])
def test_bad_token_401(rc, token):
    add_runtime_data(rc.db_path)
    before = snapshot(rc.db_path)
    r = post(rc, token=token)
    assert r.status_code == 401
    assert r.json()["error"] == "invalid_token"
    assert snapshot(rc.db_path) == before
    rows = reset_rows(rc.db_path)
    assert [(x["outcome"], x["failure_reason"]) for x in rows] == [("rejected", "invalid_token")]


# ---------- confirmation ----------

@pytest.mark.parametrize("body", [
    {"requester_label": "pytest"},                         # confirm missing
    {"confirm": "reset", "requester_label": "pytest"},     # lowercase
    {"confirm": "RESET ", "requester_label": "pytest"},    # trailing space
    {"confirm": "yes", "requester_label": "pytest"},
    {"confirm": True, "requester_label": "pytest"},
    None,                                                  # JSON null body
])
def test_bad_confirmation_400(rc, body):
    add_runtime_data(rc.db_path)
    before = snapshot(rc.db_path)
    r = post(rc, body=body)
    assert r.status_code == 400
    assert r.json()["error"] == "confirmation_mismatch"
    assert snapshot(rc.db_path) == before
    assert [(x["outcome"], x["failure_reason"]) for x in reset_rows(rc.db_path)] == [("rejected", "confirmation_mismatch")]


def test_malformed_json_body_400(rc):
    r = rc.post(URL, content=b"{not json", headers={"X-Demo-Reset-Token": TEST_TOKEN,
                                                     "Content-Type": "application/json"})
    assert r.status_code == 400
    assert r.json()["error"] == "confirmation_mismatch"


def test_missing_requester_label_400(rc):
    before = snapshot(rc.db_path)
    r = post(rc, body={"confirm": "RESET"})
    assert r.status_code == 400
    assert r.json()["error"] == "missing_requester_label"
    assert snapshot(rc.db_path) == before
    assert [(x["outcome"], x["requester_label"]) for x in reset_rows(rc.db_path)] == [("rejected", "unknown")]


# ---------- rate limits ----------

def test_second_success_within_30s_is_429(rc, clock):
    assert post(rc).status_code == 200
    add_runtime_data(rc.db_path)
    before = snapshot(rc.db_path)

    clock.advance(29)
    r = post(rc)
    assert r.status_code == 429
    assert r.json()["error"] == "rate_limit_success_window"
    assert snapshot(rc.db_path) == before

    clock.advance(1)                     # exactly 30 s after the first success
    assert post(rc).status_code == 200
    outcomes = [(x["outcome"], x["failure_reason"]) for x in reset_rows(rc.db_path)]
    assert outcomes == [("success", None), ("rate_limited", "rate_limit_success_window"), ("success", None)]


def test_sixth_rejected_attempt_is_429_not_401(rc, clock):
    add_runtime_data(rc.db_path)
    before = snapshot(rc.db_path)
    for _ in range(3):
        assert post(rc, token="wrong").status_code == 401
    for _ in range(2):
        assert post(rc, body={"confirm": "no", "requester_label": "x"}).status_code == 400
    r = post(rc, token="wrong")                           # sixth rejected attempt
    assert r.status_code == 429
    assert r.json()["error"] == "rate_limit_rejected_attempts"
    assert post(rc).status_code == 429                    # even a valid request is blocked for this IP
    assert snapshot(rc.db_path) == before

    clock.advance(15 * 60)                                # window expires
    assert post(rc).status_code == 200

    reasons = [x["failure_reason"] for x in reset_rows(rc.db_path)]
    assert reasons == ["invalid_token"] * 3 + ["confirmation_mismatch"] * 2 + \
                      ["rate_limit_rejected_attempts"] * 2 + [None]


def test_rate_limited_attempts_do_not_extend_reject_window(rc, clock):
    for _ in range(5):
        post(rc, token="wrong")
    clock.advance(14 * 60)
    assert post(rc).status_code == 429
    clock.advance(60)                                     # 15 min after the first five
    assert post(rc).status_code == 200


# ---------- audit log ----------

def test_demo_resets_count_rises_by_one_per_attempt(rc, clock):
    attempts = [
        dict(token="wrong"), dict(body={"confirm": "x", "requester_label": "y"}), dict(),
        dict(), dict(token=None),
    ]
    seen = []
    for kw in attempts:
        post(rc, **kw)
        seen.append(count(rc.db_path, "demo_resets"))
        clock.advance(1)
    assert seen == [1, 2, 3, 4, 5]


# ---------- atomicity ----------

def test_forced_failure_rolls_back_everything(rc, monkeypatch):
    add_runtime_data(rc.db_path)
    (rc.uploads / "abc.pdf").write_bytes(b"%PDF-1.4 test")
    before = snapshot(rc.db_path)

    def boom(conn, fx):
        # Deletion has already happened inside the transaction when this runs.
        assert conn.execute("SELECT COUNT(*) FROM vendors").fetchone()[0] == 0
        raise RuntimeError("simulated loader failure")

    monkeypatch.setattr(seed_module, "insert_fixtures", boom)
    r = post(rc)
    assert r.status_code == 500
    assert r.json()["error"] == "reset_failed"

    assert snapshot(rc.db_path) == before
    assert (rc.uploads / "abc.pdf").exists()
    rows = reset_rows(rc.db_path)
    assert len(rows) == 1 and rows[0]["outcome"] == "failed"
    assert rows[0]["failure_reason"] and "simulated loader failure" in rows[0]["failure_reason"]


def test_corrupted_fixture_file_rolls_back(rc, monkeypatch, tmp_path):
    import shutil

    bad = tmp_path / "seed"
    shutil.copytree(seed_module.SEED_DIR, bad)
    doc = json.loads((bad / "purchase_orders.json").read_text(encoding="utf-8"))
    doc["records"][1]["po_total"] += 1                      # PO-1042
    (bad / "purchase_orders.json").write_text(json.dumps(doc), encoding="utf-8")
    monkeypatch.setattr(seed_module, "SEED_DIR", bad)
    monkeypatch.setattr(seed_module.load_fixtures, "__defaults__", (bad,))

    add_runtime_data(rc.db_path)
    before = snapshot(rc.db_path)
    r = post(rc)
    assert r.status_code == 500
    assert snapshot(rc.db_path) == before
    row = reset_rows(rc.db_path)[-1]
    assert row["outcome"] == "failed" and "PO-1042 po_total mismatch" in row["failure_reason"]


def test_failed_success_does_not_start_30s_window(rc, monkeypatch):
    monkeypatch.setattr(seed_module, "insert_fixtures", lambda c, f: (_ for _ in ()).throw(RuntimeError("x")))
    assert post(rc).status_code == 500
    monkeypatch.undo()
    assert post(rc).status_code == 200


def test_log_write_failure_returns_500(rc, monkeypatch, caplog):
    def broken(*a, **k):
        raise RuntimeError("disk full")

    monkeypatch.setattr(ResetService, "_insert_row", staticmethod(broken))
    with caplog.at_level(logging.ERROR, logger="clearmatch.reset"):
        r = post(rc, token="wrong")
    assert r.status_code == 500
    assert r.json()["error"] == "reset_log_failed"
    assert "could not write demo_resets row" in caplog.text


# ---------- uploads ----------

def test_uploads_deleted_after_success(rc):
    (rc.uploads / "a.pdf").write_bytes(b"%PDF-1.4 a")
    (rc.uploads / "b.pdf").write_bytes(b"%PDF-1.4 b")
    (rc.uploads / ".gitkeep").write_bytes(b"")
    assert post(rc).status_code == 200
    assert sorted(p.name for p in rc.uploads.iterdir()) == [".gitkeep"]


def test_uploads_kept_when_rejected(rc):
    (rc.uploads / "a.pdf").write_bytes(b"%PDF-1.4 a")
    post(rc, token="wrong")
    post(rc, body={"confirm": "no", "requester_label": "x"})
    assert (rc.uploads / "a.pdf").exists()


def test_upload_delete_error_does_not_undo_reset(rc, monkeypatch, caplog):
    from pathlib import Path

    (rc.uploads / "locked.pdf").write_bytes(b"%PDF")
    add_runtime_data(rc.db_path)
    real_unlink = Path.unlink

    def failing_unlink(self, *a, **k):
        if self.name == "locked.pdf":
            raise PermissionError("locked")
        return real_unlink(self, *a, **k)

    monkeypatch.setattr(Path, "unlink", failing_unlink)
    with caplog.at_level(logging.ERROR, logger="clearmatch.reset"):
        r = post(rc)
    assert r.status_code == 200
    assert count(rc.db_path, "runs") == 0
    assert "could not delete uploaded file locked.pdf" in caplog.text


# ---------- token never leaks ----------

def test_token_never_echoed(rc, clock, monkeypatch, caplog):
    caplog.set_level(logging.DEBUG)
    bodies = []
    for kw in (dict(token="wrong-" + TEST_TOKEN), dict(body={"confirm": "x", "requester_label": "y"}),
               dict(), dict()):
        r = post(rc, **kw)
        bodies.append(r.text)
        assert TEST_TOKEN not in r.text and TEST_TOKEN not in str(r.headers)
        clock.advance(1)
    monkeypatch.setattr(seed_module, "insert_fixtures", lambda c, f: (_ for _ in ()).throw(RuntimeError("boom")))
    clock.advance(60)
    bodies.append(post(rc).text)

    assert TEST_TOKEN not in caplog.text
    for row in reset_rows(rc.db_path):
        assert TEST_TOKEN not in json.dumps(row), row
    statuses = [json.loads(b).get("error") for b in bodies]
    assert statuses == ["invalid_token", "confirmation_mismatch", None, "rate_limit_success_window", "reset_failed"]
