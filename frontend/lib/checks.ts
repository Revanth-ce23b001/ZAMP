// Check-table presentation (DEC-020, proposed by implementation, pending review).
//
// Turns the evidence rows the backend records into five plain columns, each holding one kind of value:
//   Check        display name (one map, check-labels.json) plus the line / field / PO it is about
//   Requirement  what the rule demands, tolerance folded in ("≤ ₹62,000 + 1%", "≤ 10 units", "score ≥ 50")
//   Observed     one figure, optionally with a short sub-line (its composition, or a verification label)
//   Variance     a signed number, or an em-dash
//   Result       PASSED / FAILED / NEEDS REVIEW / NOT CHECKED (INFO for rows the rules record as informational)
// No cell ever carries serialized data (JSON, dicts, lists or key=value strings).
// Presentation only: every value comes from the stored evidence; no rule is re-evaluated here.

import labels from "@/lib/check-labels.json";
import type { Evidence, RunDetail } from "@/lib/client";

export type CheckRow = {
  key: string;
  stage: string;
  group: string;
  check: string;          // name, plus " — " subject where the check is about one line, field or PO
  requirement: string;
  observed: string;
  observedSub: string | null;
  variance: string;
  result: string;         // display word
  resultKey: string;      // the stored result, for styling
  code: string | null;
};

type Label = { name: string; group: string };
const NAMES = labels.names as Record<string, Label>;
const PATTERNS = labels.patterns as { match: string; name: string; group: string; line_group?: number }[];
export const GROUP_ORDER = labels.group_order as Record<string, string[]>;

export const RESULT_WORD: Record<string, string> = {
  PASSED: "PASSED", FAILED: "FAILED", NEEDS_REVIEW: "NEEDS REVIEW", NOT_CHECKED: "NOT CHECKED", INFO: "INFO",
};

const DASH = "—";

/** Display name, group and (for line-numbered ids) the line number, from the one label map. */
export function labelFor(check: string): { name: string; group: string; line: number | null } | null {
  const exact = NAMES[check];
  if (exact) return { ...exact, line: null };
  for (const p of PATTERNS) {
    const m = new RegExp(p.match).exec(check);
    if (m) return { name: p.name, group: p.group, line: p.line_group ? Number(m[p.line_group]) : null };
  }
  return null;
}

// ---------------------------------------------------------------- small text helpers

function text(v: unknown): string {
  if (v === null || v === undefined || v === "") return DASH;
  if (typeof v === "object") {
    // Never stringify structures into a cell; flatten to words.
    if (Array.isArray(v)) return v.map(text).join(", ");
    return Object.entries(v as Record<string, unknown>).map(([k, x]) => `${k.replace(/_/g, " ")} ${text(x)}`).join(", ");
  }
  return String(v);
}

const sym = (s: string) => s.replace(/<=/g, "≤").replace(/>=/g, "≥").replace(/(\d)-(\d)/g, "$1–$2");
const sentence = (s: string) => (s && s !== DASH ? s[0].toUpperCase() + s.slice(1) : s);

/** "₹2,20,000.40" -> 22000040 paise. Exact; used only to total two displayed parts and to sign a variance. */
function paiseOf(s: string): number | null {
  const m = /^(-?)₹([\d,]+)(?:\.(\d{1,2}))?$/.exec(s.trim());
  if (!m) return null;
  const whole = Number(m[2].replace(/,/g, ""));
  const frac = m[3] ? Number(m[3].padEnd(2, "0")) : 0;
  return (m[1] ? -1 : 1) * (whole * 100 + frac);
}

