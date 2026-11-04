"use client";

// The six decision-detail tabs (PRD §10.5). Display only: every value comes from the backend.
// Vocabulary per FR-034 (honest labels only).

import Link from "next/link";
import { useMemo, useState } from "react";

import { CodeChip, ConfidenceChip, ResultChip } from "@/components/ui";
import type { AuditLog, Evidence, RunDetail, VendorSide } from "@/lib/client";
import { qty, rupees, show, when } from "@/lib/format";

// Table cells take their padding, dividers and micro-label heads from .tbl (globals.css).
const th = "";
const td = "";

// FR-017 critical fields
const CRITICAL = new Set(["invoice_number", "invoice_date", "vendor_name", "vendor_gstin", "total", "po_reference"]);
const FIELDS: [string, string, "text" | "money" | "rate"][] = [
  ["invoice_number", "Invoice number", "text"], ["invoice_date", "Invoice date", "text"],
  ["vendor_name", "Vendor name", "text"], ["vendor_gstin", "Vendor GSTIN", "text"],
  ["po_reference", "PO reference", "text"], ["document_type", "Document type", "text"], ["currency", "Currency", "text"],
  ["subtotal", "Subtotal", "money"], ["discount", "Discount", "money"], ["taxable_value", "Taxable value", "money"],
  ["cgst", "CGST", "money"], ["cgst_rate", "CGST rate", "rate"], ["sgst", "SGST", "money"], ["sgst_rate", "SGST rate", "rate"],
  ["igst", "IGST", "money"], ["igst_rate", "IGST rate", "rate"], ["round_off", "Round-off", "money"],
  ["total", "Total", "money"], ["bank_account_number", "Bank account (masked)", "text"], ["ifsc", "IFSC", "text"],
];

function fmt(kind: string, v: unknown) {
  if (v === null || v === undefined) return "—";
  return kind === "money" ? rupees(v) : kind === "rate" ? `${v}%` : String(v);
}

// ------------------------------------------------------------------ Extracted data

