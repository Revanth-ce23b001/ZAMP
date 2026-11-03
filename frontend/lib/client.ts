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

export type RunDetail = {
  run_id: string;
  run_number: number;
  run_display: string;
  file_name: string;
  status: string;
  error_message: string | null;
  stage_results: { stage: string; status: string; summary: string; evidence_ids: number[] }[];
  extraction: { provider: string; model: string; cached: boolean } | null;
  decision: {
    status: string;
    reason_codes: string[];
    summary: string[];
    next_actions: string[];
    notes: string[];
    evidence: Evidence[];
    committed: boolean;
  } | null;
};
