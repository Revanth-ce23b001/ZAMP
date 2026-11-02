# ClearMatch AP — Specification Freeze & Day 1 Build Brief

**Project:** Zamp AI Solutions Analyst case study · PS-1 Invoice Processing  
**Product:** ClearMatch AP  
**Specification:** `ClearMatch_AP_PRD_v1.2_Final.md` (PRD v1.2.1 Final) — single source of truth, frozen for build  
**Decision sign-off:** All eight provisional defaults OD-1 through OD-8 accepted by the product owner; implementation clarifications DEC-001 to DEC-005 recorded in `DECISIONS.md`  
**Build window:** 7 days · solo developer

## 1. Freeze notice

The eight provisional defaults in Section 18 of `ClearMatch_AP_PRD_v1.2_Final.md` are accepted without modification. Treat the PRD plus this sign-off as the implementation contract; where this brief and the PRD differ, the PRD wins. Do not reopen product decisions or expand scope during implementation unless a genuine contradiction or blocker is found. Record any such issue in `DECISIONS.md` before changing behaviour.

### Accepted decisions

| ID | Frozen decision |
|---|---|
| OD-1 | Inferred PO candidates use deterministic 60/30/10 scoring; no LLM involvement. Candidates scoring at least 50 are shown; the top two are flagged as plausible when their scores are within 10 points of each other. |
| OD-2 | PO total is pre-tax (`Σ quantity × unit price`); PO consumption uses invoice taxable value. GST and other charges are excluded. |
| OD-3 | Same vendor + normalised invoice number: original APPROVE → new submission REJECT (`CONFIRMED_DUPLICATE`); original HOLD → new submission HOLD (`POSSIBLE_DUPLICATE`). |
| OD-4 | Vendor name matches but GSTIN differs → HOLD (`VENDOR_NOT_FOUND`) with evidence that the name matched but GSTIN did not. |
| OD-5 | If a bank change cannot be verified through the trusted contact on record, the invoice remains HOLD. Record contact, channel and outcome; do not auto-reject. |
| OD-6 | Only HOLD can be overridden. APPROVE and REJECT are not overridable in the prototype. |
| OD-7 | Extract `document_type` and `currency` in P0; enforce `UNSUPPORTED_DOCUMENT` in P1. |
| OD-8 | No manual correction of extracted fields in MVP. Reviewers resolve a HOLD through the permitted override flow; field editing is P2. |

## 2. Non-negotiable product invariants

1. **AI extracts; deterministic rules decide; humans resolve exceptions.**
2. Never approve on model confidence alone. Critical fields require confidence ≥0.85 **and** deterministic corroboration.
3. All money is stored as integer paise. Convert to rupees only for display and extraction boundary handling.
4. Only `commit_approval` writes PO consumption.
5. `commit_approval` is idempotent and rechecks cumulative PO quantity/value inside a SQLite write transaction (`BEGIN IMMEDIATE`).
6. HOLD and REJECT never consume PO balance.
7. A repeated SHA-256 file hash returns the existing run; it does not create a new run or trigger extraction.
8. All reason codes are retained; final severity precedence is REJECT > HOLD > APPROVE.
9. Bank account numbers are masked (`XXXX XXXX 4471`) in API responses, SSE payloads, logs and UI. IFSCs are shown in full in the vendor master, invoice evidence and comparison panel; an IFSC is a public branch identifier (DEC-001). Trusted-contact phone and email are never returned by `GET /api/vendors`; they appear only in a run with `BANK_DETAILS_MISMATCH` (PRD FR-044).
10. No live payment, GST portal, bank ownership, ERP or GRN verification is claimed. This is a synthetic-data, two-way-match prototype.
11. Demo reset follows PRD FR-116 (DEC-002). The UI reset route is **intentionally public** and touches synthetic demo data only. Backend `POST /api/demo/reset` requires header `X-Demo-Reset-Token` matching `DEMO_RESET_TOKEN`; the token lives only in server-side env vars and is added by the Next.js route `frontend/app/api/demo/reset/route.ts`, never sent to the browser. The route only injects the token and forwards; it enforces nothing. The backend independently enforces typed confirmation (body `confirm: "RESET"`, else `400`) and both rate limits (1 successful reset per 30 seconds; 5 rejected attempts per IP per 15 minutes; else `429`). The reset is one atomic transaction. Every attempt writes one row to the append-only `demo_resets` table (`success`, `rejected`, `rate_limited`, `failed`); failures are logged in a separate transaction after rollback, with `failure_reason`.

