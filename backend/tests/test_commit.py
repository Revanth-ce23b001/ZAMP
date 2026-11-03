"""commit_approval (FR-090..FR-094, §9.9, §12.6) and decision persistence, on a seeded database."""
import json
from datetime import date

import pytest

from app.policy import load_policy
from app.services.commit import commit_approval
from app.services.decision import decide_run
from app.services.extraction.schema import InvoiceExtraction
from app.services.refdata import load_consumption
from tests.test_rules import f, invoice, line, po41_invoice

POLICY = load_policy()
TODAY = date(2026, 11, 3)


def new_run(conn, n: int) -> str:
    rid = f"R-{n}"
    conn.execute("INSERT INTO runs (run_id, run_number, file_name, file_hash, file_path, status, created_at) "
                 "VALUES (?, ?, 'x.pdf', ?, 'x.pdf', 'PROCESSING', '2026-11-03T10:00:00+05:30')", (rid, n, f"h{n}"))
    return rid


def decide(conn, n, x: InvoiceExtraction):
    rid = new_run(conn, n)
    result, commit = decide_run(conn, rid, x, POLICY, [], today=TODAY)
    return rid, result, commit


def consumption_rows(conn, rid=None):
    q = "SELECT po_line_id, quantity, value FROM po_consumption" + (" WHERE run_id = ?" if rid else " WHERE run_id IS NOT NULL")
    return [tuple(r) for r in conn.execute(q, (rid,) if rid else ())]


def test_approve_commits_once_even_if_called_twice(seeded_db):
    rid, result, commit = decide(seeded_db, 1, invoice())
    assert result.status == "APPROVE" and commit.outcome == "committed"
    assert consumption_rows(seeded_db, rid) == [("PO-1042-1", 10, 650000)]
    again = commit_approval(seeded_db, rid, POLICY)
    assert again.outcome == "already_committed"
    assert consumption_rows(seeded_db, rid) == [("PO-1042-1", 10, 650000)]
    d = seeded_db.execute("SELECT committed, committed_at, status FROM decisions WHERE run_id = ?", (rid,)).fetchone()
    assert d["committed"] == 1 and d["committed_at"] and d["status"] == "APPROVE"
    led = seeded_db.execute("SELECT status, source, po_number FROM invoice_ledger WHERE run_id = ?", (rid,)).fetchone()
    assert tuple(led) == ("APPROVE", "run", "PO-1042")
    types = [r[0] for r in seeded_db.execute("SELECT type FROM audit_events WHERE run_id = ? ORDER BY event_id", (rid,))]
    assert types.count("commit") == 1 and "decision" in types


def test_hold_never_reduces_po_balance(seeded_db):
    before = load_consumption(seeded_db)
    rid, result, commit = decide(seeded_db, 1, invoice(total=f(8670.0)))
    assert result.status == "HOLD" and commit is None
    assert load_consumption(seeded_db) == before
    assert consumption_rows(seeded_db) == []
    assert commit_approval(seeded_db, rid, POLICY).outcome == "not_applicable"
    assert consumption_rows(seeded_db) == []


def test_reject_never_reduces_po_balance(seeded_db):
    before = load_consumption(seeded_db)
    x = invoice(vendor_name=f("Malnad Logistics"), vendor_gstin=f("29AAGFM9012R1Z4"), po_reference=f("PO-1046"),
                line_items=[line("Freight", 1, 15000.0, "FRT-LCL")], subtotal=f(15000.0), taxable_value=f(15000.0),
                cgst=f(1350.0), sgst=f(1350.0), total=f(17700.0), bank_account_number=f(None), ifsc=f(None))
    _, result, commit = decide(seeded_db, 1, x)
    assert result.status == "REJECT" and commit is None
    assert load_consumption(seeded_db) == before


