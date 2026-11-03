"use client";

// Live processing screen (PRD §10.4, FR-083, FR-114; wireframe screen 3).
// Left: the seven stages and a Commit row, driven by the backend's SSE stream (which replays every
// stored event on connect and resumes from Last-Event-ID on reconnect). Right: the PDF.
// This screen only displays what the backend decided; it computes nothing.

import { useCallback, useEffect, useRef, useState } from "react";

import { eventsUrl, fileUrl, getRun, retryRun, type Evidence, type RunDetail } from "@/lib/client";

const STAGES = [
  ["INGEST", "Ingest"], ["EXTRACT", "Extract"], ["VALIDATE", "Validate"], ["VENDOR_BANK", "Vendor & Bank"],
  ["DUPLICATE", "Duplicate"], ["PO_MATCH", "PO Match"], ["DECIDE", "Decide"], ["COMMIT", "Commit"],
] as const;
type StageKey = (typeof STAGES)[number][0];
type Status = "PENDING" | "RUNNING" | "PASSED" | "FAILED" | "NEEDS_REVIEW" | "NOT_CHECKED" | "ERROR";
type Row = { status: Status; summary?: string; startedAt?: string; endedAt?: string };

const CHIP: Record<Status, string> = {
  PENDING: "border border-gray-300 text-gray-500 bg-white",
  RUNNING: "bg-blue-100 text-blue-800 animate-pulse",
  PASSED: "bg-green-100 text-green-800",
  FAILED: "bg-red-100 text-red-800",
  NEEDS_REVIEW: "bg-amber-100 text-amber-800",
  NOT_CHECKED: "bg-gray-200 text-gray-700",
  ERROR: "bg-red-100 text-red-800",
};
const LABEL: Record<Status, string> = {
  PENDING: "Pending", RUNNING: "Running", PASSED: "Passed", FAILED: "Failed",
  NEEDS_REVIEW: "Needs Review", NOT_CHECKED: "Not Checked", ERROR: "Error",
};
const DOT: Record<Status, string> = {
  PENDING: "border-2 border-gray-300 bg-white", RUNNING: "bg-blue-500 animate-pulse", PASSED: "bg-green-500",
  FAILED: "bg-red-500", NEEDS_REVIEW: "bg-amber-500", NOT_CHECKED: "bg-gray-400", ERROR: "bg-red-500",
};
const DECISION_BADGE: Record<string, string> = {
  APPROVE: "bg-green-600 text-white", HOLD: "bg-amber-500 text-white", REJECT: "bg-red-600 text-white",
};

const emptyRows = (): Record<StageKey, Row> =>
  Object.fromEntries(STAGES.map(([k]) => [k, { status: "PENDING" as Status }])) as Record<StageKey, Row>;

function duration(a?: string, b?: string): string {
  if (!a || !b) return "";
  const ms = new Date(b).getTime() - new Date(a).getTime();
  return ms < 1000 ? "<1 s" : `${(ms / 1000).toFixed(0)} s`;
}

function show(v: unknown): string {
  if (v === null || v === undefined || v === "") return "—";
  return typeof v === "object" ? JSON.stringify(v) : String(v);
}

