"use client";

// Assumptions and Policy (FR-117, PRD §10.7). Everything is read from GET /api/policy, which serves
// policy.yaml as loaded by the backend, so this page cannot drift from the rules actually applied.

import { useEffect, useState } from "react";

import { getPolicy, type ApiError, type Policy, type PolicyEntry } from "@/lib/client";
import { MIN_STAGE_DISPLAY_MS } from "@/lib/display";

const humanise = (s: string) => s.replace(/_/g, " ");

// Readable rendering of a policy value: scalars as-is, lists comma-joined, maps as "key: value" lines.
function Value({ v }: { v: unknown }) {
  if (v === null || v === undefined) return <span className="text-gray-400">none</span>;
  if (typeof v === "boolean") return <>{v ? "yes" : "no"}</>;
  if (Array.isArray(v)) return <>{v.map((x) => (typeof x === "object" ? JSON.stringify(x) : String(x))).join(", ")}</>;
  if (typeof v === "object") {
    return (
      <ul className="space-y-0.5">
        {Object.entries(v as Record<string, unknown>).map(([k, x]) => (
          <li key={k}><span className="text-gray-500">{humanise(k)}:</span> <Value v={x} /></li>))}
      </ul>
    );
  }
  return <>{String(v)}</>;
}

// Extra keys some entries carry next to value (e.g. A-05 allowed_range, A-10 lower_limit).
const BASE_KEYS = new Set(["id", "name", "value", "unit", "label", "meaning", "source"]);
const extras = (e: PolicyEntry) => Object.entries(e).filter(([k]) => !BASE_KEYS.has(k));

const STAGE_NAME: Record<string, string> = {
  VALIDATE: "Validate", VENDOR_BANK: "Vendor & Bank", DUPLICATE: "Duplicate", PO_MATCH: "PO Match",
};
const SEVERITY: Record<string, string> = { REJECT: "bg-red-100 text-red-800", HOLD: "bg-amber-100 text-amber-800" };

function H2({ children, id }: { children: React.ReactNode; id: string }) {
  return <h2 id={id} className="mt-10 scroll-mt-20 text-lg font-semibold">{children}</h2>;
}

