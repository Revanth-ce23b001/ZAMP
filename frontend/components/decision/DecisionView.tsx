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

  if (error) return <div role="alert" className="notice notice-bad">{error}</div>;
  if (!d) return <div className="animate-pulse space-y-4"><div className="h-28 rounded-card bg-hover" /><div className="h-40 rounded-card bg-hover" /></div>;

  if (d.status === "ERROR") {
    return (
      <div role="alert" className="notice notice-bad">
        <p className="font-semibold">{d.run_display}: processing error (no decision)</p>
        <p className="mt-1 text-sm">{d.error_message}</p>
        <button type="button" className="btn btn-primary mt-3"
                onClick={() => retryRun(runId).then(() => { window.location.href = `/runs/${runId}/live`; })}>Retry</button>
      </div>
    );
  }
  const dec = d.decision;
  if (!dec) {
    return <p>{d.run_display} is {d.status.toLowerCase()}. <Link className="font-medium underline" href={`/runs/${runId}/live`}>Watch it live</Link>.</p>;
  }
  const bank = dec.reason_codes.includes("BANK_DETAILS_MISMATCH");

  return (
    <section>
      {existing && (
        <div className="notice notice-run mb-5">
          This exact file was already processed in {d.run_display}. It was not processed again.
        </div>
      )}

      {/* Header */}
      <div className="card flex flex-wrap items-start gap-6" data-testid="decision-header">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-3">
            <DecisionBadge status={dec.status} large />
            <h1 className="page-title">{d.invoice?.invoice_number ?? "No invoice number"}</h1>
          </div>
          <div className="mt-1.5 text-[15px] text-ink-2">{d.vendor?.master?.legal_name ?? d.invoice?.vendor_name ?? "Unknown vendor"}</div>
          <div className="mt-3 flex flex-wrap items-center gap-x-2 gap-y-1 text-[13px] text-ink-3">
            <span className="font-mono">{d.run_display}</span>
            <span>· Decided by {DECIDED_BY[dec.decided_by] ?? dec.decided_by}</span>
            <span>· Policy <span className="font-mono" title={dec.policy_version}>{dec.policy_version.slice(0, 8)}</span></span>
            {dec.committed && <span>· Committed</span>}
            <span>·</span>
            <Link href={`/runs/${runId}/live`} className="font-medium underline">Processing timeline</Link>
          </div>
        </div>
        <div className="ml-auto text-right">
          <div className="micro">Total</div>
          <div className="mt-1 text-[28px] font-semibold leading-none tracking-tight tabular-nums">{d.invoice?.total?.display ?? "—"}</div>
        </div>
      </div>

      {/* Security banner (FR-042) */}
      {bank && d.trusted_contact && (
        <div role="alert" data-testid="security-banner" className="notice notice-bad mt-5 !border-bad-dot">
          <strong>Security exception.</strong> Bank details differ from vendor master. Verify with {d.trusted_contact.name},
          {" "}{d.trusted_contact.phone} (vendor master). Do not use contact details printed on this invoice.
        </div>
      )}

      {/* Why (FR-085): one sentence and one next action per code, severity order */}
      <div className="card mt-5" data-testid="why-panel">
        <h2 className="card-title">Why</h2>
        {dec.reason_codes.length === 0 ? (
          <p className="mt-2 text-ink-2">All checks passed: clean two-way match within policy.</p>
        ) : (
          <ul className="mt-3 divide-y divide-line">
            {dec.reason_codes.map((c, i) => (
              <li key={c} className="grid gap-x-4 gap-y-1 py-3 first:pt-0 last:pb-0 md:grid-cols-[minmax(0,15rem)_1fr]" data-testid="why-item">
                <span><CodeChip code={c} /></span>
                <div>
                  <span>{dec.summary[i]}</span>
                  <div className="mt-0.5 text-[13px] text-ink-3">Next action: {dec.next_actions[i]}</div>
                </div>
              </li>))}
          </ul>
        )}
        {dec.status === "APPROVE" && dec.reason_codes.length > 0 && (
          <p className="mt-3 text-[13px] text-ink-3">Approved by {DECIDED_BY[dec.decided_by]?.toLowerCase()} of the codes above; see Audit.</p>
        )}
        {dec.notes.length > 0 && (
          <ul className="mt-3 list-disc pl-5 text-[13px] text-ink-3">{dec.notes.map((n) => <li key={n}>{n}</li>)}</ul>
        )}
      </div>

      <div className="mt-5 flex flex-col gap-5 lg:flex-row">
        <div className="min-w-0 lg:w-3/5">
          <div className="flex flex-wrap gap-1 border-b border-line" role="tablist">
            {TABS.map(([k, label]) => (
              <button key={k} type="button" role="tab" aria-selected={tab === k} onClick={() => setTab(k)}
                      className={"-mb-px min-h-11 border-b-2 px-3 text-sm " +
                        (tab === k ? "border-accent font-semibold text-ink" : "border-transparent font-medium text-ink-3 hover:text-ink")}>
                {label}
              </button>))}
          </div>
          <div className="card mt-4 overflow-x-auto">
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
          <iframe title="Invoice PDF" src={fileUrl(runId)} className="mt-4 h-[640px] w-full rounded-card first:mt-0 border border-line bg-surface" />
        </div>
      </div>
    </section>
  );
}
