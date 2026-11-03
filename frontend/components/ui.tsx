// Shared visual vocabulary (PRD §10): APPROVE green, HOLD amber, REJECT red; check and stage chips
// Passed green, Failed red, Needs Review amber, Not Checked grey.

export const DECISION_STYLE: Record<string, string> = {
  APPROVE: "bg-green-600 text-white",
  HOLD: "bg-amber-500 text-white",
  REJECT: "bg-red-600 text-white",
  ERROR: "bg-red-700 text-white",
};

export function DecisionBadge({ status, large = false }: { status: string; large?: boolean }) {
  return (
    <span data-testid="decision-status"
          className={(large ? "px-4 py-1.5 text-lg " : "px-2 py-0.5 text-xs ") + "rounded font-bold " +
                     (DECISION_STYLE[status] ?? "bg-gray-500 text-white")}>
      {status}
    </span>
  );
}

const RESULT_STYLE: Record<string, [string, string]> = {
  PASSED: ["bg-green-100 text-green-800", "Passed"],
  FAILED: ["bg-red-100 text-red-800", "Failed"],
  NEEDS_REVIEW: ["bg-amber-100 text-amber-800", "Needs Review"],
  NOT_CHECKED: ["bg-gray-200 text-gray-700", "Not Checked"],
  INFO: ["bg-blue-50 text-blue-800", "Info"],
};

export function ResultChip({ result }: { result: string | null | undefined }) {
  const [cls, label] = RESULT_STYLE[result ?? ""] ?? ["bg-gray-100 text-gray-700", result ?? "—"];
  return <span className={"whitespace-nowrap rounded px-2 py-0.5 text-xs " + cls}>{label}</span>;
}

export function ConfidenceChip({ value }: { value: number | null | undefined }) {
  if (value === null || value === undefined) return <span className="text-xs text-gray-400">—</span>;
  const low = value < 0.85; // FR-013: amber below the 0.85 critical threshold (A-17)
  return (
    <span data-testid={low ? "confidence-low" : "confidence-ok"}
          className={"rounded px-1.5 py-0.5 text-xs " + (low ? "bg-amber-100 text-amber-800" : "bg-green-100 text-green-800")}>
      {value.toFixed(2)}
    </span>
  );
}

export function CodeChip({ code }: { code: string }) {
  return <span className="rounded bg-gray-100 px-2 py-0.5 font-mono text-xs text-gray-800">{code}</span>;
}