export default function PolicyPage() {
  const [p, setP] = useState<Policy | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    getPolicy().then(setP).catch((e: ApiError) => setError(e.message ?? "Policy could not be loaded"));
  }, []);

  return (
    <section className="max-w-5xl">
      <h1 className="text-2xl font-semibold">Assumptions and Policy</h1>
      <p className="mt-2 text-gray-600">
        Every threshold below is a prototype assumption, labelled <b>[PA]</b>: a value chosen for this prototype, not a
        Zamp policy and not a universal accounting rule. All of them live in <code>policy.yaml</code>.
      </p>

      {error && (
        <div role="alert" className="mt-6 rounded border border-red-200 bg-red-50 p-4 text-sm text-red-800">
          Policy could not be loaded ({error}). Check that the backend is running.
        </div>
      )}
      {!p && !error && <div className="mt-6 h-64 animate-pulse rounded bg-gray-100" />}

      {p && (
        <>
          <dl className="mt-6 grid gap-4 rounded border border-gray-200 bg-white p-4 text-sm md:grid-cols-2">
            <div>
              <dt className="text-gray-500">Policy version (SHA-256 of policy.yaml)</dt>
              <dd className="mt-1 break-all font-mono" data-testid="policy-version">{p.policy_version}</dd>
              <dd className="mt-1 text-xs text-gray-500">Every decision records the version it was made under.</dd>
            </div>
            <div data-testid="scope">
              <dt className="text-gray-500">Scope</dt>
              <dd className="mt-1">
                <b>{p.scope.matching}</b> (invoice against PO; goods receipt is not checked) · <b>{p.scope.currency}</b> only
                · <b>{p.scope.tax_regime}</b> only · <b>Sequential</b> processing, one invoice at a time
                ({humanise(p.scope.processing)}).
              </dd>
            </div>
          </dl>

          <nav className="mt-4 flex flex-wrap gap-3 text-sm text-blue-700">
            {[["assumptions", "Assumptions"], ["other", "Other values"], ["codes", "Reason codes"],
              ["not-verified", "What this does not verify"], ["hosting", "Demo hosting notes"]].map(([id, label]) => (
              <a key={id} href={`#${id}`} className="underline">{label}</a>))}
          </nav>

          <H2 id="assumptions">Assumptions A-01 to A-{String(p.assumptions.length).padStart(2, "0")}</H2>
          <div className="mt-2 overflow-x-auto rounded border border-gray-200 bg-white">
            <table className="w-full text-sm" data-testid="assumptions-table">
              <thead className="bg-gray-50 text-left text-xs uppercase text-gray-500">
                <tr><th className="px-3 py-2">ID</th><th className="px-3 py-2">Meaning</th><th className="px-3 py-2">Value</th>
                  <th className="px-3 py-2">Unit</th><th className="px-3 py-2">Source</th></tr>
              </thead>
              <tbody>
                {p.assumptions.map((a) => (
                  <tr key={a.id} data-testid="policy-key" data-key={a.id} className="border-t border-gray-100 align-top">
                    <td className="whitespace-nowrap px-3 py-2">
                      <div className="font-mono">{a.id}</div>
                      <span className="rounded bg-violet-100 px-1.5 text-xs text-violet-800">{a.label}</span>
                    </td>
                    <td className="px-3 py-2">
                      <div>{a.meaning}</div>
                      <div className="font-mono text-xs text-gray-400">{a.name}</div>
                    </td>
                    <td className="px-3 py-2">
                      <Value v={a.value} />
                      {extras(a).map(([k, v]) => (
                        <div key={k} className="text-xs"><span className="text-gray-500">{humanise(k)}:</span> <Value v={v} /></div>))}
                    </td>
                    <td className="px-3 py-2 text-gray-600">{a.unit}</td>
                    <td className="px-3 py-2 text-xs text-gray-500">{a.source}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <H2 id="other">Other [PA] values stated in the PRD</H2>
          <div className="mt-2 overflow-x-auto rounded border border-gray-200 bg-white">
            <table className="w-full text-sm">
              <thead className="bg-gray-50 text-left text-xs uppercase text-gray-500">
                <tr><th className="px-3 py-2">Name</th><th className="px-3 py-2">Value</th><th className="px-3 py-2">Unit</th>
                  <th className="px-3 py-2">Source</th></tr>
              </thead>
              <tbody>
                {p.other_assumptions.map((a) => (
                  <tr key={a.name} data-testid="policy-key" data-key={a.name} className="border-t border-gray-100 align-top">
                    <td className="px-3 py-2">
                      <span className="capitalize">{humanise(a.name)}</span>{" "}
                      <span className="rounded bg-violet-100 px-1.5 text-xs text-violet-800">{a.label}</span>
                    </td>
                    <td className="px-3 py-2">
                      <Value v={a.value} />
                      {extras(a).map(([k, v]) => (
                        <div key={k} className="text-xs"><span className="text-gray-500">{humanise(k)}:</span> <Value v={v} /></div>))}
                    </td>
                    <td className="px-3 py-2 text-gray-600">{a.unit}</td>
                    <td className="px-3 py-2 text-xs text-gray-500">{a.source}</td>
                  </tr>
                ))}
                {Object.entries(p.p1_rules).map(([k, on]) => (
                  <tr key={k} data-testid="policy-key" data-key={`p1_rules.${k}`} className="border-t border-gray-100">
                    <td className="px-3 py-2"><span className="capitalize">P1 rule: {humanise(k)}</span></td>
                    <td className="px-3 py-2">{on ? "on" : "off"}</td>
                    <td className="px-3 py-2 text-gray-600">switch</td>
                    <td className="px-3 py-2 text-xs text-gray-500">PRD §15, OD-7 (off until P0 is stable)</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <H2 id="codes">Reason codes ({p.reason_codes.length})</H2>
          <div className="mt-2 overflow-x-auto rounded border border-gray-200 bg-white">
            <table className="w-full text-sm" data-testid="codes-table">
              <thead className="bg-gray-50 text-left text-xs uppercase text-gray-500">
                <tr><th className="px-3 py-2">Code</th><th className="px-3 py-2">Severity</th><th className="px-3 py-2">Stage</th>
                  <th className="px-3 py-2">What the reviewer is told to do</th></tr>
              </thead>
              <tbody>
                {p.reason_codes.map((c) => (
                  <tr key={c.code} data-testid="reason-code" data-code={c.code} className="border-t border-gray-100 align-top">
                    <td className="px-3 py-2 font-mono text-xs">{c.code}</td>
                    <td className="px-3 py-2"><span className={"rounded px-2 py-0.5 text-xs font-semibold " + (SEVERITY[c.severity] ?? "bg-gray-100")}>{c.severity}</span></td>
                    <td className="whitespace-nowrap px-3 py-2">{c.stage} · {STAGE_NAME[c.stage_key] ?? humanise(c.stage_key)}</td>
                    <td className="px-3 py-2 text-gray-600">{c.next_action.replace("{contact}", "the trusted contact")}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <H2 id="not-verified">What this prototype does not verify (PRD §14.2)</H2>
          <div className="mt-2 overflow-x-auto rounded border border-gray-200 bg-white">
            <table className="w-full text-sm" data-testid="not-verified">
              <thead className="bg-gray-50 text-left text-xs uppercase text-gray-500">
                <tr><th className="px-3 py-2">Not verified</th><th className="px-3 py-2">What it does instead</th></tr>
              </thead>
              <tbody>
                {p.does_not_verify.map((d) => (
                  <tr key={d.not_verified} className="border-t border-gray-100 align-top">
                    <td className="px-3 py-2 font-medium">{d.not_verified}</td><td className="px-3 py-2 text-gray-600">{d.instead}</td>
                  </tr>))}
              </tbody>
            </table>
          </div>

          <H2 id="hosting">Demo hosting notes</H2>
          <div className="mt-2 space-y-3 rounded border border-gray-200 bg-white p-4 text-sm text-gray-700">
            <p data-testid="reset-history-note">
              <b>Reset history is not kept across restarts (DEC-016).</b> Reset history (<code>demo_resets</code>) is
              append-only: the database blocks edits and deletions, and a demo reset never clears it. The demo backend
              runs on a free hosting tier whose disk is temporary. The server sleeps after about 15 minutes without traffic, and
              each wake-up, restart or redeploy starts from a fresh database: runs and reset history are cleared, and seed data and the shipped extraction cache are reloaded. The
              append-only guarantee therefore holds within one server lifetime, not across restarts. Keeping it
              permanently would need a paid persistent disk, which is outside this prototype.
            </p>
            <p data-testid="cold-start-note">
              <b>Cold start.</b> The free tier puts the backend to sleep after about 15 minutes without traffic. The first
              request after that waits for it to wake up (measured at 33 to 43 seconds); later requests are normal.
            </p>
            <p data-testid="stepper-note">
              <b>Live screen pacing.</b> On the live processing screen each stage row stays on <i>Running</i> for at
              least {MIN_STAGE_DISPLAY_MS} ms before showing its result, so fast stages stay visible. This is display
              only: the backend adds no delay, and recorded stage durations and timestamps are unchanged.
            </p>
          </div>
        </>
      )}
    </section>
  );
}
