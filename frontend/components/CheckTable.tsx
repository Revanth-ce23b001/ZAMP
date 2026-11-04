// Shared check table (DEC-020): one component for every stage, on the decision screen's Evidence tab
// and in the live screen's stage panels. Five columns, one kind of value per column, grouped under
// sub-headings. Below 1024px each row stacks into a labelled block (one DOM, styled by .checks).

import { CodeChip } from "@/components/ui";
import { grouped, type CheckRow } from "@/lib/checks";

const RESULT_CLASS: Record<string, string> = {
  PASSED: "s-ok", FAILED: "s-bad", NEEDS_REVIEW: "s-hold", NOT_CHECKED: "s-nc", INFO: "s-run",
};

export default function CheckTable({ rows, stage, rowTestId }: { rows: CheckRow[]; stage: string; rowTestId?: string }) {
  return (
    <table className="checks" data-testid="check-table" data-stage={stage}>
      <colgroup>
        {/* Variance and Result are sized for their longest values ("+₹19,000", "NEEDS REVIEW"); the three text columns share the rest. */}
        <col /><col /><col /><col className="w-[88px]" /><col className="w-[140px]" />
      </colgroup>
      <thead>
        <tr><th>Check</th><th>Requirement</th><th>Observed</th><th className="num">Variance</th><th>Result</th></tr>
      </thead>
      {grouped(rows, stage).map(({ group, rows: rs }) => (
        <tbody key={group}>
          <tr className="checks-group"><th colSpan={5}>{group}</th></tr>
          {rs.map((r) => (
            <tr key={r.key} data-testid={rowTestId ?? "check-row"} className="checks-row" data-result={r.resultKey}>
              <td data-label="Check" className="font-medium">{r.check}</td>
              <td data-label="Requirement">{r.requirement}</td>
              <td data-label="Observed">
                <span className="block">{r.observed}</span>
                {r.observedSub && <span className="block text-[12px] leading-snug text-ink-3" data-testid="observed-sub">{r.observedSub}</span>}
              </td>
              <td data-label="Variance" className="num">{r.variance}</td>
              <td data-label="Result">
                <span className={"status " + (RESULT_CLASS[r.resultKey] ?? "s-nc")}><span className="dot" aria-hidden />{r.result}</span>
                {r.code && <span className="mt-1 block"><CodeChip code={r.code} /></span>}
              </td>
            </tr>
          ))}
        </tbody>
      ))}
    </table>
  );
}