def test_recheck_at_commit_blocks_and_writes_nothing(seeded_db):
    """T-25 shape: decision APPROVE, then another approval consumes the balance first."""
    # 60 units on PO-1041 (220 already approved): within limits when decided...
    rid, result, _ = decide(seeded_db, 1, po41_invoice(60))
    assert result.status == "APPROVE"
    # ...undo its commit to simulate an approval that has not been committed yet
    seeded_db.execute("DELETE FROM po_consumption WHERE run_id = ?", (rid,))
    seeded_db.execute("UPDATE decisions SET committed = 0, committed_at = NULL WHERE run_id = ?", (rid,))
    # another invoice for 50 units commits first: 220 + 50 = 270
    rid2, r2, c2 = decide(seeded_db, 2, po41_invoice(50).model_copy(update={
        "invoice_number": InvoiceExtraction.model_validate({"invoice_number": f("INV-T-2")}).invoice_number}))
    assert c2.outcome == "committed"
    before = consumption_rows(seeded_db)

    blocked = commit_approval(seeded_db, rid, POLICY)          # 270 + 60 > 300; value 2,70,000 + 60,000 > 3,01,000
    assert blocked.outcome == "blocked"
    assert set(blocked.codes) == {"QUANTITY_EXCEEDS_PO", "PO_BALANCE_EXCEEDED"}
    assert consumption_rows(seeded_db) == before                # nothing written
    d = seeded_db.execute("SELECT status, committed, reason_codes, summary FROM decisions WHERE run_id = ?", (rid,)).fetchone()
    assert d["status"] == "HOLD" and d["committed"] == 0
    assert set(json.loads(d["reason_codes"])) == {"QUANTITY_EXCEEDS_PO", "PO_BALANCE_EXCEEDED"}
    assert "TMT-20: 270 approved + 60 invoiced exceeds PO qty 300 by 30." in json.loads(d["summary"])
    assert seeded_db.execute("SELECT status FROM invoice_ledger WHERE run_id = ?", (rid,)).fetchone()[0] == "HOLD"
    ev = seeded_db.execute("SELECT payload FROM audit_events WHERE run_id = ? AND type = 'commit_blocked'", (rid,)).fetchone()
    assert ev and "Commit blocked" in json.loads(ev[0])["message"]


def test_unique_ledger_po_line_backs_idempotency(seeded_db):
    import sqlite3

    rid, _, _ = decide(seeded_db, 1, invoice())
    led = seeded_db.execute("SELECT ledger_id FROM invoice_ledger WHERE run_id = ?", (rid,)).fetchone()[0]
    with pytest.raises(sqlite3.IntegrityError):
        seeded_db.execute("INSERT INTO po_consumption (run_id, ledger_id, po_line_id, quantity, value, committed_at) "
                          "VALUES (?, ?, 'PO-1042-1', 1, 1, 'x')", (rid, led))


def test_decision_record_shape(seeded_db):
    rid, result, _ = decide(seeded_db, 1, invoice(ifsc=f("ICIC0005678")))
    d = dict(seeded_db.execute("SELECT * FROM decisions WHERE run_id = ?", (rid,)).fetchone())
    assert d["status"] == "HOLD" and json.loads(d["reason_codes"]) == ["BANK_DETAILS_MISMATCH"]
    assert d["decided_by"] == "rules" and d["policy_version"] == POLICY.version
    assert d["matched_po"] == "PO-1042" and d["committed"] == 0
    assert "50200012341234" not in d["evidence"]
    run = seeded_db.execute("SELECT status, stage_results FROM runs WHERE run_id = ?", (rid,)).fetchone()
    assert run["status"] == "DECIDED"
    assert [s["stage"] for s in json.loads(run["stage_results"])] == [
        "VALIDATE", "VENDOR_BANK", "DUPLICATE", "PO_MATCH", "DECIDE"]


def test_unknown_vendor_writes_no_ledger_row(seeded_db):
    rid, result, _ = decide(seeded_db, 1, invoice(vendor_name=f("Horizon Traders"), vendor_gstin=f("29AAHFH4821L1Z3")))
    assert result.reason_codes == ["VENDOR_NOT_FOUND"]
    assert seeded_db.execute("SELECT COUNT(*) FROM invoice_ledger WHERE run_id = ?", (rid,)).fetchone()[0] == 0
