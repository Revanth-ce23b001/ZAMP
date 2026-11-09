"use client";

// Live processing screen (PRD §10.4, FR-083, FR-114; wireframe screen 3).
// Left: the seven stages and a Commit row, driven by the backend's SSE stream (which replays every
// stored event on connect and resumes from Last-Event-ID on reconnect). Right: the PDF.
// This screen only displays what the backend decided; it computes nothing.

import { useCallback, useEffect, useRef, useState } from "react";

import Link from "next/link";
import { eventsUrl, fileUrl, getRun, retryRun, type Evidence, type RunDetail } from "@/lib/client";
import CheckTable from "@/components/CheckTable";
import { ShiningText } from "@/components/ui/shining-text";
import { APP_NAME } from "@/lib/brand";
import { checkRows } from "@/lib/checks";
import { MIN_STAGE_DISPLAY_MS } from "@/lib/display";

const STAGES = [
  ["INGEST", "Ingest"], ["EXTRACT", "Extract"], ["VALIDATE", "Validate"], ["VENDOR_BANK", "Vendor & Bank"],
  ["DUPLICATE", "Duplicate"], ["PO_MATCH", "PO Match"], ["DECIDE", "Decide"], ["COMMIT", "Commit"],
] as const;
type StageKey = (typeof STAGES)[number][0];
type Status = "PENDING" | "RUNNING" | "PASSED" | "FAILED" | "NEEDS_REVIEW" | "NOT_CHECKED" | "ERROR";
type Row = { status: Status; summary?: string; startedAt?: string; endedAt?: string };

// Status pills (globals.css): text on background with a solid dot. Pending is an outline.
const CHIP: Record<Status, string> = {
  PENDING: "border border-line bg-surface text-ink-3",
  RUNNING: "s-run",
  PASSED: "s-ok",
  FAILED: "s-bad",
  NEEDS_REVIEW: "s-hold",
  NOT_CHECKED: "s-nc",
  ERROR: "s-bad",
};
const LABEL: Record<Status, string> = {
  PENDING: "Pending", RUNNING: "Running", PASSED: "Passed", FAILED: "Failed",
  NEEDS_REVIEW: "Needs Review", NOT_CHECKED: "Not Checked", ERROR: "Error",
};
// 22px stage nodes on the rail: outlined while pending, ringed while running, solid once settled.
const NODE: Record<Status, string> = {
  PENDING: "border-2 border-ink-4 bg-surface",
  RUNNING: "bg-run-dot ring-4 ring-run-bg",
  PASSED: "bg-ok-dot", FAILED: "bg-bad-dot", NEEDS_REVIEW: "bg-hold-dot", NOT_CHECKED: "bg-nc-dot", ERROR: "bg-bad-dot",
};
const GLYPH: Partial<Record<Status, string>> = { PASSED: "✓", FAILED: "✕", NEEDS_REVIEW: "!", NOT_CHECKED: "–", ERROR: "!" };
const DECISION_BADGE: Record<string, string> = { APPROVE: "s-ok", HOLD: "s-hold", REJECT: "s-bad" };

const emptyRows = (): Record<StageKey, Row> =>
  Object.fromEntries(STAGES.map(([k]) => [k, { status: "PENDING" as Status }])) as Record<StageKey, Row>;

