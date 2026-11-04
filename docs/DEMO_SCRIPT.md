# ClearMatch AP: demo script

- Live app: https://clearmatch-ap.vercel.app
- Backend health check: https://clearmatch-ap-backend.onrender.com/api/health
- Test PDFs: `backend/samples/` (15 rehearsed, cached) and `docs/interviewer-kit/pdfs/` (3 live). Index: [`interviewer-kit/README.md`](interviewer-kit/README.md)

## 1. Warm-up

The backend runs on Render's free tier. Two behaviours matter:

- **Cold start.** After about 15 minutes without traffic the server sleeps. The first request then waits for it to wake (measured 33–43 s).
- **Reset on wake (DEC-016).** Every wake-up, restart or redeploy starts a fresh database: seed data back, no runs, empty reset history. The extraction cache reloads from the committed snapshot, so the 15 rehearsed invoices still need no API call.

The GitHub keep-warm job pings every 10 minutes when GitHub runs it. Its schedule is best effort; on 3–4 Nov 2026 it went over an hour without firing. Do not rely on it.

**The day before**
1. Upload `docs/interviewer-kit/pdfs/B02_template_prestige_PWR-TPL-0001.pdf` once. It has never been run live; expect APPROVE. Reset afterwards.
2. Do no other live uploads on the demo key that day. gemini-3.6-flash allows 20 requests a day; gemini-3.5-flash-lite allows 500. Local testing uses a different Google project and never touches the Render key (DEC-017).

**10 minutes before**
1. Open the health URL above and wait for `{"status":"ok"}`. If it was asleep, this takes up to about 45 s.
2. Open the live app. Click **Reset demo data**, type `RESET`, confirm.
3. Open **Work Queue**: the KPIs read 0 and the queue says "No invoices processed yet".
4. Optional check: run T-01 from **Run sample**, then reset again. Wait 30 s between resets; that is the rate limit.
5. Keep the tab open, and click something at least every 10 minutes until you start. Each request resets the sleep timer.

## 2. Five-minute video (PRD §17.1)

Reset first. Every run below is a rehearsed sample: about a second to decide. The live screen
deliberately holds each stage on "Running" for 0.35 s so it can be seen (display only).

| Time | Beat | Do | Say |
|---|---|---|---|
| 0:00–0:30 | Context | Open **Policy**: scroll past the A-01…A-35 table and the 17 reason codes | "Accounts payable teams check every invoice against a vendor master and a purchase order before paying. ClearMatch splits that in two: AI reads the invoice; deterministic rules decide. Every threshold is here, labelled as a prototype assumption, and every decision records which policy version made it." |
| 0:30–1:45 | T-01 happy path | Work Queue → Run sample `T01_…` → watch seven stages → **View decision** | "Seven stages: ingest, extract, validate, vendor and bank, duplicate, PO match, decide. Extracted data: every field with its confidence. 'TMT Steel Rod 12 mm' pairs with PO line TMT-12 through an alias, not fuzzy matching. The four arithmetic checks reconcile, including a ₹5,000 discount, freight and a 40-paise round-off. PO-1042 had ₹1,25,000 remaining; this approval consumes ₹95,000, leaving ₹30,000." |
| 1:45–2:45 | T-02 split-PO overrun | Run `T02_…` → decision → PO match tab | "Third invoice against PO-1041. Two earlier invoices already used ₹2,20,000 of ₹3,00,000. This one is ₹1,00,000 pre-tax for 100 units; only 80 units remain. Quantity: 220 approved plus 100 invoiced exceeds 300 by 20. Value: over the ceiling, which is the PO total plus one tolerance of ₹1,000, applied once per PO, not per invoice. HOLD, with both codes and the numbers behind them." |
| 2:45–3:45 | T-03 bank mismatch + override | Run `T03_…` → security banner → Vendor & bank tab → review panel: record a *not verified* attempt, then *verified* → Approve | "The account ends 4471 on both sides, so a last-four check would pass. We compare the full account and the IFSC: ICIC0005678 against HDFC0005512 on the master. The banner tells the reviewer to call the trusted contact on the vendor master, never the number printed on the invoice. An unverified attempt is recorded and the invoice stays on hold. Verified: the override goes through the same commit check, and the balance updates." |
| 3:45–4:30 | Reports + audit | **Reports** → click a reason → back → the T-03 decision → Audit tab | "Counts by status, auto-approval rate, value on hold, exceptions by reason. Each reason opens the filtered queue. PO balances show remaining and the allowed overrun as separate numbers. The audit trail has the override: who, why, the verification channel and outcome, and whether the commit went through." |
| 4:30–5:00 | Limits and next | **Policy** → "What this prototype does not verify" | Section 5 below, then: "Next would be goods-receipt matching, ERP posting and email ingestion." |

