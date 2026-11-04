"use client";

// Invoice Work Queue (FR-113, FR-002, PRD §10.2; wireframe screen 1).
// KPIs count every run; the chips and the reason/PO filters narrow the table only. Filters live in
// the URL (?status=, ?reason_code=, ?po_number=) so Reports and Purchase Orders can link straight in.
// This screen only displays what the backend decided; it computes no business result.

import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";

import RunLauncher from "@/components/RunLauncher";
import { CodeChip, DecisionBadge } from "@/components/ui";
import { listRuns, retryRun, type ApiError, type RunRow } from "@/lib/client";
import { DECIDED_BY, seconds, STATUS_LABEL, when } from "@/lib/format";

const IN_PROGRESS = ["QUEUED", "PROCESSING"];
const CHIPS: { key: string; label: string; match: (r: RunRow) => boolean }[] = [
  { key: "", label: "All", match: () => true },
  { key: "APPROVE", label: "Approved", match: (r) => r.status === "APPROVE" },
  { key: "HOLD", label: "On hold", match: (r) => r.status === "HOLD" },
  { key: "REJECT", label: "Rejected", match: (r) => r.status === "REJECT" },
  { key: "ERROR", label: "Error", match: (r) => r.status === "ERROR" },
  { key: "PROCESSING", label: "In progress", match: (r) => IN_PROGRESS.includes(r.run_status) },
];
const SHORT_DECIDED_BY: Record<string, string> = { reviewer: "Reviewer override", rules_after_review: "After PO confirmation" };
const KPI_DOT: Record<string, string> = { "kpi-approved": "s-ok", "kpi-hold": "s-hold", "kpi-rejected": "s-bad" };
const MAX_CODES = 2;          // reason-code chips shown before "+n"
const POLL_MS = 3000;         // refresh while any run is queued or processing

function StatusBadge({ row }: { row: RunRow }) {
  if (IN_PROGRESS.includes(row.run_status)) {
    return <span className="status s-run"><span className="dot" aria-hidden />{STATUS_LABEL[row.run_status]}</span>;
  }
  return (
    <span className="flex flex-col items-start gap-0.5">
      <DecisionBadge status={row.status} />
      {row.decided_by && row.decided_by !== "rules" && (
        <span className="whitespace-nowrap text-[11px] text-ink-3" data-testid="decided-by">
          {SHORT_DECIDED_BY[row.decided_by] ?? DECIDED_BY[row.decided_by] ?? row.decided_by}</span>)}
    </span>
  );
}

