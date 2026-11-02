# ClearMatch AP: Decision Log

Implementation contract: `ClearMatch_AP_PRD_v1.2_Final.md` (PRD v1.2.1 Final, frozen 2 Nov 2026; the filename is kept constant across patches) plus this log. The PRD is authoritative. Record any contradiction or blocker here **before** changing behaviour.

## Frozen product decisions (accepted 2 Nov 2026, no modifications)

| ID | Decision |
|---|---|
| OD-1 | Inferred PO candidates use deterministic 60/30/10 scoring; no LLM involvement. Candidates scoring at least 50 are shown; the top two are flagged as plausible when their scores are within 10 points of each other. |
| OD-2 | PO total is pre-tax (Σ quantity × unit price). PO consumption uses invoice taxable value. GST and other charges are excluded. |
| OD-3 | Same vendor + same normalised invoice number: original APPROVE → REJECT `CONFIRMED_DUPLICATE`; original HOLD → HOLD `POSSIBLE_DUPLICATE`. |
| OD-4 | Vendor name matches but GSTIN differs → HOLD `VENDOR_NOT_FOUND`, with evidence that the name matched and the GSTIN did not. |
| OD-5 | Bank change not verified through the trusted contact on record → invoice stays HOLD. Contact, channel and outcome are recorded. No automatic REJECT. |
| OD-6 | Only HOLD can be overridden. APPROVE and REJECT are final in the prototype. |
| OD-7 | `document_type` and `currency` are extracted in P0; the `UNSUPPORTED_DOCUMENT` rule is enforced in P1. |
| OD-8 | No manual correction of extracted fields in the MVP. HOLDs are resolved through the override flow; field editing is P2. |

## Implementation clarifications (resolved 2 Nov 2026; do not reopen product decisions)

