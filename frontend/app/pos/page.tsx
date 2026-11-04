"use client";

// Purchase Orders (wireframe screens 7 and 9). GET /api/pos returns PO headers and balances only, so
// there is no line-level PO detail screen; each PO links to the Work Queue filtered by that PO.

import { useEffect, useState } from "react";

import PoTable from "@/components/PoTable";
import { listPos, type ApiError, type PoBalance } from "@/lib/client";

export default function PosPage() {
  const [pos, setPos] = useState<PoBalance[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    listPos().then(setPos).catch((e: ApiError) => setError(e.message ?? "Could not load purchase orders"));
  }, []);

  return (
    <section>
      <h1 className="text-2xl font-semibold">Purchase Orders</h1>
      <p className="mt-1 text-sm text-gray-600">
        Two-way match. Amounts are pre-tax. Approved counts committed approvals only. Remaining = PO total − approved;
        the allowed overrun (min(1% of PO total, ₹1,000), once per PO) is a separate column and the ceiling is PO total +
        allowed overrun. Click a PO to see the invoices matched to it.
      </p>
      {error && <div role="alert" className="mt-4 rounded border border-red-200 bg-red-50 p-3 text-sm text-red-800">{error}</div>}
      <div className="mt-4">
        {pos ? <PoTable pos={pos} ceiling /> : !error && <div className="h-40 animate-pulse rounded bg-gray-100" />}
      </div>
    </section>
  );
}
