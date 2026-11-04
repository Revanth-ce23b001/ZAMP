"use client";

// Measured accuracy (DEC-018). Every number comes from GET /api/accuracy, which serves the committed
// measurement file (backend/seed/extraction_accuracy.json). Nothing here is computed or hardcoded.

import { Fragment, useEffect, useState } from "react";

import { CodeChip } from "@/components/ui";
import { getAccuracy, type Accuracy, type AccuracyField, type ApiError } from "@/lib/client";
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

// Fields that failed first; ties keep the measurement's field order (Array.sort is stable).
const byMisses = (rows: AccuracyField[]) => [...rows].sort((x, y) => y.misses.length - x.misses.length);

// The lead sentence, built from the measurement so it stays true if the measurement changes.
function leadStatement(a: Accuracy): string {
  const worst = byMisses(a.field_accuracy)[0];
  const d = a.decision_accuracy;
  const decisions = `${d.correct} of ${d.total} decisions were correct.`;
  if (!worst || worst.misses.length === 0) return `Every checked field was extracted correctly. ${decisions}`;
  return `${LABEL[worst.field] ?? worst.field} was extracted incorrectly on ${worst.misses.length} of ${worst.total} invoices. ${decisions}`;
}

export default function AccuracyPage() {
  const [a, setA] = useState<Accuracy | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    getAccuracy().then(setA).catch((e: ApiError) => setError(e.message ?? "Could not load the measurement"));
  }, []);

  return (
    <section className="max-w-5xl">
      <h1 className="page-title">Measured accuracy</h1>
      {a && (
        <p className="mt-3 text-[18px] font-medium leading-snug text-ink" data-testid="acc-lead">{leadStatement(a)}</p>
      )}
      <p className="mt-2 text-[15px] text-ink-2">
        The deterministic layer does not trust extraction on its own, so a field-level miss does not become a
        decision-level error. Per-field extraction accuracy and decision accuracy are different measurements; the
        gap between them is the point of the architecture.
      </p>

      {error && <div role="alert" className="notice notice-bad mt-6">{error}</div>}
      {!a && !error && <div className="mt-6 h-64 animate-pulse rounded-card bg-hover" />}

      {a && (
        <>
          <h2 className="section-title mt-10">Extraction, per field</h2>
          <p className="mt-1 text-[13px] text-ink-3">{a._meta.match_rule}.</p>
          <div className="card-flush mt-3 overflow-x-auto">
            <table className="tbl" data-testid="accuracy-fields">
              <thead><tr><th>Field</th><th className="num">Misses</th><th className="num">Correct / total</th><th className="num">Accuracy</th><th className="num">Not stated in document</th><th>Misses: sample, expected, returned</th></tr></thead>
              <tbody>
                {byMisses(a.field_accuracy).map((f) => (
                  <tr key={f.field} data-testid="accuracy-field" data-field={f.field}>
                    <td className="whitespace-nowrap font-medium">{LABEL[f.field] ?? f.field}</td>
                    <td className={"num " + (f.misses.length ? "font-semibold text-bad" : "text-ink-3")}>{f.misses.length}</td>
                    <td className="num">{f.correct}/{f.total}</td>
                    <td className="num">{pct(f.correct, f.total)}</td>
                    <td className="num">{f.unresolved > 0 ? <span title={f.unresolved_samples.map((u) => u.test_id).join(", ")}>{f.unresolved}</span> : "0"}</td>
                    <td>
                      {f.misses.length === 0 ? <span className="text-ink-3">none</span> : (
                        <ul className="space-y-1">
                          {f.misses.map((x) => (
                            <li key={x.test_id} className="text-[13px]">
                              <span className="font-mono text-ink-2">{x.test_id}</span>: expected <span className="font-mono">{value(f.field, x.expected)}</span>,
                              {" "}returned <span className="font-mono text-bad">{value(f.field, x.returned)}</span>
                              {f.field === "subtotal_paise" && (
                                <span className="mt-0.5 block text-ink-3" data-testid="graceful-degradation">
                                  <span className="status s-nc mr-1"><span className="dot" aria-hidden />Graceful degradation</span>
                                  amounts() used the sum of line amounts in place of the missing subtotal, and E1 was marked Not Checked.
                                </span>
                              )}
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
              <h3 className="card-title">Not stated in document</h3>
              <p className="mt-1 text-[13px] text-ink-3">
                These fields are excluded from scoring because the PDF does not print them, so there is nothing to check the
                model against. It is also why the UNSUPPORTED_DOCUMENT rule stays disabled: it is a P1 rule, off by design
                (OD-7), and on documents that print no &ldquo;Tax Invoice&rdquo; title it would hold invoices for something the PDF never says.
              </p>
              <ul className="mt-2 space-y-1 text-[13px] text-ink-2">
                {a.field_accuracy.filter((f) => f.unresolved > 0).map((f) => {
                  const reasons = [...new Set(f.unresolved_samples.map((u) => u.reason))];
                  return (
                    <li key={f.field}>
                      <span className="font-medium text-ink">{LABEL[f.field] ?? f.field}</span>, {f.unresolved} sample{f.unresolved === 1 ? "" : "s"}
                      {" "}({f.unresolved_samples.map((u) => u.test_id).join(", ")}): {reasons.map((r) => (r ?? "").replace(/^unresolved: /, "")).join(" / ")}
                    </li>);
                })}
              </ul>
            </div>
          )}

          <h2 className="section-title mt-10">Decisions, expected vs actual:{" "}
            <span className="tabular-nums" data-testid="acc-decisions-headline">{a.decision_accuracy.correct}/{a.decision_accuracy.total}</span> correct</h2>
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
            {" "}All checked fields: <span data-testid="acc-fields-headline">{a.field_totals.correct}/{a.field_totals.total}</span>
            {" "}exact ({pct(a.field_totals.correct, a.field_totals.total)}), {a.field_totals.unresolved} not stated in document.
            {" "}{a._meta.dataset[0].toUpperCase() + a._meta.dataset.slice(1)}.
          </p>
        </>
      )}
    </section>
  );
}
