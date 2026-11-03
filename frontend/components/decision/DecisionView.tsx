"use client";

// Decision details screen (FR-110, FR-084, FR-085, PRD §10.5). Explains a decision from the
// evidence the backend recorded; computes nothing itself.

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import ReviewPanel from "@/components/ReviewPanel";
import { AuditTab, DuplicatesTab, EvidenceTab, ExtractedTab, PoMatchTab, VendorBankTab } from "@/components/decision/Tabs";
import { CodeChip, DecisionBadge } from "@/components/ui";
import { fileUrl, getAudit, getRun, retryRun, type ApiError, type AuditLog, type RunDetail } from "@/lib/client";
import { DECIDED_BY } from "@/lib/format";

const TABS = [
  ["extracted", "Extracted data"], ["vendor", "Vendor & bank"], ["duplicates", "Duplicates"],
  ["po", "PO match"], ["evidence", "Evidence"], ["audit", "Audit"],
] as const;
type TabKey = (typeof TABS)[number][0];

export default function DecisionView({ runId, existing }: { runId: string; existing: boolean }) {
  const [d, setD] = useState<RunDetail | null>(null);
  const [audit, setAudit] = useState<AuditLog | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<TabKey>("extracted");

  const load = useCallback(() => {
    getRun(runId).then(setD).catch((e: ApiError) => setError(e.error === "run_not_found" ? "Run not found" : e.message));
    getAudit(runId).then(setAudit).catch(() => undefined);
  }, [runId]);
  useEffect(load, [load]);

  if (error) return <div role="alert" className="rounded border border-red-200 bg-red-50 p-4 text-red-800">{error}</div>;
  if (!d) return <div className="animate-pulse space-y-3"><div className="h-10 w-1/2 rounded bg-gray-200" /><div className="h-40 rounded bg-gray-200" /></div>;

  if (d.status === "ERROR") {
    return (
      <div role="alert" className="rounded border border-red-200 bg-red-50 p-4 text-red-800">
        <p className="font-semibold">{d.run_display}: processing error (no decision)</p>
        <p className="mt-1 text-sm">{d.error_message}</p>
        <button type="button" className="mt-3 rounded bg-red-600 px-3 py-1 text-white"
                onClick={() => retryRun(runId).then(() => { window.location.href = `/runs/${runId}/live`; })}>Retry</button>
      </div>
    );
  }
  const dec = d.decision;
  if (!dec) {
    return <p>{d.run_display} is {d.status.toLowerCase()}. <Link className="text-blue-700 underline" href={`/runs/${runId}/live`}>Watch it live</Link>.</p>;
  }
  const bank = dec.reason_codes.includes("BANK_DETAILS_MISMATCH");

  return (
    <section>
      {existing && (
        <div className="mb-4 rounded border border-blue-200 bg-blue-50 p-3 text-sm text-blue-900">
          This exact file was already processed in {d.run_display}. It was not processed again.
        </div>
      )}

      {/* Header */}
      <div className="flex flex-wrap items-center gap-4 rounded border border-gray-200 bg-white p-4" data-testid="decision-header">
        <DecisionBadge status={dec.status} large />
        <div>
          <div className="text-lg font-semibold">{d.invoice?.invoice_number ?? "No invoice number"} · {d.vendor?.master?.legal_name ?? d.invoice?.vendor_name ?? "Unknown vendor"}</div>
          <div className="text-sm text-gray-600">
            Total {d.invoice?.total?.display ?? "—"} · {d.run_display} · Decided by {DECIDED_BY[dec.decided_by] ?? dec.decided_by}
            {" "}· Policy <span className="font-mono" title={dec.policy_version}>{dec.policy_version.slice(0, 8)}</span>
            {dec.committed && " · Committed"}
          </div>
        </div>
        <Link href={`/runs/${runId}/live`} className="ml-auto text-sm text-blue-700 underline">Processing timeline</Link>
      </div>

      {/* Security banner (FR-042) */}
      {bank && d.trusted_contact && (
        <div role="alert" data-testid="security-banner" className="mt-4 rounded border-2 border-red-400 bg-red-50 p-3 text-sm text-red-900">
          <strong>Security exception.</strong> Bank details differ from vendor master. Verify with {d.trusted_contact.name},
          {" "}{d.trusted_contact.phone} (vendor master). Do not use contact details printed on this invoice.
        </div>
      )}

      {/* Why (FR-085): one sentence and one next action per code, severity order */}
      <div className="mt-4 rounded border border-gray-200 bg-white p-4" data-testid="why-panel">
        <h2 className="font-semibold">Why</h2>
        {dec.reason_codes.length === 0 ? (
          <p className="mt-1 text-sm text-gray-700">All checks passed: clean two-way match within policy.</p>
        ) : (
          <ul className="mt-2 space-y-2">
            {dec.reason_codes.map((c, i) => (
              <li key={c} className="text-sm" data-testid="why-item">
                <CodeChip code={c} /> <span>{dec.summary[i]}</span>
                <div className="ml-1 mt-0.5 text-gray-600">Next action: {dec.next_actions[i]}</div>
              </li>))}
          </ul>
        )}
        {dec.status === "APPROVE" && dec.reason_codes.length > 0 && (
          <p className="mt-2 text-xs text-gray-600">Approved by {DECIDED_BY[dec.decided_by]?.toLowerCase()} of the codes above; see Audit.</p>
        )}
        {dec.notes.length > 0 && (
          <ul className="mt-3 list-disc pl-5 text-xs text-gray-600">{dec.notes.map((n) => <li key={n}>{n}</li>)}</ul>
        )}
      </div>

      <div className="mt-4 flex flex-col gap-6 lg:flex-row">
        <div className="min-w-0 lg:w-3/5">
          <div className="flex flex-wrap gap-1 border-b border-gray-200" role="tablist">
            {TABS.map(([k, label]) => (
              <button key={k} type="button" role="tab" aria-selected={tab === k} onClick={() => setTab(k)}
                      className={"border-b-2 px-3 py-2 text-sm " + (tab === k ? "border-blue-600 font-semibold text-blue-700" : "border-transparent text-gray-600")}>
                {label}
              </button>))}
          </div>
          <div className="mt-4 overflow-x-auto rounded border border-gray-200 bg-white p-4">
            {tab === "extracted" && <ExtractedTab d={d} />}
            {tab === "vendor" && <VendorBankTab d={d} />}
            {tab === "duplicates" && <DuplicatesTab d={d} />}
            {tab === "po" && <PoMatchTab d={d} />}
            {tab === "evidence" && <EvidenceTab evidence={dec.evidence} />}
            {tab === "audit" && <AuditTab audit={audit} />}
          </div>
        </div>
        <div className="lg:w-2/5">
          {dec.status === "HOLD" && <ReviewPanel key={dec.reason_codes.join()} detail={d} onDone={load} />}
          <iframe title="Invoice PDF" src={fileUrl(runId)} className="mt-4 h-[640px] w-full rounded border border-gray-200 bg-white" />
        </div>
      </div>
    </section>
  );
}
