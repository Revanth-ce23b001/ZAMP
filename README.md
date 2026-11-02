# ClearMatch AP

Prototype for the Zamp PS-1 invoice-processing case study. An LLM reads an invoice PDF;
deterministic rules decide APPROVE, HOLD or REJECT; humans resolve exceptions.

- Specification: [`ClearMatch_AP_PRD_v1.2_Final.md`](ClearMatch_AP_PRD_v1.2_Final.md) (PRD v1.2.1, authoritative)
- Decision log: [`DECISIONS.md`](DECISIONS.md)
- Day 1 brief: [`DAY1_FREEZE_AND_BUILD_BRIEF.md`](DAY1_FREEZE_AND_BUILD_BRIEF.md)
- Seed data: [`backend/seed/README_FIXTURES.md`](backend/seed/README_FIXTURES.md) (`fixtures-v1`, synthetic)
- Wireframe (layout and navigation only; its sample data is placeholder): [`docs/wireframes/`](docs/wireframes/)

**Status: Day 1 (1A + 1B).** Backend scaffold, policy, reason codes, schema, seed loader, read
endpoints and demo reset; Next.js shell with navigation, Policy page and Demo Reset page.
No upload, extraction, rules engine or business screens yet.

All data is synthetic. Money is stored as integer paise.

## Setup

Requires Python 3.12+ (tested on 3.14.5) and Node 20.9+ (tested on 24.16).

```bash
# backend
cd backend
python -m pip install -r requirements.txt
cp ../.env.example ../.env                 # set DEMO_RESET_TOKEN

# frontend
cd ../frontend
npm ci
cp .env.example .env.local                 # set BACKEND_URL and the SAME DEMO_RESET_TOKEN
```

### Environment variables

Backend (`.env` at the repo root):

| Variable | Required | Purpose |
|---|---|---|
| `DEMO_RESET_TOKEN` | **yes** | Shared secret for `POST /api/demo/reset`. The backend **refuses to start** without it. |
| `DATABASE_PATH` | no | SQLite file. Default `backend/data/clearmatch.db`. |
| `UPLOADS_DIR` | no | Uploaded PDFs. Default `backend/storage/uploads`. |
| `FRONTEND_ORIGIN` | no | The only origin allowed by CORS. Default `http://localhost:3000`. |
| `LLM_PROVIDER`, `LLM_MODEL`, `ANTHROPIC_API_KEY`, `GEMINI_API_KEY` | no | Extraction (Day 2). Unused today. |

Frontend (`frontend/.env.local`). Both are **server-only**; never give them a `NEXT_PUBLIC_` prefix:

| Variable | Purpose |
|---|---|
| `BACKEND_URL` | Backend origin, e.g. `http://localhost:8000`. |
| `DEMO_RESET_TOKEN` | Same value as the backend. Added by `app/api/demo/reset/route.ts`; never sent to the browser. |

## Commands

Backend (from `backend/`):

| Task | Command |
|---|---|
| Apply migrations | `python -m app.db.migrate` |
| Seed (idempotent; also migrates) | `python -m app.db.seed` |
| Run the API | `python -m uvicorn app.main:app --env-file ../.env --reload` (port 8000) |
| All tests | `python -m pytest -q` |
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
`demo_resets` history are never cleared. Wait at least 30 seconds between resets.

## API

| Method | Path | Returns |
|---|---|---|
| GET | `/api/health` | `{"status": "ok"}` |
| GET | `/api/meta` | Product version, `policy_version` (SHA-256 of `policy.yaml`), fixture version, scope flags |
| GET | `/api/seed-status` | Row counts: vendors, purchase_orders, po_lines, ledger, consumption |
| GET | `/api/pos` | Each PO with `po_total`, `approved` (consumed by committed approvals), `remaining`, `allowed_overrun` = min(1% of total, ₹1,000), `ceiling`. Each amount is `{paise, display}` |
| GET | `/api/vendors` | Vendor master with masked account (`XXXX XXXX 4471`) and full IFSC. No trusted-contact fields |
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
  app/                     work-queue, pos, vendors, reports (placeholders), policy, demo-reset
    api/demo/reset/route.ts  server-side reset proxy: adds the token and forwards; enforces nothing
  components/              TopNav, Placeholder
  lib/api.ts               server-only backend calls
  scripts/check-token-leak.mjs
docs/wireframes/
```

## Policy

Every threshold is a prototype assumption (**[PA]**) in `backend/app/policy.yaml`, not a
Zamp policy. Editing the file changes `policy_version` after a restart.
