# Model strategy

*Interview and video preparation, not a contract. The binding decisions are DEC-013 (provider
chain) and DEC-014 (cache snapshot) in [`DECISIONS.md`](../DECISIONS.md).*

## The principle

**The LLM only reads the invoice. It never decides.** It returns the PRD §8.4 fields, each as
`{value, confidence}`, validated against a strict schema. Every APPROVE / HOLD / REJECT comes
from deterministic rules in `backend/app/rules/` driven by `policy.yaml`, with evidence for each
check. The prompt forbids computing, correcting or inferring anything that is not printed, and
contains no vendor list, PO numbers or expected answers (a test enforces this).

That rule is tested, not assumed. T-15 prints a total of ₹8,670 while its lines reconcile to
₹7,670. The model returns the printed 8,670 (confidence 0.99), so the rules raise
`ARITHMETIC_MISMATCH` instead of the model silently fixing the invoice. T-04 prints no PO
reference, and the model returns `null`, so the rules infer a candidate (`PO_INFERRED`) instead
of trusting a guess.

## The chain we chose

| Path | Chain today | Notes |
|---|---|---|
| Text (14 of 15 samples) | `gemini:gemini-3.6-flash` → `gemini:gemini-3.5-flash-lite` | Text layer read by pdfplumber, sent as text |
| Vision (scans, T-05) | `gemini:gemini-3.6-flash` → `gemini:gemini-3.5-flash-lite` | Pages rendered at 200 DPI, sent as images |

The fallback is set on Render (DEC-017) and costs nothing: on the free tier each model has its own
daily quota (20 requests per model per day on the project we tested). It is a weaker reader:
re-extracting all 15 samples with it reproduced 13 of 15 outcomes. T-03 and T-06 gained a spurious
`LINE_ITEM_UNMATCHED`, which holds an invoice; it never approved one wrongly.

**Why Gemini Flash:**
- It is hosted, so there is no GPU to run, and it has a free tier.
- It accepts a JSON Schema and returns schema-shaped JSON.
- It reads page images for the scan branch.
- It runs at temperature 0 with a fixed seed and minimal thinking.

On the 15 samples, every load-bearing field was exact, including the vision-path T-05. The known
miss is the vendor GSTIN on two layouts whose vendor block shares a text line with the buyer.
There the exact vendor *name* still identifies the vendor (FR-030), and the miss is recorded
rather than hidden.

**Why a chain, not a single model.** The brief's one hard requirement is that the process runs
live in the interview, and one free-tier model is a single point of failure. We hit three
availability failures while building:
- `gemini-2.5-flash` had been withdrawn for new users.
- `gemini-3.8-flash` returned "high demand" (503) for hours.
- The free tier allows 20 requests per model per day (429).

The chain falls through on 429, 5xx, timeouts and connection errors only. A schema failure stops
the chain, because that is a prompt defect that must surface, not be hidden by a second model.
The deployed backend runs on a Gemini key from a Google project used only by Render, so local
testing never spends the demo's quota. No paid or second-vendor provider is used (DEC-017).

**Why the demo makes zero provider calls.** The extractions for the 15 prepared samples ship as
a regenerated snapshot, `backend/seed/extraction_cache.json`. The UI shows the "Cached
extraction" badge. Any new PDF an interviewer uploads is extracted live through the chain.

**Cost.** About 800–1,700 input and 450–850 output tokens per invoice. That is roughly $0.003
per invoice at the paid list price (through 31 Dec 2026), and $0 on the free tier.

## Alternatives considered and rejected

| Option | Why not |
|---|---|
| **Fine-tuning a model** | There is no real training data, only fifteen self-authored samples. It would need a GPU to train and serve. And it would memorise our own test set, which is exactly what the tests exist to catch. |
| **Community OCR checkpoints** (e.g. `JackChew/Qwen2-VL-2B-OCR`) | No hosted provider, so we would host it. It needs about 16 GB of VRAM, which rules out a free-tier deployment. It emits text, not our schema, so a second step would still be needed to fill the fields. |
| **PaddleOCR-VL** | An excellent document parser, but it parses pages; it does not fill a schema. Parked as a possible Day 6 upgrade for the **scan branch only**: OCR the page, then hand the text to the same text chain. The digital path already has a perfect text layer. |
