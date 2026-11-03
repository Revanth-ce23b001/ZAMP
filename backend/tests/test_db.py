import json
import sqlite3

import pytest

from app.db.migrate import applied_versions, migrate
from app.db.seed import load_fixtures, seed, table_counts
from tests.conftest import fixture_records

EXPECTED_TABLES = {
    "vendors", "purchase_orders", "po_lines", "runs", "extraction_cache", "extractions",
    "decisions", "invoice_ledger", "po_consumption", "overrides", "audit_events", "demo_resets",
}
SEED_COUNTS = {"vendors": 7, "purchase_orders": 11, "po_lines": 15, "ledger": 5, "consumption": 5}


def tables(conn):
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}


# ---------- migration ----------

def test_migration_runs_on_empty_db(empty_db):
    assert tables(empty_db) == set()
    versions = ["0001_initial", "0002_decision_commit_plan", "0003_decision_details"]
    assert migrate(empty_db) == versions
    assert EXPECTED_TABLES <= tables(empty_db)
    assert applied_versions(empty_db) == versions
    assert migrate(empty_db) == []        # recorded; not re-applied
    assert applied_versions(empty_db) == versions


def test_foreign_keys_on(seeded_db):
    assert seeded_db.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    with pytest.raises(sqlite3.IntegrityError):
        seeded_db.execute("INSERT INTO purchase_orders VALUES ('PO-X', 'V999', '2026-10-01', 'INR', 0)")


def test_unique_constraints(seeded_db):
    with pytest.raises(sqlite3.IntegrityError):
        seeded_db.execute(
            "INSERT INTO po_consumption (run_id, ledger_id, po_line_id, quantity, value, committed_at) "
            "VALUES (NULL, 'L-SEED-01', 'PO-1041-1', 1, 100, '2026-11-02T10:00:00+05:30')"
        )
    run = ("R-1", 1, "a.pdf", "hash-1", "/x/a.pdf", "QUEUED", "2026-11-02T10:00:00+05:30")
    sql = "INSERT INTO runs (run_id, run_number, file_name, file_hash, file_path, status, created_at) VALUES (?,?,?,?,?,?,?)"
    seeded_db.execute(sql, run)
    with pytest.raises(sqlite3.IntegrityError):
        seeded_db.execute(sql, ("R-2", 2, "b.pdf", "hash-1", "/x/b.pdf", "QUEUED", "2026-11-02T10:00:00+05:30"))


def test_duplicate_lookup_index(seeded_db):
    cols = [r["name"] for r in seeded_db.execute("PRAGMA index_info('idx_ledger_vendor_invoice_no')")]
    assert cols == ["vendor_id", "invoice_number_normalised"]


def test_money_columns_are_integer(seeded_db):
    money_cols = {
        "purchase_orders": ["po_total"], "po_lines": ["unit_price"],
        "invoice_ledger": ["total", "taxable_value", "cgst", "sgst", "igst"], "po_consumption": ["value"],
    }
    for table, cols in money_cols.items():
        declared = {r["name"]: r["type"] for r in seeded_db.execute(f"PRAGMA table_info({table})")}
        for col in cols:
            assert declared[col] == "INTEGER", (table, col)
            types = {r[0] for r in seeded_db.execute(f"SELECT DISTINCT typeof({col}) FROM {table}")}
            assert types <= {"integer", "null"}, (table, col, types)


# ---------- seed ----------

def test_seed_counts_and_foreign_keys(seeded_db):
    assert table_counts(seeded_db) == SEED_COUNTS
    assert seeded_db.execute("PRAGMA foreign_key_check").fetchall() == []


def test_seed_twice_is_idempotent(seeded_db):
    before = table_counts(seeded_db)
    inserted = seed(seeded_db)
    assert inserted == dict.fromkeys(SEED_COUNTS, 0)
    assert table_counts(seeded_db) == before == SEED_COUNTS


def test_seed_values_loaded_as_is(seeded_db):
    for v in fixture_records("vendors.json"):
        row = dict(seeded_db.execute("SELECT * FROM vendors WHERE vendor_id = ?", (v["vendor_id"],)).fetchone())
        assert row == {k: v.get(k) for k in row}
    for rec in fixture_records("ledger.json"):
        row = dict(seeded_db.execute("SELECT * FROM invoice_ledger WHERE ledger_id = ?", (rec["ledger_id"],)).fetchone())
        row["line_signature"] = json.loads(row["line_signature"])
        assert row == {k: rec.get(k) for k in row}
    for po in fixture_records("purchase_orders.json"):
        for line in po["lines"]:
            row = seeded_db.execute("SELECT * FROM po_lines WHERE po_line_id = ?", (line["po_line_id"],)).fetchone()
            assert json.loads(row["aliases"]) == line["aliases"]
            assert (row["quantity"], row["unit_price"], row["description"]) == (
                line["quantity"], line["unit_price"], line["description"])


