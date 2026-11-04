// PO balances (FR-111). Remaining is PO total minus committed approvals; the allowed overrun is its own
// column and is never added to remaining (FR-068). Values come from the backend as paise + display.

import Link from "next/link";

import type { PoBalance } from "@/lib/client";

export default function PoTable({ pos, ceiling = false }: { pos: PoBalance[]; ceiling?: boolean }) {
  return (
    <div className="card-flush overflow-x-auto">
      <table className="tbl" data-testid="po-table">
        <thead>
          <tr>
            <th>PO</th><th>Vendor</th>
            <th className="num">PO total</th><th className="num">Approved</th>
            <th className="num">Remaining</th><th className="num">Allowed overrun</th>
            {ceiling && <th className="num">Ceiling</th>}
            <th>% used</th>
          </tr>
        </thead>
        <tbody>
          {pos.map((p) => (
            <tr key={p.po_number} data-testid="po-row" data-po={p.po_number}>
              <td className="whitespace-nowrap">
                <Link href={`/work-queue?po_number=${encodeURIComponent(p.po_number)}`}
                      className="font-mono font-medium underline" title="Invoices matched to this PO">{p.po_number}</Link>
              </td>
              <td>{p.vendor_name}</td>
              <td data-col="po_total" className="num whitespace-nowrap">{p.po_total.display}</td>
              <td data-col="approved" className="num whitespace-nowrap">{p.approved.display}</td>
              <td data-col="remaining" className="num whitespace-nowrap font-semibold">{p.remaining.display}</td>
              <td data-col="allowed_overrun" className="num whitespace-nowrap text-ink-3">{p.allowed_overrun.display}</td>
              {ceiling && <td data-col="ceiling" className="num whitespace-nowrap text-ink-3">{p.ceiling.display}</td>}
              <td>
                <div className="flex items-center gap-2">
                  <div className="h-1.5 w-20 rounded-full bg-hover">
                    <div className={"h-1.5 rounded-full " + (p.pct_used > 100 ? "bg-bad-dot" : "bg-ink-2")}
                         style={{ width: `${Math.min(p.pct_used, 100)}%` }} />
                  </div>
                  <span data-col="pct_used" className="text-xs text-ink-3 tabular-nums">{p.pct_used.toFixed(1)}%</span>
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
