-- 0001_initial: ClearMatch AP schema (PRD v1.2.1 §8).
--
-- Conventions
--   * Money is INTEGER paise everywhere (columns named total, value, unit_price, po_total, ...).
--   * Timestamps are TEXT in ISO 8601 with an explicit offset, e.g. 2026-11-05T11:02:10+05:30.
--   * JSON values are TEXT holding JSON (SQLite has no JSON column type); json_valid() CHECKs guard them.
--   * Foreign keys are enforced per connection with PRAGMA foreign_keys = ON (set by app/db/connection.py;
--     the pragma cannot be set inside this migration's transaction).
--   * Booleans are INTEGER 0/1 with CHECK (SQLite adaptation; no native BOOLEAN).
--   * Enums are TEXT with CHECK (SQLite adaptation; no native ENUM).
--   * Quantities use NUMERIC affinity: whole numbers are stored as INTEGER, fractional ones as REAL
--     (PRD types quantity as "number").
-- The migration runner wraps this file in one transaction and records it in schema_migrations.

-- ---------------------------------------------------------------- reference data (§8.2, §8.3)

CREATE TABLE vendors (
    vendor_id              TEXT PRIMARY KEY,
    legal_name             TEXT NOT NULL,
    gstin                  TEXT NOT NULL UNIQUE,
    state_code             TEXT NOT NULL,
    state_name             TEXT NOT NULL,
    bank_account_number    TEXT NOT NULL,          -- full, synthetic; never leaves the backend unmasked (FR-041)
    ifsc                   TEXT NOT NULL,
    trusted_contact_name   TEXT NOT NULL,
    trusted_contact_phone  TEXT NOT NULL,          -- never returned by GET /api/vendors (FR-044)
    trusted_contact_email  TEXT,                   -- never returned by GET /api/vendors (FR-044)
    status                 TEXT NOT NULL CHECK (status IN ('active', 'blocked')),
    CHECK (length(gstin) = 15),
    CHECK (state_code = substr(gstin, 1, 2))
);

CREATE TABLE purchase_orders (
    po_number  TEXT PRIMARY KEY,
    vendor_id  TEXT NOT NULL REFERENCES vendors (vendor_id),
    po_date    TEXT NOT NULL,                      -- ISO date YYYY-MM-DD
    currency   TEXT NOT NULL CHECK (currency = 'INR'),           -- A-01
    po_total   INTEGER NOT NULL CHECK (po_total >= 0)            -- paise; derived = sum(qty x unit_price), pre-tax (OD-2)
);
CREATE INDEX idx_purchase_orders_vendor ON purchase_orders (vendor_id);

CREATE TABLE po_lines (
    po_line_id   TEXT PRIMARY KEY,
    po_number    TEXT NOT NULL REFERENCES purchase_orders (po_number),
    line_no      INTEGER NOT NULL CHECK (line_no >= 1),
    item_code    TEXT NOT NULL,
    description  TEXT NOT NULL,
    aliases      TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(aliases)),   -- JSON array, stored already normalised
    uom          TEXT,
    quantity     NUMERIC NOT NULL CHECK (quantity > 0),
    unit_price   INTEGER NOT NULL CHECK (unit_price >= 0),               -- paise, pre-tax
    UNIQUE (po_number, line_no)
);

-- ---------------------------------------------------------------- processing (§8.4, §8.6, §8.7)

CREATE TABLE runs (
    run_id         TEXT PRIMARY KEY,               -- "sequential display number + UUID" (§8.6)
    run_number     INTEGER NOT NULL UNIQUE,        -- the sequential display number (Run #14)
    file_name      TEXT NOT NULL,
    file_hash      TEXT NOT NULL UNIQUE,           -- SHA-256; idempotency control (FR-003)
    file_path      TEXT NOT NULL,
    status         TEXT NOT NULL CHECK (status IN ('QUEUED', 'PROCESSING', 'DECIDED', 'ERROR')),
    ingest_method  TEXT CHECK (ingest_method IS NULL OR ingest_method IN ('text', 'vision')),
    stage_results  TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(stage_results)),
    error_message  TEXT,
    created_at     TEXT NOT NULL,
    started_at     TEXT,
    completed_at   TEXT,
    duration_ms    INTEGER CHECK (duration_ms IS NULL OR duration_ms >= 0)
);
CREATE INDEX idx_runs_status ON runs (status);
CREATE INDEX idx_runs_created_at ON runs (created_at);