const INR = new Intl.NumberFormat("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
/** Paise -> "₹2,80,000" (".00" dropped, as the evidence strings are written). */
function inrOf(p: number): string {
  const s = INR.format(Math.abs(p) / 100).replace(/\.00$/, "");
  return (p < 0 ? "−₹" : "₹") + s;
}

/** A variance is shown only when it is a number; signed, with zero unsigned. Labels return null. */
function signed(v: unknown): string | null {
  if (v === null || v === undefined || v === "") return null;
  if (typeof v === "number") return v === 0 ? "0" : (v > 0 ? "+" : "−") + Math.abs(v);
  const s = String(v).trim();
  const money = paiseOf(s);
  if (money !== null) return money === 0 ? "₹0" : (money > 0 ? "+" : "") + inrOf(money);
  const m = /^([+-]?)(\d+(?:\.\d+)?)(%?)$/.exec(s);
  if (m) {
    const n = Number(m[2]);
    if (n === 0) return `${m[2]}${m[3]}`;
    return `${m[1] === "-" ? "−" : "+"}${m[2]}${m[3]}`;
  }
  return null;
}

const FIELD_LABEL: Record<string, string> = {
  invoice_number: "Invoice number", invoice_date: "Invoice date", vendor_name: "Vendor name", vendor_gstin: "Vendor GSTIN",
  total: "Total", po_reference: "PO reference", quantity: "quantity", unit_price: "unit price",
};

function lineName(d: RunDetail | null, n: number): string {
  const li = d?.extraction?.fields?.line_items?.[n - 1];
  const desc = li?.description?.value ?? li?.item_code?.value;
  return desc ? `Line ${n}, ${desc}` : `Line ${n}`;
}

/** What one row is about: an invoice line, a field, or (for candidate scores) a PO. */
function subjectOf(e: Evidence, d: RunDetail | null, lineFromId: number | null, candidatePo: string | null): string | null {
  if (candidatePo) return candidatePo;
  if (lineFromId !== null) return lineName(d, lineFromId);
  const src = e.source_fields?.[0] ?? "";
  const m = /^line_items\[(\d+)\](?:\.(\w+))?$/.exec(src);
  if (m) {
    const line = lineName(d, Number(m[1]));
    return e.check === "critical_field_confidence" && m[2] ? `${line} · ${FIELD_LABEL[m[2]] ?? m[2].replace(/_/g, " ")}` : line;
  }
  if (e.check === "critical_field_confidence" && src) return FIELD_LABEL[src] ?? src.replace(/_/g, " ");
  return null;
}

// ---------------------------------------------------------------- per-check columns

type Cols = { requirement: string; observed: string; sub: string | null; variance: string | null };

function columns(e: Evidence): Cols {
  const exp = e.expected === null || e.expected === undefined ? null : text(e.expected);
  const act = text(e.actual);
  const alw = e.allowed === null || e.allowed === undefined ? null : text(e.allowed);
  const label = signed(e.variance) === null && e.variance !== null && e.variance !== undefined && e.variance !== ""
    && typeof e.variance !== "object" ? String(e.variance) : null;
  const c = e.check;

  if (c.startsWith("arithmetic_")) {
    const tol = alw ? alw.replace(/^±?/, "± ") : null;
    return { requirement: exp ? `= ${exp}${tol ? ` ${tol}` : ""}` : DASH, observed: act, sub: null, variance: signed(e.variance) };
  }
  if (c === "critical_field_confidence") return { requirement: `confidence ${sym(exp ?? "")}`, observed: act, sub: null, variance: null };
  if (c === "unit_price") return { requirement: `≤ ${exp}${alw ? ` ${alw.replace(/^\+/, "+ ")}` : ""}`, observed: act, sub: null, variance: signed(e.variance) };
  if (c === "cumulative_quantity" || c === "cumulative_po_value") {
    const parts = /^(.+?) approved \+ (.+?) this invoice$/.exec(act);
    let observed = act, sub: string | null = null;
    if (parts) {
      sub = `${parts[1]} approved + ${parts[2]} this invoice`;
      const a = paiseOf(parts[1]), b = paiseOf(parts[2]);
      if (a !== null && b !== null) observed = inrOf(a + b);
      else if (!Number.isNaN(Number(parts[1])) && !Number.isNaN(Number(parts[2]))) {
        const total = Number(parts[1]) + Number(parts[2]);
        observed = `${total} unit${total === 1 ? "" : "s"}`;
      }
    }
    let requirement = sym(exp ?? DASH);
    if (c === "cumulative_quantity") {
      const over = alw ? Number(/^([\d.]+)/.exec(alw)?.[1] ?? 0) : 0;
      requirement = `${requirement}${over ? ` + ${over}` : ""} units`;
    } else {
      const m = /^<= (₹[\d,.]+) \(PO total (₹[\d,.]+) \+ allowed overrun (₹[\d,.]+)\)$/.exec(exp ?? "");
      if (m) requirement = `≤ ${m[1]} (PO total ${m[2]} + ${m[3]} overrun)`;
    }
    const v = signed(e.variance);
    return { requirement, observed, sub, variance: v };
  }
  if (c === "po_candidate_score") {
    const b = (e.variance && typeof e.variance === "object" ? e.variance : {}) as Record<string, unknown>;
    const paired = typeof b.paired === "string" ? b.paired.replace("/", " of ") : null;
    const sub = `= ${text(b.line_pairing)} line pairing${paired ? ` (${paired})` : ""} + ${text(b.value_fits)} value fit + ${text(b.date_window)} date window`;
    return { requirement: `score ${sym(exp ?? "")}`, observed: act, sub, variance: null };
  }
  if (c === "po_inferred") return { requirement: "PO reference printed on the invoice", observed: "None printed", sub: label ? sentence(label) : null, variance: null };
  if (c === "line_pairing") {
    if (e.result === "PASSED") return { requirement: "Pairs with a PO line", observed: exp ?? DASH, sub: alw ? `paired ${alw}` : null, variance: null };
    return { requirement: sentence(`${exp ?? "a PO line"}${alw ? ` (${alw})` : ""}`), observed: "No matching PO line", sub: null, variance: null };
  }
  if (c === "vendor_identified") {
    return { requirement: sentence(sym(exp && e.result !== "PASSED" ? `${exp}${alw ? ` (${alw})` : ""}` : "a vendor in the master")),
             observed: act, sub: e.result === "PASSED" ? (alw ? sentence(alw) : null) : (label ? sentence(label) : null), variance: null };
  }
  if (c === "gstin_matches_master" || c === "bank_details_match") {
    return { requirement: exp ? `= ${exp}${alw ? ` (${alw})` : ""}` : DASH, observed: act, sub: label ? sentence(label) : null, variance: null };
  }
  if (c === "duplicate_similar") {
    return { requirement: "Informational: does not affect the decision", observed: act.replace(/^Similar invoice exists: /, ""),
             sub: label ? sentence(label) : null, variance: null };
  }
  // Generic: expected (+ any tolerance not already in it) / actual / numeric variance; labels go to the sub-line.
  const requirement = exp ? sentence(sym(alw && alw !== exp ? `${exp} (${alw})` : exp)) : (alw ? sentence(sym(alw)) : DASH);
  return { requirement, observed: act, sub: label ? sentence(label) : null, variance: signed(e.variance) };
}

/** Pairs candidate-score rows with the decision's candidate list. Both are written from the same list in
 *  the same order (app/rules/po_match.py); a pair is used only if its score and breakdown also agree. */
function candidatePos(evidence: Evidence[], d: RunDetail | null): Map<Evidence, string> {
  const rows = evidence.filter((e) => e.check === "po_candidate_score");
  const cands = d?.decision?.po_candidates ?? [];
  const out = new Map<Evidence, string>();
  rows.forEach((e, i) => {
    const c = cands[i];
    if (c && c.score === e.actual && JSON.stringify(c.breakdown) === JSON.stringify(e.variance)) out.set(e, c.po_number);
  });
  return out;
}

export function checkRows(evidence: Evidence[], d: RunDetail | null): CheckRow[] {
  const pos = candidatePos(d?.decision?.evidence ?? evidence, d);
  return evidence.map((e, i) => {
    const l = labelFor(e.check);
    const name = l?.name ?? e.check;                         // unmapped ids are caught by test_check_labels.py
    const subject = subjectOf(e, d, l?.line ?? null, pos.get(e) ?? null);
    const cols = columns(e);
    return {
      key: `${e.stage}-${i}`, stage: e.stage, group: l?.group ?? "Other checks",
      check: subject ? `${name} — ${subject}` : name,
      requirement: cols.requirement, observed: cols.observed, observedSub: cols.sub,
      variance: cols.variance ?? DASH,
      result: RESULT_WORD[e.result] ?? e.result.replace(/_/g, " "), resultKey: e.result, code: e.reason_code,
    };
  });
}

/** Rows of one stage grouped under the stage's sub-headings, in the configured order. */
export function grouped(rows: CheckRow[], stage: string): { group: string; rows: CheckRow[] }[] {
  const order = GROUP_ORDER[stage] ?? [];
  const groups = [...new Set(rows.map((r) => r.group))].sort((a, b) => {
    const ia = order.indexOf(a), ib = order.indexOf(b);
    return (ia === -1 ? 99 : ia) - (ib === -1 ? 99 : ib);
  });
  return groups.map((g) => ({ group: g, rows: rows.filter((r) => r.group === g) }));
}
