// Browser-side calls to the FastAPI backend.
//
// The browser talks to the backend directly (CORS allows the frontend origin), including the SSE
// stream: proxying a stream through a serverless Next.js route buffers it and breaks the live
// view on deploy. NEXT_PUBLIC_BACKEND_ORIGIN is a public URL, not a secret. The demo reset
// token is NOT used here; resets still go through the server-only route /api/demo/reset.

export const BACKEND = (process.env.NEXT_PUBLIC_BACKEND_ORIGIN || "http://localhost:8000").replace(/\/+$/, "");

export type Sample = { name: string; size_bytes: number };
export type Submitted = { run_id: string; existing: boolean; run_number: number; run_display: string };
export type ApiError = { error: string; message: string };

async function asJson<T>(res: Response): Promise<T> {
  const body = await res.json().catch(() => ({ error: "bad_response", message: `HTTP ${res.status}` }));
  if (!res.ok) throw body as ApiError;
  return body as T;
}

export async function listSamples(): Promise<Sample[]> {
  return asJson(await fetch(`${BACKEND}/api/samples`, { cache: "no-store" }));
}

export async function processSample(name: string): Promise<Submitted> {
  return asJson(await fetch(`${BACKEND}/api/samples/${encodeURIComponent(name)}/process`, { method: "POST" }));
}

export async function uploadInvoice(file: File): Promise<Submitted> {
  const form = new FormData();
  form.append("file", file);
  return asJson(await fetch(`${BACKEND}/api/invoices`, { method: "POST", body: form }));
}

export async function getRun(runId: string): Promise<RunDetail> {
  return asJson(await fetch(`${BACKEND}/api/runs/${runId}`, { cache: "no-store" }));
}

export async function retryRun(runId: string): Promise<{ run_id: string; status: string }> {
  return asJson(await fetch(`${BACKEND}/api/runs/${runId}/retry`, { method: "POST" }));
}

export type ReviewResult = {
  run_id: string;
  override_id: string;
  action: string;
  status: string;
  commit_result: "committed" | "blocked" | "not_applicable";
  message: string;
  reason_codes: string[];
};

async function postJson<T>(url: string, body: unknown): Promise<T> {
  return asJson(await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" },
                                   body: JSON.stringify(body) }));
}

export function overrideRun(runId: string, body: {
  action: "approve" | "reject"; reviewer_name: string; reason: string; codes_overridden: string[];
  bank_verification?: { verified_with: string; channel: string; outcome: string };
}): Promise<ReviewResult> {
  return postJson(`${BACKEND}/api/runs/${runId}/override`, body);
}

export function confirmPo(runId: string, body: { po_number: string; reviewer_name: string; reason: string }) {
  return postJson<ReviewResult>(`${BACKEND}/api/runs/${runId}/confirm-po`, body);
}

export const eventsUrl = (runId: string) => `${BACKEND}/api/runs/${runId}/events`;
export const fileUrl = (runId: string) => `${BACKEND}/api/runs/${runId}/file`;

export type Evidence = {
  check: string;
  stage: string;
  source_fields: string[];
  expected: unknown;
  actual: unknown;
  variance: unknown;
  allowed: unknown;
  result: string;
  reason_code: string | null;
};

export type Money = { paise: number; display: string };
export type Field<T = unknown> = { value: T | null; confidence: number };

export type PoLine = {
  invoice_line: number;
  description: string | null;
  item_code: string | null;
  po_line_id: string | null;
  po_item_code?: string;
  po_description?: string;
  pairing: "code" | "description" | "alias" | null;
  quantity: { po: number; previously_approved: number; this_invoice: number; excess: number } | null;
  unit_price: { po: Money; invoice: Money; variance_pct: number; allowed_pct: number } | null;
  result: string;
};

export type PoDetail = {
  po_number: string;
  method: "explicit" | "inferred" | "confirmed";
  po_total: Money;
  previously_approved: Money;
  this_invoice: Money | null;
  remaining: Money;
  allowed_overrun: Money;
  ceiling: Money;
  invoices_previously_approved: number;
  lines: PoLine[];
};

export type DuplicateMatch = {
  tier: string;
  ledger_id: string;
  source: string;
  run_id: string | null;
  run_display: string | null;
  invoice_number_raw: string;
  invoice_number_normalised: string;
  invoice_date: string;
  total: Money;
  status: string;
  po_number: string | null;
  lines: { key: string; qty: number }[];
};

export type VendorSide = {
  legal_name: string | null;
  gstin: string | null;
  state_code: string | null;
  state_name?: string;
  status?: string;
  vendor_id?: string;
  bank_account_masked: string | null;
  ifsc: string | null;
};

export type RunDetail = {
  run_id: string;
  run_number: number;
  run_display: string;
  file_name: string;
  status: string;
  error_message: string | null;
  completed_at: string | null;
  stage_results: { stage: string; status: string; summary: string; evidence_ids: number[] }[];
  extraction: {
    provider: string; model: string; cached: boolean; ingest_method: string;
    fields: Record<string, any>;
  } | null;
  invoice: {
    invoice_number: string | null; invoice_number_normalised: string | null; invoice_date: string | null;
    vendor_name: string | null; vendor_gstin: string | null; total: Money | null; taxable_value: Money | null;
    line_signature: { key: string; qty: number }[] | null;
  } | null;
  vendor: {
    identified: boolean;
    identification_note: string | null;
    invoice: VendorSide;
    master: VendorSide | null;
    labels: { gstin_format: string | null; gstin_master: string | null; bank: string | null; bank_result: string | null };
  } | null;
  decision: {
    status: string;
    reason_codes: string[];
    summary: string[];
    next_actions: string[];
    notes: string[];
    evidence: Evidence[];
    committed: boolean;
    committed_at: string | null;
    decided_by: string;
    policy_version: string;
    matched_po: string | null;
    po_candidates: { po_number: string; score: number; plausible: boolean; breakdown: Record<string, unknown> }[];
    duplicate_matches: DuplicateMatch[];
    po_detail: PoDetail | null;
  } | null;
  trusted_contact?: { name: string; phone: string; email: string | null };
};

