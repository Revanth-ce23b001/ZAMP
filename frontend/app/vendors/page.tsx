"use client";

// Vendor master (wireframe screen 8). GET /api/vendors carries no trusted-contact fields (FR-044, DEC-008).

import { useEffect, useState } from "react";

import { listVendors, type ApiError, type Vendor } from "@/lib/client";

const STATUS_STYLE: Record<string, string> = { active: "s-ok", blocked: "s-bad" };

export default function VendorsPage() {
  const [vendors, setVendors] = useState<Vendor[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    listVendors().then(setVendors).catch((e: ApiError) => setError(e.message ?? "Could not load vendors"));
  }, []);

  return (
    <section>
      <h1 className="page-title">Vendors</h1>
      <p data-testid="no-contact-caption" className="mt-2 text-ink-2">
        Synthetic vendor master. No trusted-contact details here: they appear only on a run with a bank-details mismatch.
      </p>
      {error && <div role="alert" className="notice notice-bad mt-4">{error}</div>}
      <div className="card-flush mt-4 overflow-x-auto">
        <table className="tbl" data-testid="vendor-table">
          <thead>
            <tr><th>Vendor code</th><th>Legal name</th>
              <th>GSTIN</th><th>State</th><th>Status</th></tr>
          </thead>
          <tbody>
            {vendors === null && !error && [0, 1, 2].map((i) => (
              <tr key={i}>
                {Array.from({ length: 5 }, (_, j) => <td key={j}><span className="block h-4 animate-pulse rounded-chip bg-hover" /></td>)}
              </tr>))}
            {vendors?.map((v) => (
              <tr key={v.vendor_id} data-testid="vendor-row">
                <td className="font-mono text-ink-2">{v.vendor_id}</td>
                <td className="font-medium">{v.legal_name}</td>
                <td className="font-mono text-ink-2">{v.gstin}</td>
                <td>{v.state_name} ({v.state_code})</td>
                <td>
                  <span className={"status " + (STATUS_STYLE[v.status] ?? "s-nc")}><span className="dot" aria-hidden />{v.status}</span>
                </td>
              </tr>))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
