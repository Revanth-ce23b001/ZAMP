# InvoPilot seed fixtures (fixtures-v1)

Synthetic seed data for PRD v1.2.1 (frozen 2 Nov 2026). Goes in `backend/seed/`. Money is integer paise.

| File | Contents | Records |
|---|---|---|
| `vendors.json` | V001–V007: identity, GSTIN, state, full synthetic bank account + IFSC, trusted contact, status | 7 |
| `purchase_orders.json` | 11 POs with lines (item code, description, normalised aliases, uom, qty, pre-tax unit price); `po_total` derived | 11 |
| `ledger.json` | Seeded approved historical invoices: INV-SL-0451, INV-SL-0467, INV-0912, INV-2041, INV-GC-0921 | 5 |
| `consumption.json` | Opening PO consumption, derived 1:1 from ledger lines (never set independently) | 5 |
| `test_fixtures.py` | 16 validation tests (FKs, totals, reconciliation, demo preconditions) | |

## Provenance rule

Every record carries `_synthetic_fields` (invented for this prototype) and, where relevant, `_derived_fields` (computed from PRD values). **Any field not listed in either is specified in the PRD.** No value represents a real vendor, bank account or Zamp policy. Loaders should ignore keys starting with `_`.

| Value | Source |
|---|---|
| Vendor IDs, names, states, statuses; V001 and V003 GSTINs; all V003 bank/contact details | PRD §8.2, §8.9 |
| V001 bank account and IFSC | Derived from PRD §8.4 extraction example (T-01 requires a match) |
| GSTINs for V002, V004–V007; all other bank details and contacts | Synthetic (GSTIN format per FR-031) |
| PO numbers, vendor links, item codes, quantities, unit prices | PRD §8.9 |
| PO-1042 date, aliases, uom | PRD §8.3 |
| Other PO dates, aliases, uom; descriptions for LAP-14, MON-24, DOCK-USB | Synthetic |
| INV-0912, INV-2041, INV-GC-0921 dates and totals | PRD §9.10, §8.5 |
| INV-SL-0451 and INV-SL-0467 dates | Synthetic |
| Seeded GST at 18% (intra-state CGST 9% + SGST 9%, inter-state IGST 18%) | Fixture assumption; matches PRD totals |

## Constraints the test invoice PDFs must respect

These come from the fixture dates and the frozen duplicate rules. Breaking them makes a test fire an extra reason code.

| Test | Constraint |
|---|---|
| T-02 | Invoice date **after 21 Oct 2026** (more than 7 days after INV-SL-0467, which has the same total and lines). Recommended: 29 Oct 2026. Expect the non-blocking "similar invoice exists" note. |
| T-04, T-17 | Invoice date on or after 12 Oct 2026 (PO-1036 date) and within 180 days of the PO dates. Recommended: 30 Oct 2026. |
| T-06 | Invoice date more than 7 days after 1 Oct 2026. PRD value: 31 Oct 2026. |
| T-16 | Invoice date within 7 days of 3 Oct 2026. PRD value: 6 Oct 2026. |
| T-03 | Invoice bank account must end in 4471 and carry IFSC `ICIC0005678`. |
| T-01 | Invoice bank details must equal V001 master (`50200012341234`, `HDFC0001234`). |

## Normalisation note

Description normalisation follows FR-064 with one implementation clarification: a decimal point between digits is kept (`2.5 sqmm` → `2.5sqmm`), because removing it would turn `2.5` into `2 5`. See `DECISIONS.md` DEC-003.
