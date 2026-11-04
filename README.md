# ClearMatch AP

Prototype for the Zamp PS-1 invoice-processing case study. An LLM reads an invoice PDF;
deterministic rules decide APPROVE, HOLD or REJECT; humans resolve exceptions.

**Vendor GSTIN was extracted incorrectly on 6 of 16 invoices. 16 of 16 decisions were correct.** The deterministic layer does not trust extraction on its own, so a field-level miss does not become a decision-level error.

**The architecture in three sentences.** The model only transcribes each invoice into a fixed schema; it has no
field through which to approve, hold or reject. Deterministic, versioned rules decide from that transcription plus the
vendor master and purchase orders, so a field the model gets wrong is either caught by a rule or does not change the
outcome. A person resolves every exception, and every decision records its evidence and the policy version behind it.

**Measured, not claimed** (synthetic demo dataset, 16 sample invoices, zero model calls; DEC-018):
- Field-level extraction: **154 of 161** checked fields exact (95.7%); 15 fields the PDFs do not establish are excluded, not guessed.
- Decisions: **16 of 16** correct (status and the full set of reason codes, against PRD §13).
- Full per-field and per-scenario tables: [`/accuracy`](https://clearmatch-ap.vercel.app/accuracy) (served from `backend/seed/extraction_accuracy.json`; regenerate with `python -m app.tools.measure_extraction_accuracy` from `backend/`).

- Specification: [`ClearMatch_AP_PRD_v1.2_Final.md`](ClearMatch_AP_PRD_v1.2_Final.md) (PRD v1.2.1, authoritative)
- Decision log: [`DECISIONS.md`](DECISIONS.md)
- Day 1 brief: [`DAY1_FREEZE_AND_BUILD_BRIEF.md`](DAY1_FREEZE_AND_BUILD_BRIEF.md)
- Seed data: [`backend/seed/README_FIXTURES.md`](backend/seed/README_FIXTURES.md) (`fixtures-v1`, synthetic)
- Wireframe (layout and navigation only; its sample data is placeholder): [`docs/wireframes/`](docs/wireframes/)

**Status: Day 6 (feature-complete for P0, deployed).** Live: https://clearmatch-ap.vercel.app
(backend https://clearmatch-ap-backend.onrender.com). All 16 sample invoices produce exactly the
PRD §13 outcomes. The full §17.3 run order works on the deployed link. Demo procedure:
[`docs/DEMO_SCRIPT.md`](docs/DEMO_SCRIPT.md). Interviewer PDFs and the policy demo:
[`docs/interviewer-kit/README.md`](docs/interviewer-kit/README.md).

All data is synthetic. Money is stored as integer paise.

## Deployed demo: read this first

The backend runs on Render's free tier and the frontend on Vercel. Two hosting behaviours matter:

- **Cold start.** The backend sleeps after about 15 minutes idle. The first request then takes 33–43 s (measured) while it wakes.
- **The database resets on every wake, restart or redeploy (DEC-016).** The disk is temporary, so runs and the `demo_resets` history do not survive. Seed data, PO balances and the extraction cache (from `backend/seed/extraction_cache.json`) are reloaded at startup. The 16 sample invoices therefore re-run with no API call. Reset history is append-only within one server lifetime, not across restarts.

**Warm-up before a demo:**
1. Open https://clearmatch-ap-backend.onrender.com/api/health and wait for `{"status":"ok"}`.
2. Open the app, click **Reset demo data**, type `RESET`.
3. Keep the tab active (any request at least every 10 minutes).

The GitHub keep-warm workflow helps but is not guaranteed: scheduled workflows can be delayed or skipped. Details and the
mid-interview contingency: [`docs/DEMO_SCRIPT.md`](docs/DEMO_SCRIPT.md).

**Live extraction is free-tier only (DEC-017).** Uncached PDFs go to `gemini-3.6-flash` (20 requests
a day), falling back to `gemini-3.5-flash-lite` (500 a day) on 429, 5xx, timeout or connection
errors. Render uses a Gemini key from a Google project nothing else uses. Measured on Render:
about 15 to 45 seconds per invoice on the primary, depending on how quickly the model responds;
10 s on the fallback.

## Setup

Requires Python 3.12+ (tested on 3.14.5) and Node 20.9+ (tested on 24.16).

```bash
# backend
cd backend
python -m pip install -r requirements.txt
cp ../.env.example .env                    # set DEMO_RESET_TOKEN and the LLM key; backend/.env is gitignored

# frontend
cd ../frontend
npm ci
cp .env.example .env.local                 # set BACKEND_URL and the SAME DEMO_RESET_TOKEN
```

### Environment variables

Backend (`backend/.env`, gitignored; never put real keys in `.env.example`):

| Variable | Required | Purpose |
|---|---|---|
| `DEMO_RESET_TOKEN` | **yes** | Shared secret for `POST /api/demo/reset`. The backend **refuses to start** without it. |
| `DATABASE_PATH` | no | SQLite file. Default `backend/data/clearmatch.db`. |
| `UPLOADS_DIR` | no | Uploaded PDFs. Default `backend/storage/uploads`. |
| `FRONTEND_ORIGIN` | no | The only origin allowed by CORS. Default `http://localhost:3000`. |
| `LLM_PROVIDER`, `LLM_MODEL` | for extraction | `gemini` and a pinned model, e.g. `gemini-3.6-flash`. If unset, every run ends in ERROR ("provider not configured"). |
| `GEMINI_API_KEY` | for extraction | Backend only; never sent to the browser, logged or stored. Free tier only; no paid or second-vendor provider is used (DEC-017). |
| `EXTRACTION_TEXT_CHAIN`, `EXTRACTION_VISION_CHAIN` | no | Ordered fallback chains (DEC-013, DEC-017), e.g. `gemini:gemini-3.6-flash,gemini:gemini-3.5-flash-lite`. Fall through only on 429/5xx/timeout/connection errors. Default: the single `LLM_PROVIDER:LLM_MODEL`. |
| `EXTRACTION_TIMEOUT_SECONDS` | no | Per-call provider timeout, default 120 (Render: 45). Each model keeps one retry before the next is tried. |
| `EXTRACTION_CACHE` | no | `on` (default) reuses extractions by file hash (FR-014). `off` disables cache reads and writes. |
| `CACHE_SNAPSHOT` | no | Cache snapshot loaded into an empty cache at startup (DEC-014). Default `backend/seed/extraction_cache.json`; `off` disables. |
| `SAMPLES_DIR` | no | Sample PDFs listed by `GET /api/samples`. Default `backend/samples`. |

Frontend (`frontend/.env.local`). Both are **server-only**; never give them a `NEXT_PUBLIC_` prefix:

| Variable | Purpose |
|---|---|
| `BACKEND_URL` | Backend origin for server code (reset route, policy page), e.g. `http://localhost:8000`. |
| `NEXT_PUBLIC_BACKEND_ORIGIN` | Backend origin for the **browser** (uploads, run data, the SSE live stream). Public, not a secret; inlined at build. The browser connects directly because a serverless proxy buffers streams; the backend's `FRONTEND_ORIGIN` must allow this app. |
| `DEMO_RESET_TOKEN` | Same value as the backend. Added by `app/api/demo/reset/route.ts`; never sent to the browser. |

## Commands

Backend (from `backend/`):

| Task | Command |
|---|---|
| Apply migrations | `python -m app.db.migrate` |
| Seed (idempotent; also migrates) | `python -m app.db.seed` |
| Run the API | `python -m uvicorn app.main:app --env-file .env --reload` (port 8000) |
| Run with the cache disabled | add `EXTRACTION_CACHE=off` to `backend/.env`, or set it in the shell first (PowerShell: `$env:EXTRACTION_CACHE="off"`) |
| All offline tests | `python -m pytest -q` (live provider tests are skipped) |
| PRD §13 scenarios only (served from the cache) | `python -m pytest tests/test_scenarios.py -q` |
| SSE and live-run tests (real uvicorn server) | `python -m pytest tests/test_live.py -q` |
| Browser end-to-end (builds the frontend; no provider calls) | `python -m pytest tests/test_e2e_browser.py --run-e2e -s` (screenshots in `backend/data/e2e/`) |
| Watch a run's live stream | `curl -N http://localhost:8000/api/runs/<run_id>/events` (add `-H "Last-Event-ID: 14"` to resume after event 14) |
| Rules unit tests only | `python -m pytest tests/test_rules.py tests/test_commit.py -q` |
| Warm the cache + export the snapshot (live calls) | `python -m app.tools.warm_cache` (`--only T06,T09 --force` to redo files) |
| Check cached extractions against the printed values | `python -m app.tools.extraction_report` |
| Live extraction tests (use the free-tier quota, ~10 min) | `python -m pytest tests/test_extraction_live.py --run-live -s` |
| Determinism only (3 live passes) | `python -m pytest tests/test_extraction_live.py --run-live -s -m determinism` |
| Fixture tests only | `python -m pytest seed/test_fixtures.py -q` |

Frontend (from `frontend/`):

| Task | Command |
|---|---|
| Dev server | `npm run dev` (port 3000) |
| Production build / start | `npm run build` then `npm start` |
| Type check | `npm run typecheck` |
| Token-leak check | `npm run test:token-leak` (builds with a sentinel token and fails if it appears anywhere in `.next`) |

The backend applies migrations and seeds an empty database on startup (NFR-12).

### Sample invoices (`invoice-templates/`)

The test PDFs in `backend/samples/` are generated from `invoice-templates/manifest.json`
(one entry per invoice; values exactly as printed) using four layouts in
`invoice-templates/layouts/`. Adding an invoice is a manifest entry, not code.

| Task | Command (from the repo root) |
|---|---|
| One-time setup | `python -m pip install -r backend/requirements.txt` then `python -m playwright install chromium` |
| Generate all PDFs + `SHA256SUMS` | `python invoice-templates/generate.py` (`--only T-01,T-05` for a subset) |
| Check rebuilds are byte-identical | `python invoice-templates/generate.py --check` |
| Interviewer kit (live, uncached PDFs) | `python invoice-templates/generate.py --manifest invoice-templates/extra/manifest.json` |
| Verify PDFs against the fixtures | `python invoice-templates/verify.py` (also run by `backend/tests/test_samples.py`) |

Values the PRD does not give are recorded in `DECISIONS.md` DEC-012 and listed per invoice
under `provenance.proposed` in the manifest. Byte-identical output holds for the same
machine and pinned Chromium build; the committed PDFs are the reference copies.

### Demo reset

Open **Reset demo data** in the top bar, type `RESET`, confirm. The browser calls the
Next.js route `/api/demo/reset`, which adds the token server-side and forwards to the
backend `POST /api/demo/reset`. The backend alone enforces the token, the typed
confirmation and the rate limits (1 successful reset per 30 s; 5 rejected attempts per IP
per 15 min). The reset is one atomic transaction; the extraction cache and the
`demo_resets` history are never cleared by a reset. On the free deployment both start empty or
seeded again after any restart (DEC-016). Wait at least 30 seconds between resets.

## API

| Method | Path | Returns |
|---|---|---|
| GET | `/api/health` | `{"status": "ok"}` |
| GET | `/api/meta` | Product version, `policy_version` (SHA-256 of `policy.yaml`), fixture version, scope flags |
| GET | `/api/seed-status` | Row counts: vendors, purchase_orders, po_lines, ledger, consumption |
| GET | `/api/pos` | Each PO with `po_total`, `approved` (consumed by committed approvals), `remaining`, `allowed_overrun` = min(1% of total, ₹1,000), `ceiling`. Each amount is `{paise, display}` |
| GET | `/api/vendors` | Vendor master with masked account (`XXXX XXXX 4471`) and full IFSC. No trusted-contact fields |
| POST | `/api/invoices` | Multipart upload. Validated before any run exists (PDF type and signature, ≤10 MB, ≤5 pages, not encrypted). Returns `{run_id, existing, run_number, run_display}`; a repeated file returns the existing run and is not reprocessed |
| GET | `/api/samples` | Sample PDFs in `backend/samples/` |
| POST | `/api/samples/{name}/process` | Submits a sample through the same upload path |
| POST | `/api/runs/{run_id}/retry` | Re-queues an ERROR run under the same `run_id` |
| GET | `/api/runs/{run_id}` | Run, stage results, extraction (account masked), decision with evidence; trusted-contact phone/email only for `BANK_DETAILS_MISMATCH` (FR-044) |
| GET | `/api/runs/{run_id}/events` | SSE (FR-114): `run`, `queued`, `stage_started`, `stage_completed`, `decision`, `commit`, `error`, `retry`, `end`. Replayed from stored audit events; resumes from `Last-Event-ID`; keepalive every 15 s; closes after the decision and any commit |
| GET | `/api/runs/{run_id}/file` | The uploaded PDF (preview) |
| POST | `/api/runs/{run_id}/override` | `{action: approve \| reject, reviewer_name, reason, codes_overridden, bank_verification?}`. HOLD only (OD-6); reason ≥ 10 chars; every HOLD code acknowledged; bank fields required for `BANK_DETAILS_MISMATCH`, approve only when `verified` (a `not verified` attempt is recorded, stays HOLD, 422). Approve goes through `commit_approval` and may be blocked |
| POST | `/api/runs/{run_id}/confirm-po` | `{po_number, reviewer_name, reason}` for `PO_INFERRED` / `PO_NOT_FOUND` with suggestions; re-runs stages 6-7 with the confirmed PO; commits if no codes remain |
| GET | `/api/runs` | Run list, filters `status` (APPROVE/HOLD/REJECT/ERROR/QUEUED/PROCESSING), `reason_code`, `po_number` |
| GET | `/api/runs/{run_id}/audit` | Ordered audit events and override records |
| GET | `/api/dashboard` | FR-111 metrics, recent runs, PO balances (remaining and allowed overrun as separate values) |
| GET | `/api/policy` | Every policy value with meaning and [PA] label, the 17 reason codes, P1 flags, the "does not verify" list |
| POST | `/api/demo/reset` | Header `X-Demo-Reset-Token`; body `{"confirm": "RESET", "requester_label": "..."}`. `200 {reset_id, restored}`, `400`, `401`, `429`, `500` |

## Layout

```
backend/
  app/
    main.py                FastAPI app and endpoints
    config.py              environment settings; refuses to start without DEMO_RESET_TOKEN
    policy.yaml            every [PA] assumption A-01..A-35 (+ other [PA] values in the PRD body)
    policy.py              loads policy.yaml; policy_version = SHA-256 of the file
    rules/reason_codes.py  the 17 reason codes from PRD §7.2
    services/reset.py      demo reset: checks, rate limits, atomic reload, demo_resets logging
    db/migrations/         versioned SQL migrations (0001_initial.sql)
    db/migrate.py          migration runner (records versions in schema_migrations)
    db/seed.py             idempotent fixtures-v1 loader (reused by reset)
    utils/                 money (paise, Indian grouping), normalisation, masking, timestamps
  seed/                    fixtures-v1 (provided; loaded as-is) + test_fixtures.py
  storage/uploads/
  tests/
frontend/
  app/                     work-queue, runs/[id] (decision), runs/[id]/live, pos, vendors, reports,
                           policy, demo-reset
    api/demo/reset/route.ts  server-side reset proxy: adds the token and forwards; enforces nothing
  components/              WorkQueue, LiveRun, decision/, ReviewPanel, PoTable, RunLauncher, TopNav
  lib/client.ts            browser calls to the backend; lib/api.ts server-only calls
  lib/display.ts           MIN_STAGE_DISPLAY_MS (live-screen pacing, display only)
  scripts/check-token-leak.mjs
invoice-templates/          manifest + layouts -> backend/samples; extra/ -> docs/interviewer-kit/pdfs
docs/                       DEMO_SCRIPT.md, MODEL_STRATEGY.md, interviewer-kit/, wireframes/
```

## Policy

Every threshold is a prototype assumption (**[PA]**) in `backend/app/policy.yaml`, not a
Zamp policy. Editing the file changes `policy_version` after a restart. The **Policy** page
shows every value, the 17 reason codes and what the prototype does not verify. To show a
threshold change flipping a decision (T-08, A-10 1% → 30%), see the interviewer-kit README §4.
