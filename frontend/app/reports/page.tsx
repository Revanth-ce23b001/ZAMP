"use client";

// Reports (FR-111, FR-115, PRD §10.1; wireframe screen 10). Every figure comes from GET /api/dashboard.

import Link from "next/link";
import { useEffect, useState } from "react";

import PoTable from "@/components/PoTable";
import { getDashboard, type ApiError, type Dashboard } from "@/lib/client";
import { seconds } from "@/lib/format";

const SEVERITY_BAR: Record<string, string> = { REJECT: "bg-red-500", HOLD: "bg-amber-500" };

export default function ReportsPage() {
  const [d, setD] = useState<Dashboard | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    getDashboard().then(setD).catch((e: ApiError) => setError(e.message ?? "Could not load reports"));
  }, []);

  if (error) return <div role="alert" className="rounded border border-red-200 bg-red-50 p-4 text-sm text-red-800">{error}</div>;
  if (!d) return <div className="h-40 animate-pulse rounded bg-gray-100" data-testid="reports-loading" />;

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
        <h1 className="text-2xl font-semibold">Reports</h1>
        <span data-testid="demo-label" className="rounded bg-gray-100 px-2 py-0.5 text-xs text-gray-700">{d.label}</span>
      </div>

      <div className="mt-4 grid grid-cols-2 gap-3 md:grid-cols-4 lg:grid-cols-7">
        {tiles.map(([label, value, id]) => (
          <div key={id} className="rounded border border-gray-200 bg-white p-3">
            <div className="text-xs text-gray-500">{label}</div>
            <div data-testid={id} className="mt-1 text-xl font-semibold">{value}</div>
          </div>
        ))}
      </div>
      <p className="mt-2 text-xs text-gray-500">
        Auto-approval rate = approved by rules without a reviewer ({d.auto_approval_rate.numerator}) ÷ decided invoices
        ({d.auto_approval_rate.denominator}). Errors and in-progress runs are not counted as processed
        {(d.by_status.ERROR ?? 0) > 0 && <> ({d.by_status.ERROR} in error)</>}.
      </p>

      <h2 className="mt-8 text-lg font-semibold">Exceptions by reason</h2>
      <div className="mt-2 rounded border border-gray-200 bg-white p-4" data-testid="exceptions">
        {d.exceptions_by_reason.length === 0 && <p className="text-sm text-gray-500">No exceptions yet.</p>}
        <ul className="space-y-2">
          {d.exceptions_by_reason.map((e) => (
            <li key={e.code}>
              <Link href={`/work-queue?reason_code=${e.code}`} data-testid="exception-row" data-code={e.code}
                    className="flex items-center gap-3 rounded px-1 hover:bg-blue-50">
                <span className="w-64 shrink-0 font-mono text-xs text-blue-700 underline">{e.code}</span>
                <span className="h-4 flex-1">
                  <span className={"block h-4 rounded " + (SEVERITY_BAR[e.severity] ?? "bg-gray-400")}
                        style={{ width: `${(e.count / max) * 100}%` }} />
                </span>
                <span className="w-8 text-right text-sm font-medium" data-testid="exception-count">{e.count}</span>
              </Link>
            </li>
          ))}
        </ul>
      </div>

      <h2 className="mt-8 text-lg font-semibold">PO balances</h2>
      <p className="mt-1 text-xs text-gray-500">
        Remaining = PO total − approved (pre-tax). The allowed overrun is shown separately and is not added to remaining.
        Click a PO to see its invoices.
      </p>
      <div className="mt-2"><PoTable pos={d.po_balances} /></div>
    </section>
  );
}
