"use client";

// Minimal review panel for a HOLD run (FR-100..FR-106, PRD §10.6). The backend enforces every rule
// (reason length, code acknowledgement, bank verification, commit recheck); the disabled states
// here are only affordances. The full decision details screen is Day 5.

import { useState } from "react";

import { confirmPo, overrideRun, type ApiError, type ReviewResult, type RunDetail } from "@/lib/client";

const BANK = "BANK_DETAILS_MISMATCH";
const MIN_REASON = 10; // UI hint only; the backend enforces policy A-29

export default function ReviewPanel({ detail, onDone }: { detail: RunDetail; onDone: (r: ReviewResult) => void }) {
  const decision = detail.decision!;
  const codes = decision.reason_codes;
  const candidates = decision.po_candidates ?? [];
  const canConfirm = candidates.length > 0 && codes.some((c) => c === "PO_INFERRED" || c === "PO_NOT_FOUND");
  const bankCase = codes.includes(BANK);

  const [name, setName] = useState("");
  const [reason, setReason] = useState("");
  const [acked, setAcked] = useState<string[]>([]);
  const [verifiedWith, setVerifiedWith] = useState("");
  const [channel, setChannel] = useState("");
  const [outcome, setOutcome] = useState("");
  const [po, setPo] = useState(candidates[0]?.po_number ?? "");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<{ ok: boolean; text: string } | null>(null);
  const [left, setLeft] = useState(false);

  const who = name.trim().length > 0 && reason.trim().length >= MIN_REASON;
  const allAcked = codes.every((c) => acked.includes(c));
  const bankFilled = !bankCase || (verifiedWith.trim() && channel && outcome);
  const canApprove = who && allAcked && bankFilled && (!bankCase || outcome === "verified");
  const canRecordAttempt = who && allAcked && bankCase && verifiedWith.trim() && channel && outcome === "not verified";

  async function act(fn: () => Promise<ReviewResult>) {
    setBusy(true);
    setResult(null);
    try {
      const r = await fn();
      setResult({ ok: r.commit_result !== "blocked", text: r.message });
      onDone(r);
    } catch (e) {
      setResult({ ok: false, text: (e as ApiError).message ?? "Request failed" });
    } finally {
      setBusy(false);
    }
  }

  const approveBody = () => ({
    action: "approve" as const, reviewer_name: name, reason, codes_overridden: acked,
    ...(bankCase ? { bank_verification: { verified_with: verifiedWith, channel, outcome } } : {}),
  });

  if (left) {
    return <div className="mt-4 rounded border border-gray-200 bg-white p-3 text-sm text-gray-600">Left on HOLD.</div>;
  }

  return (
    <div data-testid="review-panel" className="mt-4 rounded border border-amber-300 bg-white p-4 text-sm">
      <h2 className="font-semibold">Review this HOLD</h2>

      {bankCase && detail.trusted_contact && (
        <div className="mt-3 rounded border border-red-300 bg-red-50 p-3 text-red-900">
          Bank details differ from vendor master. Verify with {detail.trusted_contact.name}, {detail.trusted_contact.phone}
          {" "}(vendor master). Do not use contact details printed on this invoice.
        </div>
      )}

      <fieldset className="mt-3">
        <legend className="font-medium">Codes to resolve</legend>
        {codes.map((c, i) => (
          <label key={c} className="mt-1 flex items-start gap-2">
            <input type="checkbox" checked={acked.includes(c)} data-testid={`ack-${c}`}
                   onChange={(e) => setAcked((a) => (e.target.checked ? [...a, c] : a.filter((x) => x !== c)))} />
            <span><span className="font-mono text-xs">{c}</span> — {decision.summary[i]}</span>
          </label>
        ))}
      </fieldset>

      <div className="mt-3 grid gap-2 md:grid-cols-2">
        <input aria-label="Reviewer name" placeholder="Reviewer name" value={name} onChange={(e) => setName(e.target.value)}
               className="rounded border border-gray-300 px-2 py-1" />
        <input aria-label="Reason" placeholder={`Reason (at least ${MIN_REASON} characters)`} value={reason}
               onChange={(e) => setReason(e.target.value)} className="rounded border border-gray-300 px-2 py-1" />
      </div>

      {bankCase && (
        <div className="mt-3 grid gap-2 md:grid-cols-3">
          <input aria-label="Verified with" placeholder="Verified with (contact on record)" value={verifiedWith}
                 onChange={(e) => setVerifiedWith(e.target.value)} className="rounded border border-gray-300 px-2 py-1" />
          <select aria-label="Channel" value={channel} onChange={(e) => setChannel(e.target.value)}
                  className="rounded border border-gray-300 px-2 py-1">
            <option value="">Channel…</option>
            <option value="phone on record">phone on record</option>
            <option value="email on record">email on record</option>
          </select>
          <select aria-label="Outcome" value={outcome} onChange={(e) => setOutcome(e.target.value)}
                  className="rounded border border-gray-300 px-2 py-1">
            <option value="">Outcome…</option>
            <option value="verified">verified</option>
            <option value="not verified">not verified</option>
          </select>
        </div>
      )}

      <div className="mt-4 flex flex-wrap gap-2">
        <button type="button" disabled={!canApprove || busy} onClick={() => act(() => overrideRun(detail.run_id, approveBody()))}
                className="rounded bg-green-600 px-3 py-1.5 font-semibold text-white disabled:bg-gray-300">Approve (override)</button>
        {canRecordAttempt && (
          <button type="button" disabled={busy} onClick={() => act(() => overrideRun(detail.run_id, approveBody()))}
                  className="rounded border border-gray-400 px-3 py-1.5">Record attempt (stays on HOLD)</button>
        )}
        <button type="button" disabled={!who || busy}
                onClick={() => act(() => overrideRun(detail.run_id, { action: "reject", reviewer_name: name, reason,
                                                                         codes_overridden: codes }))}
                className="rounded bg-red-600 px-3 py-1.5 font-semibold text-white disabled:bg-gray-300">Reject</button>
        <button type="button" disabled={busy} onClick={() => setLeft(true)}
                className="rounded border border-gray-300 px-3 py-1.5">Leave on HOLD</button>
      </div>

      {canConfirm && (
        <fieldset className="mt-4 border-t border-gray-100 pt-3">
          <legend className="font-medium">Confirm PO</legend>
          {candidates.map((c) => (
            <label key={c.po_number} className="mr-4 inline-flex items-center gap-1">
              <input type="radio" name="po" value={c.po_number} checked={po === c.po_number} onChange={() => setPo(c.po_number)} />
              {c.po_number} (score {c.score}{c.plausible ? ", plausible" : ""})
            </label>
          ))}
          <button type="button" disabled={!who || !po || busy}
                  onClick={() => act(() => confirmPo(detail.run_id, { po_number: po, reviewer_name: name, reason }))}
                  className="ml-2 rounded bg-blue-600 px-3 py-1.5 font-semibold text-white disabled:bg-gray-300">Confirm PO</button>
        </fieldset>
      )}

      {result && (
        <div role="status" data-testid="review-result"
             className={"mt-3 rounded p-2 " + (result.ok ? "bg-green-50 text-green-800" : "bg-red-50 text-red-800")}>
          {result.text}
        </div>
      )}
    </div>
  );
}