-- Reusable extraction keyed by file hash. Deliberately has NO foreign key to runs:
-- demo reset deletes runs but must preserve this table (FR-014, A-30, DEC-005).
CREATE TABLE extraction_cache (
    file_hash      TEXT PRIMARY KEY,
    provider       TEXT NOT NULL,
    model          TEXT NOT NULL,
    ingest_method  TEXT NOT NULL CHECK (ingest_method IN ('text', 'vision')),
    payload        TEXT NOT NULL CHECK (json_valid(payload)),
    extracted_at   TEXT NOT NULL
);

-- Per-run copy of the extraction used (§8.4 "stored alongside, per run").
CREATE TABLE extractions (
    run_id         TEXT PRIMARY KEY REFERENCES runs (run_id),
    file_hash      TEXT NOT NULL,
    provider       TEXT NOT NULL,
    model          TEXT NOT NULL,
    ingest_method  TEXT NOT NULL CHECK (ingest_method IN ('text', 'vision')),
    payload        TEXT NOT NULL CHECK (json_valid(payload)),
    extracted_at   TEXT NOT NULL,
    cached         INTEGER NOT NULL CHECK (cached IN (0, 1))
);

CREATE TABLE decisions (
    decision_id     TEXT PRIMARY KEY,
    run_id          TEXT NOT NULL UNIQUE REFERENCES runs (run_id),
    status          TEXT NOT NULL CHECK (status IN ('APPROVE', 'HOLD', 'REJECT')),
    reason_codes    TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(reason_codes)),
    evidence        TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(evidence)),
    notes           TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(notes)),
    summary         TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(summary)),
    next_actions    TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(next_actions)),
    matched_po      TEXT REFERENCES purchase_orders (po_number),
    po_candidates   TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(po_candidates)),
    decided_by      TEXT NOT NULL CHECK (decided_by IN ('rules', 'rules_after_review', 'reviewer')),
    policy_version  TEXT NOT NULL,                 -- SHA-256 of policy.yaml
    committed       INTEGER NOT NULL DEFAULT 0 CHECK (committed IN (0, 1)),
    committed_at    TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    CHECK ((committed = 1) = (committed_at IS NOT NULL))
);

-- ---------------------------------------------------------------- ledger and consumption (§8.5)

CREATE TABLE invoice_ledger (
    ledger_id                  TEXT PRIMARY KEY,
    run_id                     TEXT UNIQUE REFERENCES runs (run_id),   -- null for seeded entries; one row per run
    source                     TEXT NOT NULL CHECK (source IN ('run', 'seed')),
    vendor_id                  TEXT NOT NULL REFERENCES vendors (vendor_id),
    invoice_number_raw         TEXT NOT NULL,
    invoice_number_normalised  TEXT NOT NULL,
    invoice_date               TEXT NOT NULL,
    total                      INTEGER NOT NULL,          -- paise
    taxable_value              INTEGER NOT NULL,          -- paise
    -- cgst/sgst/igst: not in the PRD §8.5 table, but present in the fixtures-v1 ledger records
    -- (marked _derived_fields). Stored so the fixtures load as-is (DEC-004). Nullable, paise.
    cgst                       INTEGER,
    sgst                       INTEGER,
    igst                       INTEGER,
    po_number                  TEXT REFERENCES purchase_orders (po_number),
    line_signature             TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(line_signature)),
    status                     TEXT NOT NULL CHECK (status IN ('APPROVE', 'HOLD', 'REJECT')),
    duplicate_of               TEXT REFERENCES invoice_ledger (ledger_id),
    CHECK ((source = 'seed') = (run_id IS NULL))
);
-- Duplicate lookup (FR-051..FR-054).
CREATE INDEX idx_ledger_vendor_invoice_no ON invoice_ledger (vendor_id, invoice_number_normalised);