def test_underscore_keys_ignored():
    fx = load_fixtures()
    def walk(o):
        if isinstance(o, dict):
            assert not any(k.startswith("_") for k in o), o
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    walk(fx)


def test_po_total_equals_sum_of_lines(seeded_db):
    rows = seeded_db.execute(
        "SELECT po.po_number, po.po_total, SUM(l.quantity * l.unit_price) AS line_sum "
        "FROM purchase_orders po JOIN po_lines l USING (po_number) GROUP BY po.po_number"
    ).fetchall()
    assert len(rows) == 11
    for r in rows:
        assert r["po_total"] == r["line_sum"], r["po_number"]


def test_opening_consumption_equals_ledger_lines(seeded_db):
    """Each ledger line_signature entry maps to exactly one consumption row with the same qty
    and value = qty x PO unit price; nothing else is in consumption (DEC-004)."""
    expected = {}
    for led in seeded_db.execute("SELECT * FROM invoice_ledger").fetchall():
        for sig in json.loads(led["line_signature"]):
            line = seeded_db.execute(
                "SELECT po_line_id, unit_price FROM po_lines WHERE po_number = ? AND item_code = ?",
                (led["po_number"], sig["key"]),
            ).fetchall()
            assert len(line) == 1
            expected[(led["ledger_id"], line[0]["po_line_id"])] = (sig["qty"], sig["qty"] * line[0]["unit_price"])
    got = {
        (r["ledger_id"], r["po_line_id"]): (r["quantity"], r["value"])
        for r in seeded_db.execute("SELECT * FROM po_consumption")
    }
    assert got == expected
    for led in seeded_db.execute("SELECT ledger_id, taxable_value FROM invoice_ledger"):
        total = seeded_db.execute("SELECT SUM(value) FROM po_consumption WHERE ledger_id = ?",
                                  (led["ledger_id"],)).fetchone()[0]
        assert total == led["taxable_value"]


def test_bad_fixture_aborts_without_partial_load(empty_db, tmp_path):
    import shutil
    from app.db.seed import SEED_DIR, FixtureError

    bad = tmp_path / "seed"
    shutil.copytree(SEED_DIR, bad)
    doc = json.loads((bad / "purchase_orders.json").read_text(encoding="utf-8"))
    doc["records"][0]["po_total"] += 1
    (bad / "purchase_orders.json").write_text(json.dumps(doc), encoding="utf-8")
    migrate(empty_db)
    with pytest.raises(FixtureError, match="po_total mismatch"):
        seed(empty_db, bad)
    assert table_counts(empty_db) == dict.fromkeys(SEED_COUNTS, 0)


# ---------- append-only tables ----------

def _reset_row(conn, outcome, failure_reason):
    conn.execute(
        "INSERT INTO demo_resets (requested_at, outcome, requester_label, failure_reason, detail) "
        "VALUES ('2026-11-02T10:00:00+05:30', ?, 'cli', ?, '{}')",
        (outcome, failure_reason),
    )


def test_demo_resets_insert_rules(seeded_db):
    _reset_row(seeded_db, "success", None)
    for outcome in ("rejected", "rate_limited", "failed"):
        _reset_row(seeded_db, outcome, "invalid_token")
    assert seeded_db.execute("SELECT COUNT(*) FROM demo_resets").fetchone()[0] == 4


@pytest.mark.parametrize("outcome", ["rejected", "rate_limited", "failed"])
@pytest.mark.parametrize("reason", [None, "", "   "])
def test_demo_resets_non_success_requires_failure_reason(seeded_db, outcome, reason):
    with pytest.raises(sqlite3.IntegrityError):
        _reset_row(seeded_db, outcome, reason)


def test_demo_resets_success_must_not_have_failure_reason(seeded_db):
    with pytest.raises(sqlite3.IntegrityError):
        _reset_row(seeded_db, "success", "something")


def test_demo_resets_unknown_outcome_rejected(seeded_db):
    with pytest.raises(sqlite3.IntegrityError):
        _reset_row(seeded_db, "partial", "x")


def test_demo_resets_update_and_delete_fail(seeded_db):
    _reset_row(seeded_db, "success", None)
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        seeded_db.execute("UPDATE demo_resets SET requester_label = 'x'")
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        seeded_db.execute("DELETE FROM demo_resets")
    assert seeded_db.execute("SELECT COUNT(*) FROM demo_resets").fetchone()[0] == 1


def test_audit_events_update_blocked(seeded_db):
    seeded_db.execute(
        "INSERT INTO runs (run_id, run_number, file_name, file_hash, file_path, status, created_at) "
        "VALUES ('R-1', 1, 'a.pdf', 'h', '/a.pdf', 'QUEUED', '2026-11-02T10:00:00+05:30')"
    )
    seeded_db.execute(
        "INSERT INTO audit_events (run_id, type, payload, created_at) "
        "VALUES ('R-1', 'run_created', '{}', '2026-11-02T10:00:00+05:30')"
    )
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        seeded_db.execute("UPDATE audit_events SET type = 'error'")