export default function LiveRun({ runId, existing }: { runId: string; existing: boolean }) {
  const [rows, setRows] = useState(emptyRows);
  const [header, setHeader] = useState<{ run_number?: number; file_name?: string }>({});
  const [queued, setQueued] = useState<number | null>(null);
  const [decision, setDecision] = useState<{ status: string; reason_codes: string[] } | null>(null);
  const [commitMsg, setCommitMsg] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [reconnecting, setReconnecting] = useState(false);
  const [detail, setDetail] = useState<RunDetail | null>(null);
  const [open, setOpen] = useState<StageKey | null>(null);
  const [connection, setConnection] = useState(0);        // bump to reconnect after Retry
  const ended = useRef(false);

  const refresh = useCallback(() => {
    getRun(runId).then((d) => {
      setDetail(d);
      if (d.decision) setDecision({ status: d.decision.status, reason_codes: d.decision.reason_codes });
    }).catch(() => undefined);
  }, [runId]);

  useEffect(() => {
    ended.current = false;
    const es = new EventSource(eventsUrl(runId));
    const on = (name: string, fn: (d: any) => void) =>
      es.addEventListener(name, (e) => fn(JSON.parse((e as MessageEvent).data)));
    const patch = (k: StageKey, r: Partial<Row>) => setRows((prev) => ({ ...prev, [k]: { ...prev[k], ...r } }));

    es.onopen = () => setReconnecting(false);
    es.onerror = () => { if (!ended.current) setReconnecting(true); };
    on("run", (d) => { setHeader(d); if (d.status !== "QUEUED") setQueued(null); });
    on("queued", (d) => setQueued(d.position));
    on("retry", () => {
      setRows(emptyRows()); setDecision(null); setCommitMsg(null); setError(null); setDetail(null);
    });
    on("stage_started", (d) => { setQueued(null); patch(d.stage, { status: "RUNNING", startedAt: d.at }); });
    on("stage_completed", (d) => patch(d.stage, {
      status: d.status as Status, summary: d.not_checked_reason ? `${d.summary}` : d.summary, endedAt: d.at,
    }));
    on("decision", (d) => {
      setDecision({ status: d.status, reason_codes: d.reason_codes });
      patch("COMMIT", d.status === "APPROVE"
        ? { status: "RUNNING", startedAt: d.at }
        : { status: "NOT_CHECKED", summary: "Only approved invoices consume PO balance" });
    });
    on("commit", (d) => {
      setCommitMsg(d.message);
      patch("COMMIT", { status: d.outcome === "committed" ? "PASSED" : "FAILED", summary: d.message, endedAt: d.at });
      if (d.outcome === "blocked") setDecision((prev) => prev && { status: "HOLD", reason_codes: [...prev.reason_codes, ...d.codes] });
    });
    on("error", (d) => {
      setError(d.message);
      if (d.stage) patch(d.stage, { status: "ERROR", summary: d.message });
    });
    on("end", () => { ended.current = true; setReconnecting(false); es.close(); refresh(); });
    return () => { ended.current = true; es.close(); };
  }, [runId, connection, refresh]);

  async function onRetry() {
    try {
      await retryRun(runId);
      setError(null);
      setConnection((n) => n + 1);
    } catch (e) {
      setError((e as { message?: string }).message ?? "Retry failed");
    }
  }

  const evidenceFor = (k: StageKey): Evidence[] => (detail?.decision?.evidence ?? []).filter((e) => e.stage === k);
  const runLabel = header.run_number ? `R-${String(header.run_number).padStart(4, "0")}` : "Run";

  return (
    <section>
      {existing && (
        <div className="mb-4 rounded border border-blue-200 bg-blue-50 p-3 text-sm text-blue-900">
          This exact file was already processed in {runLabel}. It was not processed again.
        </div>
      )}
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="text-2xl font-semibold">{runLabel} – Processing</h1>
        <span className="text-sm text-gray-500">{header.file_name}</span>
        {reconnecting && <span className="rounded bg-gray-100 px-2 py-0.5 text-xs text-gray-700">Reconnecting…</span>}
      </div>

      {queued !== null && (
        <div className="mt-4 rounded border border-gray-200 bg-white p-3 text-sm">
          Queued: {queued === 0 ? "next to process" : `${queued} run${queued === 1 ? "" : "s"} ahead`}
        </div>
      )}
      {decision && (
        <div data-testid="decision-banner" className="mt-4 flex flex-wrap items-center gap-3 rounded border border-gray-200 bg-white p-3">
          <span className={"rounded px-3 py-1 text-sm font-bold " + (DECISION_BADGE[decision.status] ?? "bg-gray-500 text-white")}>
            {decision.status}
          </span>
          {decision.reason_codes.length === 0
            ? <span className="text-sm text-gray-600">All checks passed</span>
            : decision.reason_codes.map((c) => (
                <span key={c} className="rounded bg-gray-100 px-2 py-0.5 font-mono text-xs text-gray-800">{c}</span>))}
          {commitMsg && <span className="text-sm text-gray-600">· {commitMsg}</span>}
        </div>
      )}
      {error && (
        <div role="alert" className="mt-4 flex items-center gap-3 rounded border border-red-200 bg-red-50 p-3 text-sm text-red-800">
          <span className="font-semibold">ERROR</span><span className="flex-1">{error}</span>
          <button type="button" onClick={onRetry} className="rounded bg-red-600 px-3 py-1 text-white">Retry</button>
        </div>
      )}

      <div className="mt-6 flex flex-col gap-6 lg:flex-row">
        <ol className="lg:w-3/5" data-testid="stepper">
          {STAGES.map(([key, name], i) => {
            const r = rows[key];
            const ev = evidenceFor(key);
            const expanded = open === key;
            return (
              <li key={key} data-stage={key} data-status={r.status} className="relative flex gap-3 pb-4">
                {i < STAGES.length - 1 && <span className="absolute left-[9px] top-6 h-full w-px bg-gray-200" />}
                <span className={"mt-1 h-5 w-5 shrink-0 rounded-full " + DOT[r.status]} />
                <div className="min-w-0 flex-1 rounded border border-gray-200 bg-white">
                  <button type="button" onClick={() => setOpen(expanded ? null : key)}
                          className="flex w-full items-center gap-3 px-3 py-2 text-left">
                    <span className="w-28 font-medium">{name}</span>
                    <span className={"rounded px-2 py-0.5 text-xs " + CHIP[r.status]}>{LABEL[r.status]}</span>
                    <span className="w-12 text-xs text-gray-400">{duration(r.startedAt, r.endedAt)}</span>
                    <span className="min-w-0 flex-1 truncate text-sm text-gray-600" title={r.summary}>{r.summary}</span>
                  </button>
                  {expanded && (
                    <div className="border-t border-gray-100 px-3 py-2 text-xs">
                      {key === "EXTRACT" && detail?.extraction && (
                        <p className="mb-2">{detail.extraction.provider}/{detail.extraction.model}
                          {detail.extraction.cached && <span className="ml-2 rounded bg-gray-100 px-1.5 py-0.5">Cached extraction</span>}</p>
                      )}
                      {ev.length === 0 ? <p className="text-gray-500">{r.summary ?? "No evidence yet."}</p> : (
                        <table className="w-full">
                          <thead className="text-left text-gray-500">
                            <tr><th>Check</th><th>Expected</th><th>Actual</th><th>Variance</th><th>Allowed</th><th>Result</th><th>Code</th></tr>
                          </thead>
                          <tbody>
                            {ev.map((e, j) => (
                              <tr key={j} className="border-t border-gray-50 align-top">
                                <td className="pr-2">{e.check}</td><td className="pr-2">{show(e.expected)}</td>
                                <td className="pr-2">{show(e.actual)}</td><td className="pr-2">{show(e.variance)}</td>
                                <td className="pr-2">{show(e.allowed)}</td><td className="pr-2">{e.result}</td>
                                <td className="font-mono">{e.reason_code ?? ""}</td>
                              </tr>))}
                          </tbody>
                        </table>
                      )}
                    </div>
                  )}
                </div>
              </li>
            );
          })}
        </ol>
        <div className="lg:w-2/5">
          <iframe title="Invoice PDF" src={fileUrl(runId)} className="h-[640px] w-full rounded border border-gray-200 bg-white" />
        </div>
      </div>
    </section>
  );
}