## 3. Day 1 definition of done

Day 1 is complete only when all of the following are true:

- [ ] Repository has separate `backend/` and `frontend/` applications.
- [ ] FastAPI starts locally and `GET /api/health` returns a successful response.
- [ ] Next.js starts locally and renders a ClearMatch AP shell.
- [ ] `policy.yaml` contains every prototype assumption from PRD §14.1, with units and `[PA]` labels.
- [ ] Reason-code registry matches PRD §7.2 exactly: code, severity, stage, stage status, explanation template, next action and priority.
- [ ] SQLite schema is created from a versioned migration; money columns use integer paise.
- [ ] Seed loader inserts 7 vendors, 11 POs and PO lines, seeded invoice ledger entries and opening PO consumption from the `fixtures-v1` files, ignoring keys that start with `_`.
- [ ] `seed/test_fixtures.py` passes (16 tests).
- [ ] Running the seed command twice is safe: it does not duplicate seed records.
- [ ] A simple read endpoint returns vendor count, PO count and current PO balances.
- [ ] README documents setup, environment variables (including `DEMO_RESET_TOKEN`), seed/reset commands and run commands.
- [ ] `.env.example` lists `DEMO_RESET_TOKEN` with a placeholder value; the backend refuses to start without it.
- [ ] No real vendor/bank data or API secrets are committed.

## 4. Day 1 implementation sequence

### A. Repository scaffold

Structure follows PRD §12.4 (the PRD wins where they differ). Day 1 creates only the parts listed in this brief:

```text
clearmatch-ap/
  README.md
  .gitignore
  .env.example                  # DEMO_RESET_TOKEN placeholder; LLM keys left blank on Day 1
  DECISIONS.md
  ClearMatch_AP_PRD_v1.2_Final.md
  backend/
    app/
      main.py                   # FastAPI app, routes
      config.py                 # env vars
      policy.yaml               # all [PA] thresholds (PRD §12.4 location)
      db/                       # versioned migrations, schema, seed loader
      services/                 # Day 1: reset service only; worker/extraction/commit come later
      rules/
        reason_codes.py         # canonical reason-code registry (PRD §12.4)
      utils/                    # money (paise), normalise, masking
    seed/                       # fixtures-v1 (provided; load as-is)
      vendors.json
      purchase_orders.json
      ledger.json
      consumption.json
      test_fixtures.py
      README_FIXTURES.md
    storage/uploads/
    tests/
    requirements.txt
  frontend/
    app/
      api/demo/reset/route.ts   # server-side reset proxy (PRD FR-116); injects the token and forwards; no enforcement logic
    components/
    lib/api.ts
    package.json
```

Keep the backend as the authority for all business decisions. The frontend must not calculate approval outcomes or PO balances.

### B. `policy.yaml`

Move every prototype assumption from PRD §14.1 into configuration. At minimum include:

- buyer state code `29`; currency `INR`; tax regime `GST`
- 7 synthetic vendors; 11 seeded POs
- required invoice fields and upload limits (PDF, 10 MB, 5 pages)
- scan threshold: fewer than 50 non-whitespace characters per page on average
- arithmetic tolerance: ₹1 per equation
- other charges non-taxable unless invoice explicitly taxes them
- critical confidence threshold: `0.85`
- invoice date: not future and no more than 365 days old
- unit price: up to 1% above PO price; no lower-price limit
- cumulative quantity: no overrun
- cumulative PO value tolerance: `min(1% of PO total, ₹1,000)`, once per PO
- PO value basis: pre-tax taxable value
- duplicate normalisation: uppercase; remove spaces, hyphens, slashes and dots only
- possible duplicate: within 7 days, same vendor, total within ₹1, and same line signature
- revision suffixes: `R`, `REV`, `A`, `B`, `V2`
- inferred PO score: 60/30/10; candidate threshold 50; top-two closeness 10
- override reason minimum: 10 characters
- description normalisation: keep a decimal point between digits; remove other punctuation; join number to unit (A-32, DEC-003)
- demo reset rate limits: 1 successful reset per 30 seconds; 5 rejected attempts per IP per 15 minutes (A-33)
- demo reset confirmation: typed `RESET` (A-34)
- T-02 invoice date: 29 Oct 2026 (A-35; used by test fixtures and PDFs, not by business logic)
- processing: sequential single worker
- extraction cache survives demo reset