export default function WorkQueue() {
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  const status = (params.get("status") ?? "").toUpperCase();
  const reason = (params.get("reason_code") ?? "").toUpperCase();
  const po = params.get("po_number") ?? "";

  const [runs, setRuns] = useState<RunRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    listRuns().then((r) => { setRuns(r); setError(null); })
      .catch((e: ApiError) => setError(e.message ?? "Could not load runs. Is the backend running?"));
  }, []);
  useEffect(load, [load]);
  const busy = runs?.some((r) => IN_PROGRESS.includes(r.run_status)) ?? false;
  useEffect(() => {
    if (!busy) return;
    const t = setInterval(load, POLL_MS);
    return () => clearInterval(t);
  }, [busy, load]);

  const setFilter = (key: string, value: string) => {
    const next = new URLSearchParams(params.toString());
    if (value) next.set(key, value); else next.delete(key);
    const qs = next.toString();
    router.replace(qs ? `${pathname}?${qs}` : pathname, { scroll: false });
  };

  const chip = CHIPS.find((c) => c.key === status) ?? CHIPS[0];
  const shown = useMemo(() => (runs ?? []).filter((r) =>
    chip.match(r) && (!reason || r.reason_codes.includes(reason)) && (!po || r.matched_po === po)), [runs, chip, reason, po]);
  const codes = useMemo(() => [...new Set((runs ?? []).flatMap((r) => r.reason_codes))].sort(), [runs]);

  const kpis = [
    ["Total runs", runs?.length, "kpi-total"],
    ["Approved", runs?.filter((r) => r.status === "APPROVE").length, "kpi-approved"],
    ["On hold", runs?.filter((r) => r.status === "HOLD").length, "kpi-hold"],
    ["Rejected", runs?.filter((r) => r.status === "REJECT").length, "kpi-rejected"],
  ] as const;

  const open = (r: RunRow) => router.push(IN_PROGRESS.includes(r.run_status) ? `/runs/${r.run_id}/live` : `/runs/${r.run_id}`);
  async function retry(e: React.MouseEvent, r: RunRow) {
    e.stopPropagation();
    try {
      await retryRun(r.run_id);
      router.push(`/runs/${r.run_id}/live`);
    } catch (err) {
      setError((err as ApiError).message ?? "Retry failed");
    }
  }

  return (
    <section>
      <h1 className="page-title">Invoice Work Queue</h1>

      <div className="mt-5 grid grid-cols-2 gap-4 md:grid-cols-4">
        {kpis.map(([label, n, id]) => (
          <div key={id} className="card !p-5">
            <div className="micro flex items-center gap-2">
              {KPI_DOT[id] && <span className={"dot " + KPI_DOT[id]} aria-hidden />}{label}
            </div>
            <div data-testid={id} className="mt-2 text-[27px] font-semibold leading-none tracking-tight tabular-nums">
              {n === undefined ? <span className="inline-block h-7 w-10 animate-pulse rounded-chip bg-hover" /> : n}
            </div>
          </div>
        ))}
      </div>

      <RunLauncher />

      <div className="mt-6 flex flex-wrap items-center gap-2" data-testid="filters">
        {CHIPS.map((c) => (
          <button key={c.label} type="button" onClick={() => setFilter("status", c.key)}
                  aria-pressed={c === chip} data-testid={`chip-${c.key || "ALL"}`}
                  className="pill">
            {c.label}
          </button>
        ))}
        <select aria-label="Reason code" value={reason} onChange={(e) => setFilter("reason_code", e.target.value)}
                data-testid="reason-filter"
                className="input ml-2">
          <option value="">Any reason code</option>
          {[...new Set([...codes, ...(reason ? [reason] : [])])].map((c) => <option key={c} value={c}>{c}</option>)}
        </select>
        {po && (
          <button type="button" onClick={() => setFilter("po_number", "")} data-testid="po-filter"
                  className="pill !border-accent !text-accent">
            PO {po} ✕
          </button>
        )}
      </div>

      {error && <div role="alert" className="notice notice-bad mt-4">{error}</div>}

      <div className="card-flush mt-4 overflow-x-auto">
        <table className="tbl tbl-hover" data-testid="runs-table">
          <thead>
            <tr>
              <th>Run #</th><th>File</th><th>Vendor</th>
              <th>Invoice no.</th><th className="num">Total</th>
              <th>Status</th><th>Reason codes</th>
              <th>Processed</th><th className="num">Duration</th><th />
            </tr>
          </thead>
          <tbody>
            {runs === null && !error && [0, 1, 2, 3].map((i) => (
              <tr key={i} data-testid="skeleton-row">
                {Array.from({ length: 10 }, (_, j) => (
                  <td key={j}><span className="block h-4 animate-pulse rounded-chip bg-hover" /></td>))}
              </tr>
            ))}
            {shown.map((r) => (
              <tr key={r.run_id} data-testid="run-row" data-run-id={r.run_id} data-status={r.status}
                  onClick={() => open(r)} className="cursor-pointer">
                <td className="whitespace-nowrap font-mono text-ink-2">{r.run_display}</td>
                <td className="max-w-[11rem] truncate text-ink-2" title={r.file_name}>{r.file_name}</td>
                <td className="font-medium">{r.vendor_name ?? "—"}</td>
                <td className="whitespace-nowrap">{r.invoice_number ?? "—"}</td>
                <td className="num whitespace-nowrap font-medium">{r.total?.display ?? "—"}</td>
                <td><StatusBadge row={r} /></td>
                <td>
                  <div className="flex flex-wrap gap-1" data-testid="row-codes">
                    {r.reason_codes.slice(0, MAX_CODES).map((c) => <CodeChip key={c} code={c} />)}
                    {r.reason_codes.length > MAX_CODES && (
                      <span className="code !text-ink-3"
                            title={r.reason_codes.slice(MAX_CODES).join(", ")}>+{r.reason_codes.length - MAX_CODES}</span>)}
                  </div>
                </td>
                <td className="min-w-[7.5rem] text-ink-3">{when(r.completed_at)}</td>
                <td className="num whitespace-nowrap text-ink-3">{seconds(r.duration_ms)}</td>
                <td className="text-right">
                  {r.status === "ERROR" && (
                    <button type="button" onClick={(e) => retry(e, r)} data-testid="row-retry"
                            className="btn btn-secondary !text-bad">Retry</button>)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {runs !== null && shown.length === 0 && (
          <div data-testid="empty-state" className="px-6 py-12 text-center text-sm text-ink-3">
            {runs.length === 0
              ? <>No invoices processed yet. Pick a sample above and press <b>Run</b>, or drop a PDF.</>
              : <>No runs match these filters. <Link href={pathname} className="font-medium underline">Clear filters</Link></>}
          </div>
        )}
      </div>
    </section>
  );
}
