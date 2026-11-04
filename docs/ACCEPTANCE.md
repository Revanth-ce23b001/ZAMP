# Acceptance pass: PRD §20 and §15 P0 (Day 6, 4 Nov 2026)

Status per item: **Met**, **Partly met**, **Not met** or **N/A**. Test names are in
`backend/tests/` unless stated otherwise.

Suite at the time of this pass (updated Day 7): 438 backend tests passed, 17 skipped (live-provider and browser
opt-ins); 13/13 browser end-to-end; 16/16 fixture tests; token-leak check passes.

## PRD §20 implementation readiness checklist

### Decisions and policy
| Item | Status | Evidence |
|---|---|---|
| OD-1 to OD-8 confirmed | Met | PRD §18 and `DECISIONS.md`. Implemented and tested: OD-1 `test_t04_scores_100_and_70`, `test_t17_two_plausible_within_10_points`; OD-2 A-13 pre-tax basis; OD-3 `test_duplicate_tiers`; OD-4 `test_od4_name_matches_but_gstin_differs`; OD-5 `test_t23_then_t22_bank_override`; OD-6 `test_final_decisions_cannot_be_overridden`; OD-7 `test_p1_rules_off_by_default`; OD-8 no field-edit path exists |
| `policy.yaml` contains every [PA] value in §14.1 | Met | `test_policy_loads_with_every_assumption`, `test_key_thresholds_match_prd`; the Policy page renders all 35 assumptions plus the other PRD values (`test_policy_page_renders_every_key_and_code`) |
| Reason-code table matches §7.2 | Met | `test_exactly_17_codes_matching_prd_set`, `test_every_field_matches_prd` |

### Data
| Item | Status | Evidence |
|---|---|---|
| `fixtures-v1` with provenance; 16 validation tests pass | Met | `python -m pytest seed/test_fixtures.py`: 16 passed |
| Seed vendors V001–V007 (full account, IFSC, trusted contact, status) | Met | `test_seed_values_loaded_as_is`, `test_seed_counts_and_foreign_keys` |
| Seed POs (11), item codes, aliases, quantities, pre-tax prices; totals derived | Met | `test_seed_counts_and_foreign_keys`, `test_po_total_equals_sum_of_lines` |
| Seeded ledger and consumption for the five invoices | Met | `test_seed_counts_and_foreign_keys` (5 ledger, 5 consumption), `test_opening_consumption_equals_ledger_lines` |
| Money in paise everywhere | Met | `test_money_columns_are_integer`; APIs return `{paise, display}` |

### Test inputs
| Item | Status | Evidence |
|---|---|---|
| PDFs for every §13 test that needs a file | **Not met** | 16 PDFs: T-01–T-09, T-12, T-15, T-16, T-17 (added Day 7), T-18, T-25a, T-25b. T-10 and T-22–T-24 reuse them; T-26–T-28 are API tests that build their own inputs. **No PDF** for T-11, T-13, T-19, T-20, T-29 (P0) and T-14, T-21 (P1). The P0 ones are covered at rule level: T-11 `test_e2_with_discount_and_e4_with_charges_and_round_off`, T-13 `test_low_confidence_total_holds_even_when_arithmetic_reconciles`, T-19 `test_missing_invoice_number_is_hold_and_duplicate_not_checked`, T-20 `test_explicit_po_of_another_vendor_is_po_not_found_with_suggestion`, T-29 `test_duplicate_tiers` (HOLD original → `POSSIBLE_DUPLICATE`) |
| At least 4 layouts and 1 photographed scan | Met | Layouts classic, banded, compact, bill; T-05 is an image-only scan (`test_only_t05_is_classified_as_scan`) |
| §13 expected results in automated scenario tests | Partly met | `test_scenarios.py` asserts exact status and codes for all 16 sample files; the scenarios without PDFs are rule- or API-level tests (above) |

### Pipeline
| Item | Status | Evidence |
|---|---|---|
| Prompt returns `null` for absent fields; never computes unprinted values | Met | `test_prompt_states_null_and_no_compute_rules`, `test_prompt_contains_no_fixture_or_expected_data`; L-01 live extraction returned `po_reference: null` |
| Temperature 0, schema validation, one retry, cache by hash | Met | `test_valid_response_first_try` (temperature 0), `test_malformed_then_valid_retries_once`, `test_two_failures_raise_extraction_error`, `test_cache_hit_after_reset_makes_no_provider_call` |
| Corroboration rules (§9.8) per critical field | Met | `rules/validate.py` (§9.8): total via E4, lines via E1 and line amounts, vendor via master, PO via existence, invoice number non-empty, date plausibility. Tests: `test_low_confidence_total_holds…`, `test_printed_line_amount_must_equal_qty_times_price`, `test_date_plausibility` |
| Stage continuation and Not Checked reasons | Met | `test_unknown_vendor_holds_and_downstream_not_checked`, `test_blocked_vendor_rejects_and_other_stages_still_run`; deployed L-01 shows "Not checked: vendor not identified" |

