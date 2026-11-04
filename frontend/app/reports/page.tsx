"use client";

// Reports (FR-111, FR-115, PRD §10.1; wireframe screen 10). Every figure comes from GET /api/dashboard.

import Link from "next/link";
import { useEffect, useState } from "react";

import PoTable from "@/components/PoTable";
import { CodeChip } from "@/components/ui";
import { getDashboard, type ApiError, type Dashboard, type FlaggedValue } from "@/lib/client";
import { seconds } from "@/lib/format";

const SEVERITY_BAR: Record<string, string> = { REJECT: "bg-bad-dot", HOLD: "bg-hold-dot" };

const TILE_DOT: Record<string, string> = { "m-approved": "s-ok", "m-hold": "s-hold", "m-rejected": "s-bad" };

// Value flagged for review (DEC-019, proposed by implementation, pending review). Two figures that are
// never added together; every amount comes from the backend in integer paise with a display string.
const BUCKET_LABEL: Record<string, string> = {
  discrepancy: "Discrepancy value flagged", held: "Invoice value held for review",
};
const BUCKET_SHORT: Record<string, string> = { discrepancy: "Measured discrepancy", held: "Invoice value at risk" };

function FlaggedTile({ v }: { v: FlaggedValue }) {
  const empty = v.decided_runs === 0;
  return (
    <div className="card mt-4" data-testid="flagged-value">
      <div className="grid gap-6 md:grid-cols-2">
        {(["discrepancy", "held"] as const).map((k) => (
          <div key={k} data-testid={`flagged-${k}`}>
            <div className="micro">{BUCKET_LABEL[k]}</div>
            <div className="mt-2 text-[27px] font-semibold leading-none tracking-tight tabular-nums" data-testid={`flagged-${k}-total`}>
              {empty ? <span className="text-[15px] font-medium text-ink-3">No decided runs yet</span> : v[k].total.display}
            </div>
            {!empty && (
              <div className="mt-1 text-[13px] text-ink-3 tabular-nums">
                {v[k].invoices} of {v.decided_runs} decided invoice{v.decided_runs === 1 ? "" : "s"} ·
                {" "}{v[k].codes.map((c) => c.toLowerCase().replace(/_/g, " ").replace(/po/g, "PO")).join(", ")}
              </div>
            )}
          </div>
        ))}
      </div>
      <p className="mt-4 text-[13px] text-ink-3" data-testid="flagged-footnote">
        These figures are the value of discrepancies surfaced for a human to resolve, not money saved. The first is the
        error the system computed; the second is the full value of invoices stopped before payment. Largest single
        discrepancy per invoice per figure, on a synthetic demo dataset. An invoice can count in both figures, so they
        are shown separately and never added together. {v.override_note}
      </p>
      {v.breakdown.length > 0 && (
        <details className="mt-3" data-testid="flagged-breakdown">
          <summary className="cursor-pointer text-sm font-medium text-ink">Breakdown by invoice ({v.breakdown.length} rows)</summary>
          <div className="card-flush mt-3 overflow-x-auto">
            <table className="tbl">
              <thead><tr><th>Run</th><th>Invoice no.</th><th>Figure</th><th>Driving code</th><th className="num">Amount</th></tr></thead>
              <tbody>
                {v.breakdown.map((b) => (
                  <tr key={b.run_id + b.bucket} data-testid="flagged-row" data-code={b.driving_code} data-bucket={b.bucket}>
                    <td className="font-mono text-ink-2"><Link href={`/runs/${b.run_id}`} className="underline">{b.run_display}</Link></td>
                    <td>{b.invoice_number ?? "—"}</td>
                    <td>{BUCKET_SHORT[b.bucket]}</td>
                    <td><CodeChip code={b.driving_code} /></td>
                    <td className="num font-medium">{b.amount.display}</td>
                  </tr>))}
              </tbody>
            </table>
          </div>
        </details>
      )}
    </div>
  );
}