## 3. Live interview order (PRD §17.3)

Reset, then:

1. **T-01**: APPROVE; PO-1042 ₹30,000 remaining.
2. **T-02**: HOLD, `QUANTITY_EXCEEDS_PO` and `PO_BALANCE_EXCEEDED`.
3. **T-03**: HOLD, `BANK_DETAILS_MISMATCH`. Show T-23 first (not verified: stays HOLD), then T-22 (verified: APPROVE, committed).
4. **T-04**: HOLD, `PO_INFERRED`. Candidates PO-1031 (score 100) and PO-1036 (70). **Confirm PO** PO-1031 (T-24): APPROVE.
5. **T-05**: the photographed scan. REJECT, `CONFIRMED_DUPLICATE` of INV-2041 (approved 12 Oct).

Backups, as asked:

| Run | Result | Point to make |
|---|---|---|
| T-06 | APPROVE, note "Similar invoice exists: INV-0912" | Weak signals are noted, not held |
| T-07 | HOLD `POSSIBLE_REVISION` | A revision suffix holds; it does not reject |
| T-08 | HOLD `UNIT_PRICE_VARIANCE` | +25% vs +1%. Also the policy demo: change A-10 to 30% and it approves (interviewer-kit README §4) |
| T-09 | HOLD `VENDOR_NOT_FOUND` | Later stages "Not checked: vendor not identified" |
| T-10 | Drop the T-01 PDF again | Opens the existing run with a banner: no new run, no API call, balance unchanged |
| T-25 | (a) HOLD bank mismatch; (b) APPROVE; override (a) | The override is blocked at commit: 50 + 60 > 100 units. HOLD with `QUANTITY_EXCEEDS_PO`, `PO_BALANCE_EXCEEDED` added |

On 4 Nov 2026 this order was walked five times on the deployed link, with a reset before each.
Wherever the test script completed a step, the decisions, reason codes, evidence and PO balances
were identical across passes. Two passes had browser timeouts (a page taking over 20 s), which left
one step undone; no decision differed.

**If the interviewer uploads their own invoice:** it is extracted live. Say: "This one isn't cached,
so it's a live call to Gemini on the free tier. It can take up to about 45 seconds." Measured on
Render: 41 s on gemini-3.6-flash, 10 s on gemini-3.5-flash-lite. If the primary model is rate-limited
or down, the chain falls through to flash-lite automatically, and the audit tab shows it. If both
fail, the run shows ERROR with a **Retry** button. Nothing is decided by the model either way.

## 4. Contingency: the backend slept mid-interview

**What you see:** a page spins for up to about 45 s. After that the Work Queue is empty, and earlier
runs return "not found".

**Why:** the free server slept and woke with a fresh database (DEC-016). Seed data and PO balances
are back to the start. The extraction cache reloaded from the committed snapshot.

**What to do:**
1. Wait for the page to load. Don't refresh repeatedly; the first request is already waking it.
2. Re-run whatever you were showing from **Run sample**. Each rehearsed invoice decides again in about a second, with no API call and the same result.
3. If you were mid-sequence (e.g. T-25 depends on T-25b), re-run the earlier steps in order. No reset is needed: the database is already at seed.

**What to say:** "The free hosting tier put the server to sleep and it restarted with a clean
database; that's a hosting limit, not the app. The extractions are cached in the repository, so
replaying them costs nothing and gives the same decisions. A production deployment would keep a
persistent database."

## 5. Limitations (PRD §14.2), as you would say them

- "It doesn't check the live GST portal. It checks the GSTIN format and whether it matches our vendor master, and the screen says exactly that. It never says 'GST verified'."
- "It can't prove who owns a bank account. It compares the full account number and IFSC with the vendor master. When they differ, a person has to verify with the trusted contact on file before anything is approved."
- "It's a two-way match, invoice against purchase order. There's no goods receipt, so it can't say the goods arrived, and the UI labels it 'Two-way match'."
- "There's no ERP. Vendors, POs and invoice history are a local synthetic dataset."
- "Nothing is paid. APPROVE means 'cleared for payment' in this prototype."
- On hosting, if asked: "It runs on free tiers. The server sleeps when idle and restarts with a clean database, and live extraction is limited to the free Gemini quota. Both are deployment choices, not design limits."