-- Written only by commit_approval (FR-090) and the seed loader.
CREATE TABLE po_consumption (
    consumption_id  INTEGER PRIMARY KEY AUTOINCREMENT,      -- surrogate key (SQLite adaptation; PRD lists none)
    run_id          TEXT REFERENCES runs (run_id),          -- null for seed
    ledger_id       TEXT NOT NULL REFERENCES invoice_ledger (ledger_id),
    po_line_id      TEXT NOT NULL REFERENCES po_lines (po_line_id),
    quantity        NUMERIC NOT NULL CHECK (quantity > 0),
    value           INTEGER NOT NULL CHECK (value >= 0),    -- paise; taxable value allocated to the line
    committed_at    TEXT NOT NULL,
    -- Idempotency key per PRD §8.5. (FR-092 words it as (run_id, po_line_id); ledger rows are 1:1
    -- with runs, and seed rows have run_id NULL, so (ledger_id, po_line_id) covers both.)
    UNIQUE (ledger_id, po_line_id)
);
CREATE INDEX idx_po_consumption_po_line ON po_consumption (po_line_id);
CREATE INDEX idx_po_consumption_run ON po_consumption (run_id);

-- ---------------------------------------------------------------- review and audit (§8.8)

CREATE TABLE overrides (
    override_id        TEXT PRIMARY KEY,
    run_id             TEXT NOT NULL REFERENCES runs (run_id),
    action             TEXT NOT NULL CHECK (action IN ('approve', 'reject', 'confirm_po')),
    original_status    TEXT NOT NULL CHECK (original_status IN ('APPROVE', 'HOLD', 'REJECT')),
    new_status         TEXT NOT NULL CHECK (new_status IN ('APPROVE', 'HOLD', 'REJECT')),
    codes_overridden   TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(codes_overridden)),
    reviewer_name      TEXT NOT NULL CHECK (length(trim(reviewer_name)) > 0),
    reason             TEXT NOT NULL,               -- minimum length is policy (A-29), enforced in the app
    bank_verification  TEXT CHECK (bank_verification IS NULL OR json_valid(bank_verification)),
    confirmed_po       TEXT REFERENCES purchase_orders (po_number),
    commit_result      TEXT NOT NULL CHECK (commit_result IN ('committed', 'blocked', 'not_applicable')),
    block_reason       TEXT,
    created_at         TEXT NOT NULL
);
CREATE INDEX idx_overrides_run ON overrides (run_id);

CREATE TABLE audit_events (
    event_id    INTEGER PRIMARY KEY AUTOINCREMENT,   -- monotonic, gives the chronological order
    run_id      TEXT NOT NULL REFERENCES runs (run_id),
    type        TEXT NOT NULL CHECK (type IN ('run_created', 'stage_started', 'stage_completed',
                                              'extraction_cached', 'extraction_called', 'decision',
                                              'override', 'commit', 'commit_blocked', 'error', 'retry')),
    payload     TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(payload)),   -- masked
    created_at  TEXT NOT NULL
);
CREATE INDEX idx_audit_events_run ON audit_events (run_id, event_id);

-- Events are immutable (FR-112). UPDATE is blocked; DELETE is not, because demo reset (FR-116)
-- explicitly deletes audit events along with runs.
CREATE TRIGGER audit_events_no_update
BEFORE UPDATE ON audit_events
BEGIN
    SELECT RAISE(ABORT, 'audit_events is append-only');
END;

-- ---------------------------------------------------------------- demo reset history (§8.8, FR-116)

-- Append-only, never cleared by reset.
CREATE TABLE demo_resets (
    reset_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    requested_at     TEXT NOT NULL,
    outcome          TEXT NOT NULL CHECK (outcome IN ('success', 'rejected', 'rate_limited', 'failed')),
    requester_label  TEXT NOT NULL CHECK (length(trim(requester_label)) > 0),
    failure_reason   TEXT,
    detail           TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(detail)),   -- masked; never the token
    -- failure_reason is required (non-blank) unless outcome = 'success', and must be NULL on success.
    CHECK (
        (outcome = 'success' AND failure_reason IS NULL)
        OR (outcome <> 'success' AND failure_reason IS NOT NULL AND length(trim(failure_reason)) > 0)
    )
);

CREATE TRIGGER demo_resets_no_update
BEFORE UPDATE ON demo_resets
BEGIN
    SELECT RAISE(ABORT, 'demo_resets is append-only');
END;

CREATE TRIGGER demo_resets_no_delete
BEFORE DELETE ON demo_resets
BEGIN
    SELECT RAISE(ABORT, 'demo_resets is append-only');
END;