export default function ReportsPage() {
  const [d, setD] = useState<Dashboard | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    getDashboard().then(setD).catch((e: ApiError) => setError(e.message ?? "Could not load reports"));
  }, []);

  if (error) return <div role="alert" className="notice notice-bad">{error}</div>;
  if (!d) return <div className="h-40 animate-pulse rounded-card bg-hover" data-testid="reports-loading" />;

  const max = Math.max(1, ...d.exceptions_by_reason.map((e) => e.count));
  const tiles: [string, string, string][] = [
    ["Invoices processed", String(d.invoices_processed), "m-processed"],
    ["Approved", String(d.by_status.APPROVE ?? 0), "m-approved"],
    ["On hold", String(d.by_status.HOLD ?? 0), "m-hold"],
    ["Rejected", String(d.by_status.REJECT ?? 0), "m-rejected"],
    ["Auto-approval rate", d.auto_approval_rate.display, "m-auto-rate"],
    ["Value on hold", d.value_on_hold.display, "m-value-hold"],
    ["Avg processing time", seconds(d.avg_processing_ms), "m-avg-time"],
  ];

  return (
    <section>
      <div className="flex flex-wrap items-baseline gap-3">
        <h1 className="page-title">Reports</h1>
        <span data-testid="demo-label" className="rounded-full border border-line px-3 py-1 text-xs font-medium text-ink-2">{d.label}</span>
      </div>

      <div className="mt-5 grid grid-cols-2 gap-4 md:grid-cols-4 lg:grid-cols-7">
        {tiles.map(([label, value, id]) => (
          <div key={id} className="card !p-5">
            <div className="micro flex min-h-[2.6em] items-start gap-2">{TILE_DOT[id] && <span className={"dot mt-[4px] " + TILE_DOT[id]} aria-hidden />}{label}</div>
            <div data-testid={id} className="mt-2 text-[22px] font-semibold leading-tight tracking-tight tabular-nums">{value}</div>
          </div>
        ))}
      </div>
      <p className="mt-3 text-[13px] text-ink-3">
        Auto-approval rate = approved by rules without a reviewer ({d.auto_approval_rate.numerator}) ÷ decided invoices
        ({d.auto_approval_rate.denominator}). Errors and in-progress runs are not counted as processed
        {(d.by_status.ERROR ?? 0) > 0 && <> ({d.by_status.ERROR} in error)</>}.
      </p>

      {d.value_flagged_for_review && <FlaggedTile v={d.value_flagged_for_review} />}

      <h2 className="section-title mt-8">Exceptions by reason</h2>
      <div className="card mt-3" data-testid="exceptions">
        {d.exceptions_by_reason.length === 0 && <p className="text-ink-3">No exceptions yet.</p>}
        <ul className="space-y-1">
          {d.exceptions_by_reason.map((e) => (
            <li key={e.code}>
              <Link href={`/work-queue?reason_code=${e.code}`} data-testid="exception-row" data-code={e.code}
                    className="flex min-h-11 items-center gap-4 rounded-ctl px-2 no-underline hover:bg-hover">
                <span className="w-64 shrink-0"><span className="code">{e.code}</span></span>
                <span className="h-2 flex-1 rounded-full bg-hover">
                  <span className={"block h-2 rounded-full " + (SEVERITY_BAR[e.severity] ?? "bg-nc-dot")}
                        style={{ width: `${(e.count / max) * 100}%` }} />
                </span>
                <span className="w-8 text-right font-semibold tabular-nums text-ink" data-testid="exception-count">{e.count}</span>
              </Link>
            </li>
          ))}
        </ul>
      </div>

      <h2 className="section-title mt-8">PO balances</h2>
      <p className="mt-1 text-[13px] text-ink-3">
        Remaining = PO total − approved (pre-tax). The allowed overrun is shown separately and is not added to remaining.
        Click a PO to see its invoices.
      </p>
      <div className="mt-3"><PoTable pos={d.po_balances} /></div>
    </section>
  );
}
