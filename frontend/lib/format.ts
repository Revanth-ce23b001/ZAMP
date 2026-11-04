// Display helpers. Amounts in rupees with Indian grouping (PRD §10): ₹1,13,850.00.

const INR = new Intl.NumberFormat("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

export function rupees(value: unknown): string {
  if (value === null || value === undefined || value === "") return "—";
  const n = typeof value === "number" ? value : Number(value);
  if (Number.isNaN(n)) return String(value);
  return (n < 0 ? "-₹" : "₹") + INR.format(Math.abs(n));
}

export function show(value: unknown): string {
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

export function qty(n: number | null | undefined): string {
  if (n === null || n === undefined) return "—";
  return Number.isInteger(n) ? String(n) : String(n);
}

export function when(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return d.toLocaleString("en-IN", { day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" });
}

export const DECIDED_BY: Record<string, string> = {
  rules: "Rules",
  rules_after_review: "Rules after reviewer PO confirmation",
  reviewer: "Reviewer override",
};

export function seconds(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) return "—";
  return ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(1)} s`;
}

// Inbox status labels (FR-113).
export const STATUS_LABEL: Record<string, string> = {
  APPROVE: "Approved", HOLD: "On hold", REJECT: "Rejected", ERROR: "Error", QUEUED: "Queued", PROCESSING: "Processing",
};
