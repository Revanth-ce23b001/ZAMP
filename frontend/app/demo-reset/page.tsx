"use client";

import { useState } from "react";

// Wireframe screen 11 / PRD FR-116.
// The typed-RESET input is a UX affordance only. This page enforces nothing: the backend
// re-checks the confirmation and applies both rate limits on every request.

const CONFIRM_PHRASE = "RESET";

type Outcome = { kind: "success" | "error"; text: string };

type ResetBody = {
  reset_id?: number;
  restored?: { vendors: number; purchase_orders: number; po_lines: number; ledger: number; consumption: number };
  error?: string;
  message?: string;
};

function describe(status: number, body: ResetBody): Outcome {
  if (status === 200 && body.restored) {
    const r = body.restored;
    return {
      kind: "success",
      text:
        `Demo data reset (reset #${body.reset_id}). Restored ${r.vendors} vendors, ${r.purchase_orders} POs, ` +
        `${r.po_lines} PO lines, ${r.ledger} ledger entries and ${r.consumption} consumption rows.`,
    };
  }
  const detail = body.message ? ` ${body.message}` : "";
  switch (status) {
    case 400:
      return { kind: "error", text: `Not reset: the confirmation was not accepted.${detail}` };
    case 401:
      return { kind: "error", text: `Not reset: the server rejected the request (reset token missing or wrong).${detail}` };
    case 429:
      return { kind: "error", text: `Not reset: rate limited.${detail}` };
    case 500:
      return { kind: "error", text: `Reset failed. No data was changed.${detail}` };
    default:
      return { kind: "error", text: `Not reset (HTTP ${status}).${detail}` };
  }
}

export default function DemoResetPage() {
  const [typed, setTyped] = useState("");
  const [busy, setBusy] = useState(false);
  const [outcome, setOutcome] = useState<Outcome | null>(null);

  async function onReset() {
    setBusy(true);
    setOutcome(null);
    try {
      const res = await fetch("/api/demo/reset", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ confirm: typed, requester_label: "web-ui" }),
      });
      let body: ResetBody = {};
      try {
        body = await res.json();
      } catch {
        body = {};
      }
      setOutcome(describe(res.status, body));
      if (res.ok) setTyped("");
    } catch {
      setOutcome({ kind: "error", text: "Could not reach the server. No data was changed." });
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="mx-auto max-w-xl rounded border border-gray-200 bg-white p-8 text-center">
      <h1 className="text-2xl font-semibold">Reset Demo Data</h1>
      <p className="mt-3 text-sm text-gray-600">
        Restore the application to the original synthetic demo data (fixtures-v1). This clears runs and
        related data, and reloads the seeded vendors, POs and ledger.
      </p>

      <div className="mt-5 rounded border border-amber-300 bg-amber-50 p-3 text-left text-sm text-amber-900">
        This action uses synthetic data only. It is intended for the interview demo and can be used by anyone.
      </div>

      <label htmlFor="confirm" className="mt-6 block text-left text-sm text-gray-700">
        To confirm, type <strong>RESET</strong> in the box below.
      </label>
      <input
        id="confirm"
        value={typed}
        onChange={(e) => setTyped(e.target.value)}
        placeholder="RESET"
        autoComplete="off"
        className="mt-2 w-full rounded border border-gray-300 px-3 py-2 text-sm"
      />
      <button
        type="button"
        onClick={onReset}
        disabled={typed !== CONFIRM_PHRASE || busy}
        className="mt-4 w-full rounded bg-blue-600 px-4 py-2 text-sm font-semibold text-white disabled:cursor-not-allowed disabled:bg-gray-300"
      >
        {busy ? "Resetting…" : "Reset Demo Data"}
      </button>

      {outcome && (
        <div
          role="status"
          className={
            "mt-4 rounded p-3 text-left text-sm " +
            (outcome.kind === "success" ? "bg-green-50 text-green-800" : "bg-red-50 text-red-800")
          }
        >
          {outcome.text}
        </div>
      )}

      <p className="mt-6 text-xs text-gray-500">
        Rate limits: 1 successful reset / 30 seconds; 5 rejected attempts per IP / 15 minutes
      </p>
    </section>
  );
}
