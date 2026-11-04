"use client";

// Measured accuracy (DEC-018). Every number comes from GET /api/accuracy, which serves the committed
// measurement file (backend/seed/extraction_accuracy.json). Nothing here is computed or hardcoded.

import { Fragment, useEffect, useState } from "react";

import { CodeChip } from "@/components/ui";
import { getAccuracy, type Accuracy, type ApiError } from "@/lib/client";
import { rupees } from "@/lib/format";

const LABEL: Record<string, string> = {
  invoice_number: "Invoice number", invoice_date: "Invoice date", vendor_name: "Vendor name",
  vendor_gstin: "Vendor GSTIN", po_number: "PO reference", currency: "Currency", document_type: "Document type",
  subtotal_paise: "Subtotal", tax_total_paise: "Tax total", invoice_total_paise: "Invoice total", line_count: "Line count",
};

// Money is integer paise end to end; it is formatted only here.
function value(field: string, v: unknown): string {
  if (v === null || v === undefined) return "null (not returned)";
  if (field.endsWith("_paise") && typeof v === "number") return rupees(v / 100);
  return String(v);
}

const pct = (a: number, b: number) => (b ? `${((a * 100) / b).toFixed(1)}%` : "—");

export default function AccuracyPage() {
  const [a, setA] = useState<Accuracy | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    getAccuracy().then(setA).catch((e: ApiError) => setError(e.message ?? "Could not load the measurement"));
  }, []);

  return (
    <section className="max-w-5xl">
      <h1 className="page-title">Measured accuracy</h1>
      <p className="mt-3 text-[15px] text-ink-2">
        Per-field extraction accuracy and decision accuracy are different measurements. The model that reads the
        invoice gets some fields wrong; the deterministic rules that decide are built so those errors are either
        caught or do not change the outcome. The gap between the two numbers below is the point of the architecture.
      </p>

      {error && <div role="alert" className="notice notice-bad mt-6">{error}</div>}
      {!a && !error && <div className="mt-6 h-64 animate-pulse rounded-card bg-hover" />}

      {a && (
        <>
          <div className="mt-6 grid gap-4 md:grid-cols-3">
            <div className="card">
              <div className="micro">Field-level extraction</div>
              <div className="mt-2 text-[27px] font-semibold leading-none tabular-nums" data-testid="acc-fields-headline">
                {a.field_totals.correct}/{a.field_totals.total}
              </div>
              <div className="mt-1 text-[13px] text-ink-3 tabular-nums">{pct(a.field_totals.correct, a.field_totals.total)} of checked fields exact</div>
            </div>
            <div className="card">
              <div className="micro">Decisions</div>
              <div className="mt-2 text-[27px] font-semibold leading-none tabular-nums" data-testid="acc-decisions-headline">
                {a.decision_accuracy.correct}/{a.decision_accuracy.total}
              </div>
              <div className="mt-1 text-[13px] text-ink-3 tabular-nums">{pct(a.decision_accuracy.correct, a.decision_accuracy.total)} status and reason codes exact</div>
            </div>
            <div className="card">
              <div className="micro">Unresolved ground truth</div>
              <div className="mt-2 text-[27px] font-semibold leading-none tabular-nums">{a.field_totals.unresolved}</div>
              <div className="mt-1 text-[13px] text-ink-3">field values the PDFs do not establish; excluded from both counts</div>
            </div>
          </div>

          <h2 className="section-title mt-10">Extraction, per field</h2>
          <p className="mt-1 text-[13px] text-ink-3">{a._meta.match_rule}.</p>
          <div className="card-flush mt-3 overflow-x-auto">
            <table className="tbl" data-testid="accuracy-fields">
              <thead><tr><th>Field</th><th className="num">Correct / total</th><th className="num">Accuracy</th><th className="num">Unresolved</th><th>Misses: sample, expected, returned</th></tr></thead>
              <tbody>
                {a.field_accuracy.map((f) => (
                  <tr key={f.field} data-testid="accuracy-field" data-field={f.field}>
                    <td className="font-medium">{LABEL[f.field] ?? f.field}</td>
                    <td className="num">{f.correct}/{f.total}</td>
                    <td className="num">{pct(f.correct, f.total)}</td>
                    <td className="num">{f.unresolved > 0 ? <span title={f.unresolved_samples.map((u) => u.test_id).join(", ")}>{f.unresolved} unresolved</span> : "0"}</td>
                    <td>
                      {f.misses.length === 0 ? <span className="text-ink-3">none</span> : (
                        <ul className="space-y-1">
                          {f.misses.map((x) => (
                            <li key={x.test_id} className="text-[13px]">
                              <span className="font-mono text-ink-2">{x.test_id}</span>: expected <span className="font-mono">{value(f.field, x.expected)}</span>,
                              {" "}returned <span className="font-mono text-bad">{value(f.field, x.returned)}</span>
                            </li>))}
                        </ul>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {a.field_accuracy.some((f) => f.unresolved > 0) && (
            <div className="card mt-4" data-testid="accuracy-unresolved">
              <h3 className="card-title">Unresolved fields (shown, not hidden)</h3>
              <ul className="mt-2 space-y-1 text-[13px] text-ink-2">
                {a.field_accuracy.filter((f) => f.unresolved > 0).map((f) => {
                  const reasons = [...new Set(f.unresolved_samples.map((u) => u.reason))];
                  return (
                    <li key={f.field}>
                      <span className="font-medium text-ink">{LABEL[f.field] ?? f.field}</span>, {f.unresolved} sample{f.unresolved === 1 ? "" : "s"}
                      {" "}({f.unresolved_samples.map((u) => u.test_id).join(", ")}): {reasons.join(" / ")}
                    </li>);
                })}
              </ul>
            </div>
          )}

          <h2 className="section-title mt-10">Decisions, expected vs actual</h2>
          <p className="mt-1 text-[13px] text-ink-3">
            Correct only if the status and the full set of reason codes match. Expected: {a.decision_accuracy.source}.
            Actual: every sample run through the real pipeline from the cached extractions, on a fresh seeded database.
          </p>
          <div className="card-flush mt-3 overflow-x-auto">
            <table className="tbl" data-testid="accuracy-decisions">
              <thead><tr><th>Scenario</th><th>Expected</th><th>Actual</th><th>Match</th></tr></thead>
              <tbody>
                {a.decision_accuracy.rows.map((r) => (
                  <tr key={r.test_id} data-testid="accuracy-decision" data-correct={r.correct ? "1" : "0"}>
                    <td className="font-mono text-ink-2">{r.test_id}</td>
                    <td><span className="font-medium">{r.expected_status}</span> {r.expected_codes.map((c) => <Fragment key={c}> <CodeChip code={c} /></Fragment>)}</td>
                    <td><span className="font-medium">{r.actual_status}</span> {r.actual_codes.map((c) => <Fragment key={c}> <CodeChip code={c} /></Fragment>)}</td>
                    <td>{r.correct
                      ? <span className="status s-ok"><span className="dot" aria-hidden />Match</span>
                      : <span className="status s-bad"><span className="dot" aria-hidden />Mismatch</span>}</td>
                  </tr>))}
              </tbody>
            </table>
          </div>

          <h2 className="section-title mt-10">What absorbed the misses</h2>
          <div className="mt-3 space-y-4">
            {a.absorbed.map((x) => (
              <div key={x.field} className="card" data-testid="absorbed" data-field={x.field}>
                <div className="flex flex-wrap items-baseline gap-x-3">
                  <h3 className="card-title">{LABEL[x.field] ?? x.field}: {x.misses} miss{x.misses === 1 ? "" : "es"}</h3>
                  <span className="font-mono text-xs text-ink-3">{x.code_path}</span>
                </div>
                <p className="mt-2 text-ink-2">{x.mechanism}</p>
                <ul className="mt-3 space-y-1 text-[13px] text-ink-3">
                  {x.evidence.map((e, i) => (
                    <li key={i}>
                      <span className="font-mono text-ink-2">{e.test_id}</span> · {e.check}: <span className="text-ink-2">{e.result}</span>
                      {e.allowed ? ` (${e.allowed})` : ""}{e.variance ? `, ${e.variance}` : ""}{e.reason_code ? ` · ${e.reason_code}` : ""}
                    </li>))}
                </ul>
              </div>
            ))}
          </div>

          <h2 className="section-title mt-10">Fallback model</h2>
          <div className="card mt-3 text-ink-2" data-testid="accuracy-fallback">
            {a.fallback_chain.supported ? (
              <p>
                {a.fallback_chain.fallback_model}: {a.fallback_chain.outcomes_reproduced} of {a.fallback_chain.samples_measured} outcomes
                {" "}reproduced; {a.fallback_chain.samples_with_wrong_outcome?.join(" and ")} gained a spurious
                {" "}<CodeChip code={a.fallback_chain.spurious_code ?? ""} />.
                <span className="mt-1 block text-[13px] text-ink-3">From {a.fallback_chain.source}: &ldquo;{a.fallback_chain.quoted_line}&rdquo;</span>
              </p>
            ) : <p>{a.fallback_chain.note}</p>}
          </div>

          <p className="mt-8 border-t border-line pt-4 text-[13px] text-ink-3 tabular-nums" data-testid="accuracy-footer">
            Cached extractions: {Object.entries(a.models.cached_extractions_by_model).map(([k, n]) => `${k} (${n})`).join(", ")}.
            {" "}Configured chain: {a.models.configured_chain.text.join(" → ")}.
            {" "}Policy <span className="font-mono">{a._meta.policy_version.slice(0, 12)}</span>.
            {" "}Measured {a._meta.measured_at.slice(0, 10)}, {a._meta.sample_size} samples, {a._meta.model_calls_made} model calls.
            {" "}{a._meta.dataset[0].toUpperCase() + a._meta.dataset.slice(1)}.
          </p>
        </>
      )}
    </section>
  );
}