export function ExtractedTab({ d }: { d: RunDetail }) {
  const f = d.extraction?.fields ?? {};
  const lines: any[] = f.line_items ?? [];
  const charges: any[] = f.other_charges ?? [];
  const arithmetic = (d.decision?.evidence ?? []).filter((e) => e.check.startsWith("arithmetic_"));
  const poPrinted = !!f.po_reference?.value;
  return (
    <div className="space-y-6" data-testid="tab-extracted">
      <p className="text-xs text-ink-3">
        {d.extraction?.ingest_method === "vision" ? "Scanned document, read by the vision model" : "Digital PDF, text layer read"} ·
        {" "}{d.extraction?.provider}/{d.extraction?.model}
        {d.extraction?.cached && <span className="status s-nc ml-2"><span className="dot" aria-hidden />Cached extraction</span>}
        {" "}· confidence is model-reported, not a calibrated probability
      </p>
      <table className="tbl">
        <thead><tr><th className={th}>Field</th><th className={th}>Value</th><th className={th}>Confidence</th><th className={th}>Critical</th></tr></thead>
        <tbody>
          {FIELDS.map(([key, label, kind]) => {
            const fld = f[key] ?? {};
            const critical = CRITICAL.has(key) && (key !== "po_reference" || poPrinted);
            return (
              <tr key={key}>
                <td className={td}>{label}</td>
                <td className={td + " font-mono"}>{fmt(kind, fld.value)}</td>
                <td className={td}><ConfidenceChip value={fld.confidence} /></td>
                <td className={td}>{critical ? <span title="Critical field (FR-017)" className="text-xs font-semibold text-ink-2">● critical</span> : ""}</td>
              </tr>
            );
          })}
        </tbody>
      </table>

      <div>
        <h3 className="card-title mb-2">Line items</h3>
        <table className="tbl" data-testid="line-items">
          <thead><tr><th className={th}>#</th><th className={th}>Item code</th><th className={th}>Description</th><th className={th}>Qty</th><th className={th + " num"}>Unit price</th><th className={th + " num"}>Amount</th></tr></thead>
          <tbody>
            {lines.map((l, i) => (
              <tr key={i}>
                <td className={td}>{i + 1}</td>
                <td className={td + " font-mono"}>{show(l.item_code?.value)}</td>
                <td className={td}>{show(l.description?.value)}</td>
                <td className={td}>{show(l.quantity?.value)} <ConfidenceChip value={l.quantity?.confidence} /></td>
                <td className={td + " num whitespace-nowrap"}>{rupees(l.unit_price?.value)} <ConfidenceChip value={l.unit_price?.confidence} /></td>
                <td className={td + " num whitespace-nowrap"}>{rupees(l.amount?.value)}</td>
              </tr>
            ))}
            {charges.map((c, i) => (
              <tr key={`c${i}`} className="text-ink-3">
                <td className={td} /><td className={td} /><td className={td}>Other charge: {c.label}</td><td className={td} /><td className={td} />
                <td className={td + " num whitespace-nowrap"}>{rupees(c.amount?.value)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div>
        <h3 className="card-title mb-2">Arithmetic (each equation allows ±₹1)</h3>
        <table className="tbl" data-testid="arithmetic">
          <thead><tr><th className={th}>Equation</th><th className={th + " num"}>Expected</th><th className={th + " num"}>Actual</th><th className={th + " num"}>Variance</th><th className={th + " num"}>Allowed</th><th className={th}>Result</th></tr></thead>
          <tbody>
            {arithmetic.map((e, i) => (
              <tr key={i}>
                <td className={td}>{e.check.replace("arithmetic_", "")}</td>
                <td className={td + " num whitespace-nowrap"}>{show(e.expected)}</td><td className={td + " num whitespace-nowrap"}>{show(e.actual)}</td>
                <td className={td + " num whitespace-nowrap"}>{show(e.variance)}</td><td className={td + " num whitespace-nowrap"}>{show(e.allowed)}</td>
                <td className={td}><ResultChip result={e.result} /></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ------------------------------------------------------------------ Vendor & bank

export function VendorBankTab({ d }: { d: RunDetail }) {
  const v = d.vendor;
  if (!v) return <p className="text-sm text-ink-3">Vendor and bank checks did not run.</p>;
  const inv: VendorSide = v.invoice;
  const m: VendorSide | null = v.master;
  const rows: [string, string | null | undefined, string | null | undefined, boolean][] = [
    ["Legal name", inv.legal_name, m?.legal_name, !!m && !!inv.legal_name && inv.legal_name !== m.legal_name],
    ["GSTIN", inv.gstin, m?.gstin, !!m && !!inv.gstin && inv.gstin !== m.gstin],
    ["State code", inv.state_code, m ? `${m.state_code} (${m.state_name})` : null, !!m && !!inv.state_code && inv.state_code !== m.state_code],
    ["Status", null, m?.status, m?.status === "blocked"],
    ["Bank account", inv.bank_account_masked, m?.bank_account_masked,
     !!m && !!inv.bank_account_masked && inv.bank_account_masked !== m.bank_account_masked],
    ["IFSC", inv.ifsc, m?.ifsc, !!m && !!inv.ifsc && inv.ifsc !== m.ifsc],
  ];
  return (
    <div className="space-y-4" data-testid="tab-vendor">
      {!v.identified && (
        <div className="notice notice-hold">
          Vendor not identified in the vendor master{v.identification_note ? `: ${v.identification_note}` : ""}.
        </div>
      )}
      <table className="tbl">
        <thead><tr><th className={th}>Field</th><th className={th}>Invoice</th><th className={th}>Vendor master{m ? ` (${m.vendor_id})` : ""}</th></tr></thead>
        <tbody>
          {rows.map(([label, a, b, diff]) => (
            <tr key={label} className={diff ? "bg-bad-bg" : ""} data-mismatch={diff ? "1" : undefined}>
              <td className={td}>{label}</td>
              <td className={td + " font-mono"}>{show(a)}</td>
              <td className={td + " font-mono"}>{show(b)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <ul className="space-y-1 text-sm">
        {v.labels.gstin_format && <li>GSTIN: <strong>{v.labels.gstin_format}</strong></li>}
        {v.labels.gstin_master && <li>GSTIN: <strong>{v.labels.gstin_master}</strong></li>}
        {v.labels.bank && <li>Bank details: <strong>{v.labels.bank}</strong> <ResultChip result={v.labels.bank_result} /></li>}
      </ul>
      <p className="text-xs text-ink-3">
        Checked against the vendor master only. GST registration and bank account ownership are not verified externally.
      </p>
    </div>
  );
}

// ------------------------------------------------------------------ Duplicates

export function DuplicatesTab({ d }: { d: RunDetail }) {
  const matches = d.decision?.duplicate_matches ?? [];
  const notes = (d.decision?.notes ?? []).filter((n) => n.startsWith("Similar invoice"));
  const dupStage = d.stage_results.find((s) => s.stage === "DUPLICATE");
  if (dupStage?.status === "NOT_CHECKED") {
    return <p className="text-sm text-ink-3" data-testid="tab-duplicates">Not checked: {dupStage.summary.replace("Not checked: ", "")}.</p>;
  }
  const blocking = matches.filter((m) => m.tier !== "similar");
  return (
    <div className="space-y-4" data-testid="tab-duplicates">
      {blocking.length === 0 ? <p className="text-sm text-ink-3">No duplicates found.</p> : (
        <table className="tbl">
          <thead><tr><th className={th}> </th><th className={th}>This invoice</th>
            {blocking.map((m) => <th key={m.ledger_id} className={th}>Matched ({m.tier})</th>)}</tr></thead>
          <tbody>
            {([
              ["Number (raw)", d.invoice?.invoice_number, (m: any) => m.invoice_number_raw],
              ["Number (normalised)", d.invoice?.invoice_number_normalised, (m: any) => m.invoice_number_normalised],
              ["Date", d.invoice?.invoice_date, (m: any) => m.invoice_date],
              ["Total", d.invoice?.total?.display, (m: any) => m.total.display],
              ["Lines", d.invoice?.line_signature?.map((l) => `${l.key} × ${qty(l.qty)}`).join(", "),
               (m: any) => m.lines.map((l: any) => `${l.key} × ${qty(l.qty)}`).join(", ")],
              ["Status", d.decision?.status, (m: any) => m.status],
            ] as [string, any, (m: any) => any][]).map(([label, mine, get]) => (
              <tr key={label}>
                <td className={td + " text-ink-3"}>{label}</td><td className={td}>{show(mine)}</td>
                {blocking.map((m) => <td key={m.ledger_id} className={td}>{show(get(m))}</td>)}
              </tr>
            ))}
            <tr>
              <td className={td + " text-ink-3"}>Original</td><td className={td} />
              {blocking.map((m) => (
                <td key={m.ledger_id} className={td}>
                  {m.run_id ? <Link className="font-medium underline" href={`/runs/${m.run_id}`}>{m.run_display}</Link>
                            : <span className="text-ink-3">Seeded history ({m.ledger_id})</span>}
                </td>))}
            </tr>
          </tbody>
        </table>
      )}
      {notes.length > 0 && (
        <div>
          <h3 className="card-title">Similar invoices (informational, no effect on the decision)</h3>
          <ul className="list-disc pl-5 text-sm">{notes.map((n) => <li key={n}>{n}</li>)}</ul>
        </div>
      )}
    </div>
  );
}

// ------------------------------------------------------------------ PO match

export function PoMatchTab({ d }: { d: RunDetail }) {
  const p = d.decision?.po_detail;
  const cands = d.decision?.po_candidates ?? [];
  const poStage = d.stage_results.find((s) => s.stage === "PO_MATCH");
  return (
    <div className="space-y-5" data-testid="tab-po">
      <p className="text-xs text-ink-3">Two-way match: invoice against purchase order. Goods receipt is not checked.</p>
      {!p && <p className="text-sm text-ink-2">{poStage?.summary}</p>}
      {p && (
        <div data-testid="po-header">
          <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
            <span className="card-title font-mono" data-testid="po-po-number">{p.po_number} ({p.method})</span>
            <span className="text-[13px] text-ink-3" data-testid="po-ceiling">Ceiling {p.ceiling.display} (PO total + allowed overrun)</span>
          </div>
          <div className="mt-3 grid grid-cols-2 overflow-hidden rounded-card border border-line md:grid-cols-5">
            {([
              ["PO total", p.po_total.display, null],
              ["Previously approved", p.previously_approved.display,
               `${p.invoices_previously_approved} invoice${p.invoices_previously_approved === 1 ? "" : "s"}`],
              ["This invoice (taxable)", p.this_invoice?.display ?? "—", null],
              ["Remaining", p.remaining.display, null],
              ["Allowed overrun", p.allowed_overrun.display, "separate from remaining"],
            ] as [string, string, string | null][]).map(([k, v, sub]) => (
              <div key={k} data-testid={`po-${k.toLowerCase().replace(/[^a-z]+/g, "-")}`}
                   className={"min-w-0 border-line px-3 py-4 [&:not(:first-child)]:border-l " + (k === "Allowed overrun" ? "bg-sunken" : "bg-surface")}>
                <div className="micro">{k}</div>
                <div className="mt-1.5 whitespace-nowrap text-[16px] font-semibold tabular-nums">{v}</div>
                {sub && <div className="text-xs text-ink-3">{sub}</div>}
              </div>
            ))}
          </div>
        </div>
      )}
      {cands.length > 0 && (
        <div>
          <h3 className="card-title mb-2">Candidate POs (deterministic score: 60 lines + 30 value + 10 date)</h3>
          <table className="tbl" data-testid="po-candidates">
            <thead><tr><th className={th}>PO</th><th className={th}>Score</th><th className={th}>Breakdown</th><th className={th}> </th></tr></thead>
            <tbody>{cands.map((c) => (
              <tr key={c.po_number}>
                <td className={td}>{c.po_number}</td><td className={td}>{c.score}</td>
                <td className={td + " text-ink-3"}>lines {show(c.breakdown.line_pairing)} ({show(c.breakdown.paired)}) · value {show(c.breakdown.value_fits)} · date {show(c.breakdown.date_window)}</td>
                <td className={td}>{c.plausible ? <span className="status s-run"><span className="dot" aria-hidden />plausible</span> : ""}</td>
              </tr>))}</tbody>
          </table>
        </div>
      )}
      {p && (
        <div>
          <h3 className="card-title mb-2">Line diff</h3>
          <table className="tbl" data-testid="po-lines">
            <thead><tr>
              <th className={th}>Invoice line</th><th className={th}>PO line</th><th className={th}>Paired by</th>
              <th className={th}>Qty: PO / prev / this / excess</th><th className={th}>Unit price: PO / invoice / var / allowed</th><th className={th}>Result</th>
            </tr></thead>
            <tbody>{p.lines.map((l) => (
              <tr key={l.invoice_line}>
                <td className={td}>{l.invoice_line}. {show(l.description)}</td>
                <td className={td + " whitespace-nowrap"}>{l.po_line_id ? <>{l.po_line_id} <span className="code">{l.po_item_code}</span></> : "no matching PO line"}</td>
                <td className={td}>{show(l.pairing)}</td>
                <td className={td}>{l.quantity ? `${qty(l.quantity.po)} / ${qty(l.quantity.previously_approved)} / ${qty(l.quantity.this_invoice)} / ${qty(l.quantity.excess)}` : "—"}</td>
                <td className={td}>{l.unit_price ? `${l.unit_price.po.display} / ${l.unit_price.invoice.display} / ${l.unit_price.variance_pct >= 0 ? "+" : ""}${l.unit_price.variance_pct}% / ${l.unit_price.allowed_pct}%` : "—"}</td>
                <td className={td}><ResultChip result={l.result} /></td>
              </tr>))}</tbody>
          </table>
        </div>
      )}
    </div>
  );
}

// ------------------------------------------------------------------ Evidence

const STAGES = ["VALIDATE", "VENDOR_BANK", "DUPLICATE", "PO_MATCH"];
const RESULTS = ["PASSED", "FAILED", "NEEDS_REVIEW", "NOT_CHECKED", "INFO"];

export function EvidenceTab({ evidence }: { evidence: Evidence[] }) {
  const [stage, setStage] = useState("");
  const [result, setResult] = useState("");
  const rows = useMemo(() => evidence.filter((e) => (!stage || e.stage === stage) && (!result || e.result === result)),
                       [evidence, stage, result]);
  return (
    <div className="space-y-3" data-testid="tab-evidence">
      <div className="flex gap-2 text-sm">
        <select aria-label="Filter by stage" value={stage} onChange={(e) => setStage(e.target.value)} className="input">
          <option value="">All stages</option>{STAGES.map((s) => <option key={s} value={s}>{s}</option>)}
        </select>
        <select aria-label="Filter by result" value={result} onChange={(e) => setResult(e.target.value)} className="input">
          <option value="">All results</option>{RESULTS.map((r) => <option key={r} value={r}>{r}</option>)}
        </select>
        <span className="self-center text-ink-3">{rows.length} of {evidence.length}</span>
      </div>
      <table className="tbl text-xs">
        <thead><tr>{["Stage", "Check", "Source fields", "Expected", "Actual", "Variance", "Allowed", "Result", "Code"].map((h) => <th key={h} className={th}>{h}</th>)}</tr></thead>
        <tbody>{rows.map((e, i) => (
          <tr key={i} data-testid="evidence-row">
            <td className={td}>{e.stage}</td><td className={td}>{e.check}</td><td className={td}>{e.source_fields.join(", ")}</td>
            <td className={td}>{show(e.expected)}</td><td className={td}>{show(e.actual)}</td><td className={td}>{show(e.variance)}</td>
            <td className={td}>{show(e.allowed)}</td><td className={td}><ResultChip result={e.result} /></td>
            <td className={td}>{e.reason_code ? <CodeChip code={e.reason_code} /> : ""}</td>
          </tr>))}</tbody>
      </table>
    </div>
  );
}

// ------------------------------------------------------------------ Audit

function describe(e: AuditLog["events"][number]): string {
  const p = e.payload;
  switch (e.type) {
    case "run_created": return `File received: ${p.file_name}`;
    case "stage_started": return `${p.stage} started${p.after_review ? " (after review)" : ""}`;
    case "stage_completed": return `${p.stage}: ${p.status} — ${p.summary}`;
    case "extraction_cached": return `Cached extraction used (${p.provider}/${p.model})`;
    case "extraction_called": return p.ok ? `Extraction by ${p.provider}/${p.model}` : `Extraction failed on ${p.provider}/${p.model}${p.fallback ? `, fell back to ${p.fallback_to}` : ""}`;
    case "decision": return `Decision: ${p.status}${p.reason_codes?.length ? ` (${p.reason_codes.join(", ")})` : ""}`;
    case "commit": return p.message ?? "Committed";
    case "commit_blocked": return p.message ?? "Commit blocked";
    case "override": return `Override (${p.action}) by ${p.reviewer_name}: ${p.original_status} → ${p.new_status}, commit ${p.commit_result}`;
    case "error": return `Error at ${p.stage}: ${p.message}`;
    case "retry": return "Retry requested";
    default: return e.type;
  }
}

export function AuditTab({ audit }: { audit: AuditLog | null }) {
  if (!audit) return <p className="text-sm text-ink-3">Loading audit trail…</p>;
  return (
    <div className="space-y-5" data-testid="tab-audit">
      <ol className="space-y-1 text-sm">
        {audit.events.map((e) => (
          <li key={e.event_id} className="flex gap-3 border-b border-line py-2 last:border-0">
            <span className="w-40 shrink-0 text-xs text-ink-3 tabular-nums">{when(e.created_at)}</span>
            <span className="w-36 shrink-0 font-mono text-xs text-ink-2">{e.type}</span>
            <span>{describe(e)}</span>
          </li>))}
      </ol>
      {audit.overrides.length > 0 && (
        <div>
          <h3 className="card-title mb-2">Override records</h3>
          <table className="tbl text-xs" data-testid="override-records">
            <thead><tr>{["When", "Action", "Reviewer", "Status", "Codes overridden", "Reason", "Bank verification", "Confirmed PO", "Commit"].map((h) => <th key={h} className={th}>{h}</th>)}</tr></thead>
            <tbody>{audit.overrides.map((o) => (
              <tr key={o.override_id}>
                <td className={td}>{when(o.created_at)}</td><td className={td}>{o.action}</td><td className={td}>{o.reviewer_name}</td>
                <td className={td}>{o.original_status} → {o.new_status}</td><td className={td}>{o.codes_overridden.join(", ")}</td>
                <td className={td}>{o.reason}</td>
                <td className={td}>{o.bank_verification ? `${o.bank_verification.verified_with}, ${o.bank_verification.channel}, ${o.bank_verification.outcome}` : "—"}</td>
                <td className={td}>{show(o.confirmed_po)}</td>
                <td className={td}>{o.commit_result}{o.block_reason ? `: ${o.block_reason}` : ""}</td>
              </tr>))}</tbody>
          </table>
        </div>
      )}
    </div>
  );
}