Every value must have a short explanatory comment and be labelled `[PA]`. Do not place secrets in this file.

### C. Reason-code registry

Create one canonical registry from PRD §7.2. Do not create codes ad hoc in business logic. The 17 codes are:

`CONFIRMED_DUPLICATE`, `POSSIBLE_DUPLICATE`, `POSSIBLE_REVISION`, `VENDOR_BLOCKED`, `VENDOR_NOT_FOUND`, `BANK_DETAILS_MISMATCH`, `MISSING_REQUIRED_FIELD`, `LOW_EXTRACTION_CONFIDENCE`, `UNSUPPORTED_DOCUMENT`, `PO_NOT_FOUND`, `PO_INFERRED`, `LINE_ITEM_UNMATCHED`, `UNIT_PRICE_VARIANCE`, `QUANTITY_EXCEEDS_PO`, `PO_BALANCE_EXCEEDED`, `ARITHMETIC_MISMATCH`, `TAX_REGIME_MISMATCH`.

Use the exact severity, stage, stage status, message template, next action and P0/P1 priority in PRD §7.2. Keep `UNSUPPORTED_DOCUMENT` and `TAX_REGIME_MISMATCH` as P1 rules.

### D. Database

Implement the PRD §8 entities and relationships. At minimum:

- `vendors`
- `purchase_orders`
- `po_lines`
- `runs`
- `extraction_cache` (PRD §8.4: keyed by `file_hash`; survives reset)
- `extractions`
- `decisions`
- `invoice_ledger`
- `po_consumption`
- `audit_events`
- `overrides`
- `demo_resets` (PRD §8.8): `reset_id` (autoincrement PK), `requested_at` (ISO 8601 with offset), `outcome` (`success` | `rejected` | `rate_limited` | `failed`), `requester_label` (required), `failure_reason` (required when outcome ≠ `success`, null on `success`, enforced by CHECK), `detail` (JSON, masked, never the token). `BEFORE UPDATE` and `BEFORE DELETE` triggers abort any change; reset only inserts into it and never clears it.

Use foreign keys and indexes for vendor/invoice-number duplicate lookup, file hash idempotency, run history and PO consumption lookup. Use a unique constraint on `runs.file_hash` and on `(ledger_id, po_line_id)` in `po_consumption`. Store timestamps as ISO 8601 with timezone offsets. Store money as integer paise.

Do not invent a parallel schema if one already exists in the PRD. Map the PRD field names directly and record any SQLite-specific adaptation in code comments.

### E. Seed data

Use the versioned synthetic fixture set `fixtures-v1` (`backend/seed/`), which contains the PRD §8 values plus explicitly annotated synthetic and derived values (DEC-004). Each record lists `_synthetic_fields` and `_derived_fields`; every other field comes from the PRD. Seed:

- Vendors `V001`–`V007`, including full synthetic bank details and trusted contacts.
- All 11 POs and their lines, aliases, quantities and pre-tax unit prices.
- Historical ledger entries: `INV-SL-0451`, `INV-SL-0467`, `INV-0912`, `INV-2041`, `INV-GC-0921`.
- Opening PO consumption corresponding to the historical approved invoices.

Load the fixtures as-is. Do not edit, regenerate or add seed values. Opening consumption is derived from ledger lines and must never be set independently. If a value is needed that exists in neither the PRD nor the fixtures, stop and report it.

## 5. First API endpoints

Day 1 endpoints only:

