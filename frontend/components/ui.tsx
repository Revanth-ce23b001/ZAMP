// Shared visual vocabulary (PRD §10): APPROVE green, HOLD amber, REJECT red; check and stage chips
// Passed green, Failed red, Needs Review amber, Not Checked grey. Every status is a dot-plus-word pill.

export const DECISION_STYLE: Record<string, string> = {
  APPROVE: "s-ok",
  HOLD: "s-hold",
  REJECT: "s-bad",
  ERROR: "s-bad",
  QUEUED: "s-run",
  PROCESSING: "s-run",
};

export function DecisionBadge({ status, large = false }: { status: string; large?: boolean }) {
  return (
    <span data-testid="decision-status" className={"status " + (large ? "status-lg " : "") + (DECISION_STYLE[status] ?? "s-nc")}>
      <span className="dot" aria-hidden />{status}
    </span>
  );
}

const RESULT_STYLE: Record<string, [string, string]> = {
  PASSED: ["s-ok", "Passed"],
  FAILED: ["s-bad", "Failed"],
  NEEDS_REVIEW: ["s-hold", "Needs Review"],
  NOT_CHECKED: ["s-nc", "Not Checked"],
  INFO: ["s-run", "Info"],
};

export function ResultChip({ result }: { result: string | null | undefined }) {
  const [cls, label] = RESULT_STYLE[result ?? ""] ?? ["s-nc", result ?? "—"];
  return <span className={"status " + cls}><span className="dot" aria-hidden />{label}</span>;
}

export function ConfidenceChip({ value }: { value: number | null | undefined }) {
  if (value === null || value === undefined) return <span className="text-xs text-ink-4">—</span>;
  const low = value < 0.85; // FR-013: amber below the 0.85 critical threshold (A-17)
  return (
    <span data-testid={low ? "confidence-low" : "confidence-ok"}
          className={"rounded-chip px-1.5 py-0.5 text-xs font-medium tabular-nums " + (low ? "s-hold" : "s-ok")}>
      {value.toFixed(2)}
    </span>
  );
}

export function CodeChip({ code }: { code: string }) {
  return <span className="code">{code}</span>;
}