| ID | Topic | Resolution | Status |
|---|---|---|---|
| DEC-001 | IFSC display | Mask the bank **account number** only (`XXXX XXXX 4471`). Show the **full IFSC** in the vendor master view, invoice evidence and comparison panel. An IFSC is a public branch identifier, not a secret, and T-03 depends on the reviewer seeing `ICIC0005678` against `HDFC0005512`. The Day 1 brief's instruction to mask IFSCs was wrong and has been removed. Matches PRD FR-041 as written. | Resolved |
| DEC-002 | Demo reset on the public deployment | Specified in PRD v1.2 FR-116 and §8.8. **Exposure:** the "Reset demo data" button and its Next.js route `frontend/app/api/demo/reset/route.ts` are intentionally public so the live demo can be reset; this is acceptable only because every record reset touches is synthetic. **Token:** backend `POST /api/demo/reset` requires header `X-Demo-Reset-Token` matching `DEMO_RESET_TOKEN` (required backend env var; backend refuses to start without it; constant-time compare). The token is held only in server-side env vars (no `NEXT_PUBLIC_` prefix), added by the Next.js route, and never reaches browser code. Its only job is to ensure every reset request reaches the backend through the Next.js route; it does not restrict who can press the button and does not by itself enforce confirmation or rate limits. The backend independently enforces both on every request. The Next.js route only injects the token and forwards. **Confirmation:** user types `RESET`; body `confirm` must equal `RESET`, else `400`. **Rate limits:** 1 successful reset per 30 seconds; 5 rejected attempts per IP per 15 minutes; else `429`. **Atomicity:** one `BEGIN IMMEDIATE` transaction (delete, reload `fixtures-v1`, FK check, count check, insert `success` row, commit); any error rolls back fully; uploaded files deleted only after commit. **Reset history (`demo_resets`):** `reset_id`, `requested_at`, `outcome` (`success` / `rejected` / `rate_limited` / `failed`), `requester_label`, `failure_reason` (required unless `success`), `detail`; UPDATE/DELETE blocked by triggers; never cleared by reset. `rejected`, `rate_limited` and `failed` rows are written in a separate transaction after rollback, so failures persist. **Responses:** 200 / 400 / 401 / 429 / 500. | Resolved |
| DEC-003 | Description normalisation with decimals | Keep a decimal point between two digits; replace every other `.` and all other punctuation with a space; join a number to the unit that follows it; collapse whitespace. `2.5 sqmm` → `2.5sqmm`, `12 mm` → `12mm`. Now written into PRD v1.2 FR-064 (seven acceptance cases), §9.5.1 and assumption A-32, so it is PRD behaviour rather than a deviation. Fixture aliases are stored in this form. | Resolved |
| DEC-004 | Seed data provenance | Seed data ships as versioned synthetic fixtures (`fixtures-v1`) in `backend/seed/`. Every record lists `_synthetic_fields` and `_derived_fields`; all other fields come from the PRD. Opening consumption is derived from ledger lines, never set independently. Claude Code uses the fixtures as-is and stops only if a needed value is missing from both the PRD and the fixtures. | Resolved |
| DEC-005 | Extraction cache vs reset | FR-014 requires the extraction cache to survive demo reset; FR-116 deletes `runs`. Resolution: the cache is its own table `extraction_cache` keyed by `file_hash` (PRD §8.4); `extractions` keeps the per-run copy. Reset never touches `extraction_cache`. SQLite adaptation only; no behaviour change. | Resolved |
| DEC-006 | `po_consumption` unique key | FR-092 says `(run_id, po_line_id)`; PRD §8.5 and the Day 1 brief say `(ledger_id, po_line_id)`. Implemented as `(ledger_id, po_line_id)`, because seed rows have `run_id` NULL and SQLite treats NULLs as distinct, so the FR-092 form would not enforce uniqueness on seed rows. Every run creates a ledger row, so FR-092's idempotency guarantee still holds. | Resolved |
| DEC-007 | `audit_events` mutability | FR-112 requires immutability; FR-116 requires reset to delete audit events. Implemented as a `BEFORE UPDATE` trigger that blocks edits, with `DELETE` permitted so reset can clear them. `demo_resets` blocks both. | Resolved |
| DEC-008 | Trusted contact in `GET /api/vendors` | FR-044 excludes phone and email; the vendor list also omits the contact name, because FR-044 confines the contact to the bank banner and override panel, and the wireframe's Vendors screen states "no trusted-contact details here". | Resolved |
| DEC-009 | Ledger tax columns | `ledger.json` carries `cgst`, `sgst` and `igst`, which PRD §8.5 does not list. Added as nullable INTEGER paise columns so the fixtures load unchanged. No rule reads them yet. | Resolved |
| DEC-010 | UI information architecture | The wireframe uses four global tabs (Work Queue, POs, Vendors, Reports) and separate PO Details, Audit Trail and Demo Reset screens, where PRD §10 describes Dashboard, Inbox and Policy navigation. Day 1B builds the wireframe's chrome and also keeps the FR-117 Assumptions and Policy page, which the wireframe omits and which is P0 and used in the demo script (§17.1). Per-screen scope is settled on the UI day, not now. | Resolved |

## Alignment notes (applied)

- All references point to `ClearMatch_AP_PRD_v1.2_Final.md`. Earlier filenames (`Zamp_PS1_Final_PRD.md`, v1.0 and v1.1) are superseded.
- PRD v1.2 also fixes the T-02 invoice date at 29 Oct 2026 (A-35) and excludes trusted-contact phone/email from `GET /api/vendors` (FR-044). Neither changes a business rule or test outcome.
- Endpoint names follow PRD §12.3, including `POST /api/demo/reset` and `GET /api/pos`. `GET /api/health`, `GET /api/meta` and `GET /api/seed-status` are now in §12.3 (v1.2.1).
- Frontend route and backend endpoint share the path `/api/demo/reset` on different origins by design (PRD FR-116). Rename the frontend route if both are ever served from one origin.
- The Day 1 brief is named `DAY1_FREEZE_AND_BUILD_BRIEF.md`, matching the handoff prompt.
