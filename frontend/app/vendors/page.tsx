"use client";

// Vendor master (wireframe screen 8). GET /api/vendors carries no trusted-contact fields (FR-044, DEC-008).

import { useEffect, useState } from "react";

import { listVendors, type ApiError, type Vendor } from "@/lib/client";

const STATUS_STYLE: Record<string, string> = {
  active: "bg-green-100 text-green-800", blocked: "bg-red-100 text-red-800",
};

export default function VendorsPage() {
  const [vendors, setVendors] = useState<Vendor[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    listVendors().then(setVendors).catch((e: ApiError) => setError(e.message ?? "Could not load vendors"));
  }, []);

  return (
    <section>
      <h1 className="text-2xl font-semibold">Vendors</h1>
      <p data-testid="no-contact-caption" className="mt-1 text-sm text-gray-600">
        Synthetic vendor master. No trusted-contact details here: they appear only on a run with a bank-details mismatch.
      </p>
      {error && <div role="alert" className="mt-4 rounded border border-red-200 bg-red-50 p-3 text-sm text-red-800">{error}</div>}
      <div className="mt-4 overflow-x-auto rounded border border-gray-200 bg-white">
        <table className="w-full text-sm" data-testid="vendor-table">
          <thead className="bg-gray-50 text-left text-xs uppercase text-gray-500">
            <tr><th className="px-3 py-2">Vendor code</th><th className="px-3 py-2">Legal name</th>
              <th className="px-3 py-2">GSTIN</th><th className="px-3 py-2">State</th><th className="px-3 py-2">Status</th></tr>
          </thead>
          <tbody>
            {vendors === null && !error && [0, 1, 2].map((i) => (
              <tr key={i} className="border-t border-gray-100">
                {Array.from({ length: 5 }, (_, j) => <td key={j} className="px-3 py-3"><span className="block h-4 animate-pulse rounded bg-gray-200" /></td>)}
              </tr>))}
            {vendors?.map((v) => (
              <tr key={v.vendor_id} data-testid="vendor-row" className="border-t border-gray-100">
                <td className="px-3 py-2 font-mono">{v.vendor_id}</td>
                <td className="px-3 py-2">{v.legal_name}</td>
                <td className="px-3 py-2 font-mono">{v.gstin}</td>
                <td className="px-3 py-2">{v.state_name} ({v.state_code})</td>
                <td className="px-3 py-2">
                  <span className={"rounded px-2 py-0.5 text-xs " + (STATUS_STYLE[v.status] ?? "bg-gray-100 text-gray-700")}>{v.status}</span>
                </td>
              </tr>))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
