// PO balances (FR-111). Remaining is PO total minus committed approvals; the allowed overrun is its own
// column and is never added to remaining (FR-068). Values come from the backend as paise + display.

import Link from "next/link";

import type { PoBalance } from "@/lib/client";

export default function PoTable({ pos, ceiling = false }: { pos: PoBalance[]; ceiling?: boolean }) {
  return (
    <div className="overflow-x-auto rounded border border-gray-200 bg-white">
      <table className="w-full text-sm" data-testid="po-table">
        <thead className="bg-gray-50 text-left text-xs uppercase text-gray-500">
          <tr>
            <th className="px-3 py-2">PO</th><th className="px-3 py-2">Vendor</th>
            <th className="px-3 py-2 text-right">PO total</th><th className="px-3 py-2 text-right">Approved</th>
            <th className="px-3 py-2 text-right">Remaining</th><th className="px-3 py-2 text-right">Allowed overrun</th>
            {ceiling && <th className="px-3 py-2 text-right">Ceiling</th>}
            <th className="px-3 py-2">% used</th>
          </tr>
        </thead>
        <tbody>
          {pos.map((p) => (
            <tr key={p.po_number} data-testid="po-row" data-po={p.po_number} className="border-t border-gray-100">
              <td className="whitespace-nowrap px-3 py-2">
                <Link href={`/work-queue?po_number=${encodeURIComponent(p.po_number)}`}
                      className="font-mono text-blue-700 underline" title="Invoices matched to this PO">{p.po_number}</Link>
              </td>
              <td className="px-3 py-2">{p.vendor_name}</td>
              <td data-col="po_total" className="whitespace-nowrap px-3 py-2 text-right">{p.po_total.display}</td>
              <td data-col="approved" className="whitespace-nowrap px-3 py-2 text-right">{p.approved.display}</td>
              <td data-col="remaining" className="whitespace-nowrap px-3 py-2 text-right font-medium">{p.remaining.display}</td>
              <td data-col="allowed_overrun" className="whitespace-nowrap px-3 py-2 text-right text-gray-600">{p.allowed_overrun.display}</td>
              {ceiling && <td data-col="ceiling" className="whitespace-nowrap px-3 py-2 text-right text-gray-600">{p.ceiling.display}</td>}
              <td className="px-3 py-2">
                <div className="flex items-center gap-2">
                  <div className="h-2 w-20 rounded bg-gray-200">
                    <div className={"h-2 rounded " + (p.pct_used > 100 ? "bg-red-500" : "bg-blue-500")}
                         style={{ width: `${Math.min(p.pct_used, 100)}%` }} />
                  </div>
                  <span data-col="pct_used" className="text-xs text-gray-600">{p.pct_used.toFixed(1)}%</span>
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