- `GET /api/health` → `{ "status": "ok" }`
- `GET /api/meta` → product version, policy version/hash and supported scope
- `GET /api/seed-status` → counts for vendors, POs, PO lines, ledger rows and consumption rows
- `GET /api/pos` → PO headers with total, consumed, remaining and allowed overrun (PRD §12.3 name)
- `POST /api/demo/reset` (PRD §12.3 name) may be stubbed on Day 1. If it exists, it must meet PRD FR-116 in full: header `X-Demo-Reset-Token`; body `{"confirm": "RESET", "requester_label": "..."}`; responses `200` / `400` (bad confirmation) / `401` (bad or missing token) / `429` (rate limited) / `500` (failed, data unchanged); atomic `BEGIN IMMEDIATE` transaction; one `demo_resets` row per attempt; uploaded files deleted only after commit.

`/api/health`, `/api/meta` and `/api/seed-status` are listed in PRD §12.3 (v1.2.1).

All monetary API fields should be integer paise plus a formatted display value if needed. Never return unmasked bank account numbers. Return IFSCs in full.

## 6. Day 1 tests

- Policy file loads and validates.
- Every reason-code key is unique and exactly matches the PRD set.
- Database migration runs from an empty database.
- Seed loader runs twice without duplicate rows.
- `extraction_cache` rows survive a reset (insert a dummy row in the test, reset, assert it is still there).
- All foreign keys pass.
- Every PO total equals the sum of its line quantity × unit price in paise.
- Opening PO consumption matches seeded approved invoices.
- PO remaining balance is never negative unless the explicit tolerance representation requires it.
- API health endpoint returns 200.
- Bank masking helper never returns a full account number, and returns the IFSC unchanged.
- `demo_resets` UPDATE and DELETE fail at the database level.
- If the reset endpoint is present: `401` without a valid token, `400` when `confirm` ≠ `RESET`, `429` on the second successful reset within 30 seconds and on the sixth rejected attempt per IP within 15 minutes; none of these change data; each writes one `demo_resets` row (`rejected` or `rate_limited`) with a `failure_reason`.
- If the reset endpoint is present: a forced mid-transaction failure leaves all data unchanged and writes a `failed` row with a non-empty `failure_reason`; a successful reset restores seed balances and keeps all earlier `demo_resets` rows and the extraction cache.
- `seed/test_fixtures.py` passes: foreign keys, PO totals, ledger-to-consumption reconciliation and preconditions for demo scenarios T-01 to T-05.

## 7. Day 1 stop line

Do **not** build invoice extraction, PDF generation, the rules engine, the full dashboard, reviewer workflow, SSE or deployment today. Those belong to later days. Build the data and configuration foundation cleanly, then move on.

## 8. Handoff prompt for Claude Code

> You are implementing ClearMatch AP, the Zamp PS-1 invoice-processing prototype. Read `ClearMatch_AP_PRD_v1.2_Final.md` (PRD v1.2 Final, the single source of truth) completely before coding, then `DECISIONS.md`. The product owner has explicitly accepted all eight Section 18 defaults OD-1 through OD-8 and the implementation clarifications DEC-001 to DEC-005. Treat them as final and implement them exactly.
>
> Implement **Day 1 only** using the Day 1 definition of done in `DAY1_FREEZE_AND_BUILD_BRIEF.md`: FastAPI + SQLite backend scaffold, Next.js frontend shell, policy.yaml, canonical reason-code registry (`backend/app/rules/reason_codes.py`), database migrations for all PRD §8 entities, idempotent seed loader for the `fixtures-v1` seed files in `backend/seed/` (provided; load as-is), health/meta/seed-status/`/api/pos` endpoints, tests and README.
>
> Preserve the PRD field names and rules. Money is integer paise. Never expose unmasked bank account numbers; show IFSCs in full. If you implement demo reset, follow PRD FR-116 exactly (token server-side only, typed `RESET`, rate limits, atomic transaction, append-only `demo_resets` with `failure_reason`). Do not invent or modify seed data; use the fixtures. Do not implement invoice extraction or business decision logic yet. Do not silently change scope or thresholds.
>
> Work in small steps: first inspect the repository and PRD, then propose the file plan, then implement. Run tests and both local servers. At the end, report files created, commands run, test results, any missing PRD data, and an explicit Day 1 pass/fail against every checklist item. If a detail is unavailable in both the PRD and the fixtures, stop and report it rather than guessing.

