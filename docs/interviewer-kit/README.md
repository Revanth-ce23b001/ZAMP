# Interviewer kit (PRD §17.5)

Everything here is synthetic. Two folders of PDFs are ready to drag onto the Work Queue's drop zone.

## 1. The rehearsed set: `backend/samples/` (16 PDFs)

These are served from the extraction cache (DEC-014). They make **no API call**, decide in about a
second, and show a "Cached extraction" badge. They also appear in the **Run sample** picker.

| File | PRD test | Expected outcome | What it shows |
|---|---|---|---|
| `T01_happy_path_SLST-2026-0512.pdf` | T-01 | APPROVE | Alias pairing, E1–E4 with round-off, PO-1042 balance ₹1,25,000 → ₹30,000 remaining |
| `T02_split_po_overrun_INV-SL-0489.pdf` | T-02 | HOLD `QUANTITY_EXCEEDS_PO`, `PO_BALANCE_EXCEEDED` | Third invoice against PO-1041: cumulative quantity and value over the once-per-PO ceiling |
| `T03_bank_mismatch_CPPL-7781.pdf` | T-03 | HOLD `BANK_DETAILS_MISMATCH` | Same last four digits, different IFSC; security banner with the trusted contact; override flow (T-22/T-23) |
| `T04_inferred_po_NIT-1123.pdf` | T-04 | HOLD `PO_INFERRED` | No PO printed; candidates PO-1031 (100) and PO-1036 (70); Confirm PO (T-24) → APPROVE |
| `T05_scanned_duplicate_INV-2041.pdf` | T-05 | REJECT `CONFIRMED_DUPLICATE` | A photographed scan (vision path) of an already-approved invoice |
| `T06_recurring_rent_INV-1004.pdf` | T-06 | APPROVE, note "Similar invoice exists: INV-0912" | A weak signal is noted, not held |
| `T07_revision_INV-2041-R.pdf` | T-07 | HOLD `POSSIBLE_REVISION` | Revision suffix on a known invoice number |
| `T08_unit_price_variance_KE-2026-0311.pdf` | T-08 | HOLD `UNIT_PRICE_VARIANCE` | ₹1,250 vs PO ₹1,000 (+25% vs +1% allowed). Used for the policy demo below |
| `T09_unknown_vendor_HT-0425.pdf` | T-09 | HOLD `VENDOR_NOT_FOUND` | Vendor not in master; later stages Not Checked |
| `T12_unmatched_line_INV-SL-0493.pdf` | T-12 | HOLD `LINE_ITEM_UNMATCHED` | A line that pairs with no PO line |
| `T15_arithmetic_mismatch_INV-SL-0497.pdf` | T-15 | HOLD `ARITHMETIC_MISMATCH` | Printed total off by exactly ₹1,000 |
| `T16_possible_duplicate_INV-GC-0927.pdf` | T-16 | HOLD `POSSIBLE_DUPLICATE` | Same vendor, total and lines within 7 days, different number |
| `T17_multiple_po_candidates_NIT-1131.pdf` | T-17 | HOLD `PO_INFERRED` | No PO printed; 2 × LAP-14 + 2 × MON-24 fit both PO-1031 and PO-1036 (score 100 each), so both are shown as plausible (OD-1) |
| `T18_blocked_vendor_MLG-1187.pdf` | T-18 | REJECT `VENDOR_BLOCKED` | Blocked vendor |
| `T25a_bank_mismatch_override_KE-2026-0312.pdf` | T-25 (a) | HOLD `BANK_DETAILS_MISMATCH` | Run before (b); its override is later blocked at commit |
| `T25b_consumes_balance_KE-2026-0313.pdf` | T-25 (b) | APPROVE | Consumes PO-1045, so overriding (a) afterwards stays HOLD with `QUANTITY_EXCEEDS_PO`, `PO_BALANCE_EXCEEDED` |

T-10 needs no file: drop `T01_…` again and the existing run opens with a banner (no new run, no API call).

## 2. Live-extraction PDFs: `docs/interviewer-kit/pdfs/` (3 PDFs)