export type AuditLog = {
  run_id: string;
  events: { event_id: number; type: string; payload: Record<string, any>; created_at: string }[];
  overrides: {
    override_id: string; action: string; original_status: string; new_status: string; codes_overridden: string[];
    reviewer_name: string; reason: string; bank_verification: { verified_with: string; channel: string; outcome: string } | null;
    confirmed_po: string | null; commit_result: string; block_reason: string | null; created_at: string;
  }[];
};

export async function getAudit(runId: string): Promise<AuditLog> {
  return asJson(await fetch(`${BACKEND}/api/runs/${runId}/audit`, { cache: "no-store" }));
}

// ---------------------------------------------------------------- read models (FR-111..FR-117)

export type RunRow = {
  run_id: string; run_number: number; run_display: string; file_name: string;
  run_status: string;            // QUEUED | PROCESSING | DECIDED | ERROR
  status: string;                // the decision when decided, else the run state
  vendor_id: string | null; vendor_name: string | null; invoice_number: string | null;
  total: Money | null; reason_codes: string[]; matched_po: string | null; decided_by: string | null;
  committed: boolean; created_at: string; completed_at: string | null; duration_ms: number | null;
  cached: boolean | null; error_message: string | null;
};

export type PoBalance = {
  po_number: string; vendor_id: string; vendor_name: string; po_date: string; currency: string; line_count: number;
  po_total: Money; approved: Money; remaining: Money; allowed_overrun: Money; ceiling: Money; pct_used: number;
};

export type Dashboard = {
  label: string;
  invoices_processed: number;
  by_status: Record<string, number>;
  auto_approval_rate: { value: number; display: string; numerator: number; denominator: number };
  value_on_hold: Money;
  exceptions_by_reason: { code: string; count: number; severity: string }[];
  avg_processing_ms: number | null;
  recent_runs: RunRow[];
  po_balances: PoBalance[];
};

export type Vendor = {
  vendor_id: string; legal_name: string; gstin: string; state_code: string; state_name: string; status: string;
  bank_account_masked: string; ifsc: string; bank_display?: string;
};

export type PolicyEntry = { id?: string; name: string; value: unknown; unit?: string; label?: string;
                            meaning?: string; source?: string; [k: string]: unknown };
export type Policy = {
  policy_version: string; policy_version_short: string;
  assumptions: PolicyEntry[]; other_assumptions: PolicyEntry[]; p1_rules: Record<string, boolean>;
  reason_codes: { code: string; severity: string; stage: number; stage_key: string; stage_status: string;
                  template: string; next_action: string; priority: number }[];
  does_not_verify: { not_verified: string; instead: string }[];
  scope: Record<string, string>;
};

const getJson = async <T>(path: string): Promise<T> => asJson(await fetch(`${BACKEND}${path}`, { cache: "no-store" }));
export const listRuns = () => getJson<RunRow[]>("/api/runs");
export const getDashboard = () => getJson<Dashboard>("/api/dashboard");
export const listPos = () => getJson<PoBalance[]>("/api/pos");
export const listVendors = () => getJson<Vendor[]>("/api/vendors");
export const getPolicy = () => getJson<Policy>("/api/policy");

// ---------------------------------------------------------------- measured accuracy (DEC-018)

export type AccuracyMiss = { test_id: string; file: string; expected: unknown; returned: unknown };
export type AccuracyField = {
  field: string; correct: number; total: number; unresolved: number;
  unresolved_samples: { test_id: string; reason: string | null }[]; misses: AccuracyMiss[];
};
export type Accuracy = {
  _meta: { dataset: string; sample_size: number; model_calls_made: number; measured_at: string; policy_version: string;
           match_rule: string };
  models: { cached_extractions_by_model: Record<string, number>;
            configured_chain: { source: string; text: string[]; vision: string[] } };
  field_accuracy: AccuracyField[];
  field_totals: { correct: number; total: number; unresolved: number };
  decision_accuracy: {
    rule: string; source: string; correct: number; total: number;
    rows: { test_id: string; expected_status: string; expected_codes: string[]; actual_status: string;
            actual_codes: string[]; correct: boolean }[];
    mismatches: unknown[];
  };
  absorbed: { field: string; misses: number; code_path: string; mechanism: string;
              evidence: { test_id: string; check: string; result: string; allowed: string | null;
                          variance: string | null; reason_code: string | null }[] }[];
  fallback_chain: { supported: boolean; quoted_line?: string; fallback_model?: string; samples_measured?: number;
                    outcomes_reproduced?: number; samples_with_wrong_outcome?: string[]; spurious_code?: string;
                    source: string; note?: string };
};

export const getAccuracy = () => getJson<Accuracy>("/api/accuracy");