### Integrity and safety
| Item | Status | Evidence |
|---|---|---|
| `commit_approval` is the only writer of `po_consumption` (besides the seed loader); idempotent; rechecks; `BEGIN IMMEDIATE` | Met | Only `services/commit.py` inserts consumption for runs. `test_approve_commits_once_even_if_called_twice`, `test_recheck_at_commit_blocks_and_writes_nothing`, `test_concurrent_overrides_commit_once`, `test_unique_ledger_po_line_backs_idempotency` |
| Single worker; no parallel path | Met | `test_runs_are_processed_one_at_a_time_in_order`; container runs `--workers 1` |
| Account numbers masked in API, SSE, logs, UI; IFSC in full | Met | `test_no_full_account_number_in_any_response`, `test_vendors_masked_and_ifsc_full`, browser checks scan page HTML. Day 6 scan of all 15 samples: no full account number in the SSE streams, the stored audit and decision records, or 6.8 MB of debug logs |
| `GET /api/vendors` excludes trusted-contact phone and email | Met | `test_vendors_exclude_trusted_contact`, `test_vendors_page_and_response_have_no_contact_details` |
| `demo_resets` append-only; reset atomic; failures logged; rate limits; typed confirmation | Met within one server lifetime | `test_demo_resets_update_and_delete_fail`, `test_forced_failure_rolls_back_everything`, `test_log_write_failure_returns_500`, `test_second_success_within_30s_is_429`, `test_bad_confirmation_400`. On the free host the table is cleared by any restart (DEC-016, verified) |
| T-02 PDF dated 29 Oct 2026 | Met | Manifest `invoice_date` 2026-10-29, checked by `verify.py` (A-35) |
| LLM key only in backend env; spend limit set | Met (free tier) | Key only in Render env (`test_api_key_never_in_responses_logs_or_audit`, token-leak check). No billing on the Render project (per the project owner), so no spend is possible; the free-tier rate limits are the cap (DEC-017) |
| `DEMO_RESET_TOKEN` required, added only by the Next.js route, never in the browser bundle | Met | `test_load_settings_requires_token`, `test_uvicorn_exits_without_token`, `npm run test:token-leak` |
| Forbidden labels absent | Met | `test_built_frontend_has_no_forbidden_labels` |

### UI
| Item | Status | Evidence |
|---|---|---|
| Live view: seven stages + commit over SSE with replay | Met | `test_live.py` (replay, reconnect with `Last-Event-ID`, keepalive), `test_t01_runs_end_to_end_live_in_the_browser` |
| Decision tabs and evidence (expected, actual, variance, allowed) for every code | Met | All six tabs checked in the browser for T-01–T-05. Every one of the 17 codes is produced by a rule test, and (since Day 6) each must carry an evidence row with all four fields (`tests/test_rules.py::run`) |
| Review panel enforces reviewer name, reason, bank fields; shows blocked-override message | Met | `test_override_validation`, `test_reason_of_exactly_ten_characters_is_accepted`; browser T-03 flow; the panel shows the API's block message when `commit_result` is `blocked` (`ReviewPanel.tsx`); T-25 blocked on the deployed link |
| Dashboard metrics, PO balances, reset button, policy page | Met | `test_reports_match_the_database_after_reset`, `test_dashboard_po_balance_four_separate_values`, `test_reset_shows_empty_queue_and_seed_balances` |

### Demo readiness
| Item | Status | Evidence |
|---|---|---|
| T-01–T-05 pass 5× consecutively after reset | Met | Day 6 Step 1: five deployed walks with a reset before each; T-01–T-05 passed in all five, with identical decisions, codes, evidence and balances |
| Demo script rehearsed under 5 minutes | **Not met** | Needs a human rehearsal; script in `docs/DEMO_SCRIPT.md` |
| Deployed link tested from a fresh browser | Met | Day 5B and Day 6 walks run in a new Chromium profile each time |
| Limitations statement prepared (§14.2) | Met | `docs/DEMO_SCRIPT.md` §5 and the Policy page |

## PRD §15 P0 scope

Every P0 item is built:

- **Ingest:** upload and validation; sample picker; SHA-256 idempotency and existing-run display; sequential worker.
- **Extraction:** text-first and vision extraction; schema with all §8.4 fields; confidence with the 0.85 threshold and corroboration; caching; provider configuration; temperature 0.
- **Validation:** required fields; date plausibility; E1–E4 with discount, other charges and round-off; tax components; the mismatch vs low-confidence distinction.
- **Vendor and bank:** vendor identification and status; GSTIN format and master match with honest labels; full bank comparison, masking, security banner, missing-bank Not Checked, trusted contact and bank override fields.
- **Duplicates:** three tiers, revision suffix and the similar-invoice note.
- **PO matching:** explicit and inferred POs, scoring and multiple candidates; item-code and alias pairing; unmatched lines; unit-price, cumulative-quantity and once-per-PO value tolerances; approved-only consumption.
- **Decision:** the decision engine, codes, severity, stage statuses, evidence and summaries; idempotent `commit_approval` with recheck.
- **Review and reporting:** review workflow, override audit, confirm PO and override blocking; dashboard metrics; audit trail; inbox; SSE live view; reset; policy page.