function duration(a?: string, b?: string): string {
  if (!a || !b) return "";
  const ms = new Date(b).getTime() - new Date(a).getTime();
  return ms < 1000 ? "<1 s" : `${(ms / 1000).toFixed(0)} s`;
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
      // The stored stage results are the truth (a PO confirmation re-runs stages 6 and 7).
      setRows((prev) => {
        const next = { ...prev };
        for (const s of d.stage_results) {
          const k = s.stage as StageKey;
          if (next[k]) next[k] = { ...next[k], status: s.status as Status, summary: s.summary };
        }
        return next;
      });
    }).catch(() => undefined);
  }, [runId]);


  useEffect(() => {
    ended.current = false;
    let closed = false;
    const es = new EventSource(eventsUrl(runId));
    const patch = (k: StageKey, r: Partial<Row>) => setRows((prev) => ({ ...prev, [k]: { ...prev[k], ...r } }));

    // Display pacing (frontend only): events are applied strictly in arrival order, and a row that
    // went to Running is held there for at least MIN_STAGE_DISPLAY_MS before its final status shows.
    // Replayed history animates the same way. The recorded timestamps shown come from the backend.
    const runningSince: Partial<Record<StageKey, number>> = {};
    const queue: [string, any][] = [];
    let pumping = false;
    const running = (k: StageKey, r: Partial<Row>) => { runningSince[k] = performance.now(); patch(k, { ...r, status: "RUNNING" }); };
    const settle = async (k: StageKey) => {
      const since = runningSince[k];
      delete runningSince[k];
      const wait = since === undefined ? 0 : MIN_STAGE_DISPLAY_MS - (performance.now() - since);
      if (wait > 0) await new Promise((res) => setTimeout(res, wait));
    };

    const apply = async (name: string, d: any) => {
      switch (name) {
        case "run": setHeader(d); if (d.status !== "QUEUED") setQueued(null); break;
        case "queued": setQueued(d.position); break;
        case "retry":
          setRows(emptyRows()); setDecision(null); setCommitMsg(null); setError(null); setDetail(null); break;
        case "stage_started": setQueued(null); running(d.stage, { startedAt: d.at }); break;
        case "stage_completed":
          await settle(d.stage);
          patch(d.stage, { status: d.status as Status, summary: d.summary, endedAt: d.at });
          break;
        case "decision":
          setDecision({ status: d.status, reason_codes: d.reason_codes });
          if (d.status === "APPROVE") running("COMMIT", { startedAt: d.at });
          else patch("COMMIT", { status: "NOT_CHECKED", summary: "Only approved invoices consume PO balance" });
          break;
        case "commit":
          await settle("COMMIT");
          setCommitMsg(d.message);
          patch("COMMIT", { status: d.outcome === "committed" ? "PASSED" : "FAILED", summary: d.message, endedAt: d.at });
          if (d.outcome === "blocked") setDecision((prev) => prev && { status: "HOLD", reason_codes: [...prev.reason_codes, ...d.codes] });
          break;
        case "error":
          if (d.stage) await settle(d.stage);
          setError(d.message);
          if (d.stage) patch(d.stage, { status: "ERROR", summary: d.message });
          break;
        case "end": ended.current = true; setReconnecting(false); refresh(); break;
      }
    };
    const pump = async () => {
      if (pumping) return;
      pumping = true;
      while (queue.length && !closed) {
        const [name, d] = queue.shift()!;
        await apply(name, d);
      }
      pumping = false;
    };

    es.onopen = () => setReconnecting(false);
    es.onerror = () => { if (!ended.current) setReconnecting(true); };
    for (const name of ["run", "queued", "retry", "stage_started", "stage_completed", "decision", "commit", "error", "end"]) {
      es.addEventListener(name, (e) => {
        const d = JSON.parse((e as MessageEvent).data);
        if (name === "end") es.close();          // stop the stream now; the queue finishes the display
        queue.push([name, d]);
        pump();
      });
    }
    return () => { closed = true; ended.current = true; es.close(); };
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
  // The model is reading while Ingest or Extract is the running stage. Derived from the same stage
  // state the rows use, so the shimmer leaves the moment Extract completes or fails and never
  // animates over an error. It adds no minimum display of its own: a cached extraction keeps only
  // the stage row's existing MIN_STAGE_DISPLAY_MS hold.
  const reading = !error && (rows.INGEST.status === "RUNNING" || rows.EXTRACT.status === "RUNNING");

  return (
    <section>
      {existing && (
        <div className="notice notice-run mb-5">
          This exact file was already processed in {runLabel}. It was not processed again.
        </div>
      )}
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="page-title">{runLabel} – Processing</h1>
        <span className="font-mono text-[13px] text-ink-3">{header.file_name}</span>
        {reconnecting && <span className="status s-nc"><span className="dot" aria-hidden />Reconnecting…</span>}
      </div>

      {reading && (
        <p className="mt-2 flex min-w-0 text-[14px]">
          <ShiningText text={`${APP_NAME} is reading the invoice…`} />
        </p>
      )}

      {queued !== null && (
        <div className="card mt-5 !py-4">
          Queued: {queued === 0 ? "next to process" : `${queued} run${queued === 1 ? "" : "s"} ahead`}
        </div>
      )}
      {decision && (
        <div data-testid="decision-banner" className="card mt-5 flex flex-wrap items-center gap-3 !py-4">
          <span className={"status status-lg " + (DECISION_BADGE[decision.status] ?? "s-nc")}>
            <span className="dot" aria-hidden />{decision.status}
          </span>
          {decision.status === "APPROVE" && decision.reason_codes.length > 0 && (
            <span className="text-ink-2">approved by reviewer override of</span>
          )}
          {decision.reason_codes.length === 0
            ? <span className="text-ink-2">All checks passed</span>
            : decision.reason_codes.map((c) => (
                <span key={c} className="code">{c}</span>))}
          {commitMsg && <span className="text-ink-2">· {commitMsg}</span>}
          {ended.current && (
            <Link href={`/runs/${runId}`} className="btn btn-primary ml-auto no-underline"
                  data-testid="view-decision">View decision</Link>
          )}
        </div>
      )}
      {error && (
        <div role="alert" className="notice notice-bad mt-5 flex items-center gap-3">
          <span className="font-semibold">ERROR</span><span className="flex-1">{error}</span>
          <button type="button" onClick={onRetry} className="btn btn-primary">Retry</button>
        </div>
      )}

      <div className="mt-6 flex flex-col gap-5 lg:flex-row">
        <ol className="lg:w-3/5" data-testid="stepper">
          {STAGES.map(([key, name], i) => {
            const r = rows[key];
            const ev = evidenceFor(key);
            const expanded = open === key;
            return (
              <li key={key} data-stage={key} data-status={r.status} className="relative flex gap-4 pb-3">
                {/* connected rail: runs from this node's centre to the next */}
                {i < STAGES.length - 1 && <span aria-hidden className="absolute bottom-0 left-[10.5px] top-[33px] w-px bg-line" />}
                <span aria-hidden
                      className={"relative z-10 mt-[11px] flex h-[22px] w-[22px] shrink-0 items-center justify-center rounded-full text-[11px] font-semibold leading-none text-white " + NODE[r.status]}>
                  {GLYPH[r.status] ?? ""}
                </span>
                <div className={"min-w-0 flex-1 rounded-card border border-line " + (r.status === "RUNNING" ? "bg-sunken" : "bg-surface")}>
                  <button type="button" onClick={() => setOpen(expanded ? null : key)}
                          className="flex min-h-11 w-full items-center gap-3 px-4 py-2 text-left">
                    <span className={"w-28 font-medium " + (r.status === "PENDING" ? "text-ink-4" : "text-ink")}>{name}</span>
                    <span className={"status " + CHIP[r.status]}>
                      {r.status !== "PENDING" && <span className={"dot" + (r.status === "RUNNING" ? " animate-pulse" : "")} aria-hidden />}
                      {LABEL[r.status]}
                    </span>
                    <span className="w-12 text-xs text-ink-3 tabular-nums">{duration(r.startedAt, r.endedAt)}</span>
                    <span className="min-w-0 flex-1 truncate text-[13px] text-ink-3" title={r.summary}>{r.summary}</span>
                  </button>
                  {expanded && (
                    <div className="border-t border-line px-4 py-3 text-xs">
                      {key === "EXTRACT" && detail?.extraction && (
                        <p className="mb-2">{detail.extraction.provider}/{detail.extraction.model}
                          {detail.extraction.cached && <span className="status s-nc ml-2"><span className="dot" aria-hidden />Cached extraction</span>}</p>
                      )}
                      {ev.length === 0
                        ? <p className="text-ink-3">{r.summary ?? "No checks recorded yet."}</p>
                        : <CheckTable rows={checkRows(ev, detail)} stage={key} />}
                    </div>
                  )}
                </div>
              </li>
            );
          })}
        </ol>
        <div className="lg:w-2/5">
          <iframe title="Invoice PDF" src={fileUrl(runId)} className="h-[640px] w-full rounded-card border border-line bg-surface" />
        </div>
      </div>
    </section>
  );
}