These are **not cached**. Uploading one makes a **live** Gemini call through the free-tier chain
(DEC-017): `gemini-3.6-flash`, falling back to `gemini-3.5-flash-lite` on 429, 5xx or timeout.
Expect roughly 10–45 seconds (measured on Render, 4 Nov 2026: 41 s on gemini-3.6-flash, 10 s
on flash-lite).

| File | Expected outcome | Purpose |
|---|---|---|
| `L01_live_unknown_vendor_DOS-2026-118.pdf` | HOLD `VENDOR_NOT_FOUND`, no PO balance change | Proves the live path. Verified on the deployed site on 4 Nov 2026 |
| `B01_template_godavari_GC-TPL-0001.pdf` | APPROVE (consumes ₹1,00,000 of PO-1048) | Clean template to modify on request. Verified APPROVE with a live extraction (local, 4 Nov 2026) |
| `B02_template_prestige_PWR-TPL-0001.pdf` | APPROVE (consumes ₹18,000 of PO-1049) | Clean template to modify on request. **Not yet run live** (quota ran out); run it once before the interview |

Once processed, a live PDF is cached on that server until the next restart. Re-uploading the same
file opens the existing run.

## 3. Modifying a template on request

The templates are entries in `invoice-templates/extra/manifest.json`. Each has an `edit_ideas` list.
Edit the printed values, keeping the arithmetic consistent (amount = quantity × unit price,
subtotal, tax, total). Then regenerate with one command from the repo root:

```
python invoice-templates/generate.py --manifest invoice-templates/extra/manifest.json
```

The PDFs are rewritten in `docs/interviewer-kit/pdfs/`. Give an edited invoice a new
`invoice_number` too, otherwise it is checked as a duplicate of the original. Some examples:

| Edit (B-01) | Expected result (worked out from the rules; not run live) |
|---|---|
| `unit_price` 25000.00 → 26000.00 (amount 104000.00, IGST 18720.00, total 122720.00) | HOLD `UNIT_PRICE_VARIANCE` (+4% vs +1%) |
| `quantity` 4 → 9 (amount 225000.00, IGST 40500.00, total 265500.00) | HOLD `QUANTITY_EXCEEDS_PO`, `PO_BALANCE_EXCEEDED` |
| `bank.ifsc` → `HDFC0001111` | HOLD `BANK_DETAILS_MISMATCH` |

The sample set itself is regenerated with `python invoice-templates/generate.py` and checked with
`python invoice-templates/generate.py --check`, which confirms byte-identical output. Leave it
unchanged: the cache snapshot is keyed by those files' hashes.

## 4. Policy demonstration: change a threshold, watch the decision change

Verified end to end on the deployed site on 4 Nov 2026.

1. **Baseline.** Run T-08. Result: HOLD `UNIT_PRICE_VARIANCE`. The PO match evidence shows ₹1,250 vs ₹1,000, +25.00% vs +1% allowed. The decision header shows `Policy 8e6324a0`.
2. **Edit `backend/app/policy.yaml`, assumption A-10:** change `value: 1` to `value: 30`, and the `meaning` line to `+30% over PO price; no limit under`.
3. **Deploy.**
   - Deployed: commit and push to `main`. Render redeploys in about a minute. The Policy page then shows the new version, `87543f23…` for exactly this edit.
   - Local: restart the backend.
   - Either way the database starts from seed again (DEC-016), so no reset is needed.
4. **Run T-08 again.** Result: **APPROVE**, committed. Evidence: +25.00% vs +30% allowed, PASSED. The decision header shows `Policy 87543f23`, and PO-1045 drops from ₹1,00,000 to ₹0 remaining. Every decision records the policy version it was made under.
5. **Restore.** Revert the commit (`git revert HEAD`) and push. The version returns to `8e6324a0…`, and T-08 is HOLD again (verified).

What to say: "The model only reads the invoice. Every threshold is a visible, versioned prototype
assumption; change one and the same extraction gives a different decision, and the record shows
which policy decided it."