**P0 gaps:** none in functionality. The gap is test inputs: five P0 scenarios have no sample PDF (above). T-17 was added on Day 7 (`test_t17_two_plausible_po_candidates_within_10_points`).

## Known issues found during the Day 6 pass

1. **Audit tab override order: fixed.** Override records were ordered by `created_at` (second precision), then by a random ID, so two overrides in the same second could appear in the wrong order. It was seen once in five deployed walks (T-03's "not verified" attempt listed after the approval). They are now ordered by `created_at` then insertion order; regression test `test_overrides_in_the_same_second_keep_insertion_order` (failed before the fix, passes after).
2. **Occasional slow page on the deployed link.** Twice in about 80 browser steps a page took over 20 s; it was not reproducible (API median 0.3 s, worst 1.2 s over 100 calls).
3. **Live extraction latency.** gemini-3.6-flash took 41 s per invoice on Render (twice), above the 20 s NFR-10 target; flash-lite took 10 s.

## Day 8 additions (measurement and value surface)

Additive only: no rule, threshold, policy value, reason code, severity, schema or decision outcome changed. Both
entries below are proposed by implementation and pending review (DEC-018, DEC-019).

| Item | Status | Evidence |
|---|---|---|
| Ground truth from the PDFs, never from model output; unresolved fields shown, not guessed | Met | `backend/seed/extraction_ground_truth.json` (built by `app.tools.build_ground_truth`). `test_ground_truth_is_reproducible_and_keyed_by_sample_hash`, `test_every_unresolved_field_says_why` |
| Per-field accuracy: exact match, exact paise, vendor name via the pipeline's normaliser, unresolved excluded | Met | `test_money_matches_only_in_exact_paise`, `test_tax_total_sums_printed_components_and_null_is_a_miss`, `test_vendor_name_uses_the_pipelines_normaliser`, `test_other_fields_need_an_exact_match`, `test_unresolved_fields_are_excluded_from_numerator_and_denominator` |
| Measurement reproducible, zero model calls, committed file current | Met | `test_compare_is_deterministic`, `test_measurement_is_idempotent_and_the_committed_file_is_current`, `test_committed_measurement_numbers_are_consistent` |
| Decision accuracy against PRD §13 | Met | `test_ground_truth_expected_decisions_match_the_scenario_tests`; 16 of 16 in `extraction_accuracy.json` |
| "What absorbed the misses" names real code paths | Met | `test_every_absorbing_mechanism_names_a_real_function`; per-miss evidence taken from the pipeline's own evidence rows |
| `GET /api/accuracy` serves the committed file as-is, also after a reset | Met | `test_accuracy_endpoint_serves_the_committed_file`, `test_accuracy_endpoint_works_after_a_demo_reset` |
| `/accuracy` page renders both tables and the footer | Met | Browser test `test_accuracy_page_renders_both_tables_and_footer` |
| README headline matches the measurement | Met | `test_readme_headline_matches_the_committed_measurement` |
| Value flagged for review: one formula per code | Met | `test_po_balance_exceeded_is_cumulative_taxable_minus_ceiling`, `test_quantity_exceeds_po_is_excess_times_po_unit_price`, `test_unit_price_variance_counts_positive_terms_only`, `test_arithmetic_mismatch_is_printed_minus_computed_total`, `test_full_total_codes_contribute_the_invoice_total`, `test_every_other_code_contributes_zero`, `test_negative_overrun_is_zero_not_a_credit` |
| Max, not sum, per invoice | Met | `test_max_not_sum_when_one_invoice_fires_balance_and_quantity`, `test_ties_break_deterministically_by_severity` |
| An overridden invoice still contributes; empty state with no decided runs | Met | `test_pipeline_breakdown_and_overridden_invoice_still_counts`, `test_zero_decided_runs_is_an_empty_state`, `test_values_are_integer_paise_with_a_display_string` |

Suite after Day 8: 465 backend tests passed (18 opt-in skips), 16 fixture tests, 14 browser end-to-end tests.

### Day 8B (presentation only)

The measured values are unchanged (`backend/seed/extraction_accuracy.json` is byte-identical). `/accuracy` now leads with the field that has the most misses, read from the measurement (`test_accuracy_lead_statement_is_derived_from_the_measurement`). The value tile is split into "Discrepancy value flagged" and "Invoice value held for review", with no combined total (`test_codes_belong_to_exactly_one_bucket`, `test_one_invoice_can_contribute_to_both_buckets`, `test_an_invoice_can_contribute_to_only_one_bucket`, `test_max_not_sum_applies_within_each_bucket`, `test_endpoint_returns_no_combined_total`). Copy rules are enforced by `tests/test_wording.py`. README: `test_readme_leads_with_the_per_field_miss`.
