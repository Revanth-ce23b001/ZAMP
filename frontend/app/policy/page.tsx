"use client";

// Assumptions and Policy (FR-117, PRD §10.7). Everything is read from GET /api/policy, which serves
// policy.yaml as loaded by the backend, so this page cannot drift from the rules actually applied.

import { useEffect, useState } from "react";

import { getPolicy, type ApiError, type Policy, type PolicyEntry } from "@/lib/client";
import { MIN_STAGE_DISPLAY_MS } from "@/lib/display";

const humanise = (s: string) => s.replace(/_/g, " ");

// Readable rendering of a policy value: scalars as-is, lists comma-joined, maps as "key: value" lines.
function Value({ v }: { v: unknown }) {
  if (v === null || v === undefined) return <span className="text-ink-3">none</span>;
  if (typeof v === "boolean") return <>{v ? "yes" : "no"}</>;
  if (Array.isArray(v)) return <>{v.map((x) => (typeof x === "object" ? JSON.stringify(x) : String(x))).join(", ")}</>;
  if (typeof v === "object") {
    return (
      <ul className="space-y-0.5">
        {Object.entries(v as Record<string, unknown>).map(([k, x]) => (
          <li key={k}><span className="text-ink-3">{humanise(k)}:</span> <Value v={x} /></li>))}
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
const SEVERITY: Record<string, string> = { REJECT: "s-bad", HOLD: "s-hold" };

function H2({ children, id }: { children: React.ReactNode; id: string }) {
  return <h2 id={id} className="section-title mt-10 scroll-mt-20">{children}</h2>;
}

export default function PolicyPage() {
  const [p, setP] = useState<Policy | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    getPolicy().then(setP).catch((e: ApiError) => setError(e.message ?? "Policy could not be loaded"));
  }, []);

  return (
    <section className="max-w-5xl">
      <h1 className="page-title">Assumptions and Policy</h1>
      <p className="mt-2 text-ink-2">
        Every threshold below is a prototype assumption, labelled <b>[PA]</b>: a value chosen for this prototype, not a
        Zamp policy and not a universal accounting rule. All of them live in <code>policy.yaml</code>.
      </p>

      {error && (
        <div role="alert" className="notice notice-bad mt-6">
          Policy could not be loaded ({error}). Check that the backend is running.
        </div>
      )}
      {!p && !error && <div className="mt-6 h-64 animate-pulse rounded-card bg-hover" />}

      {p && (
        <>
          <dl className="card mt-6 grid gap-6 md:grid-cols-2">
            <div>
              <dt className="micro">Policy version (SHA-256 of policy.yaml)</dt>
              <dd className="mt-2 break-all font-mono text-[13px]" data-testid="policy-version">{p.policy_version}</dd>
              <dd className="mt-1 text-[13px] text-ink-3">Every decision records the version it was made under.</dd>
            </div>
            <div data-testid="scope">
              <dt className="micro">Scope</dt>
              <dd className="mt-2">
                <b>{p.scope.matching}</b> (invoice against PO; goods receipt is not checked) · <b>{p.scope.currency}</b> only
                · <b>{p.scope.tax_regime}</b> only · <b>Sequential</b> processing, one invoice at a time
                ({humanise(p.scope.processing)}).
              </dd>
            </div>
          </dl>

          <nav className="mt-4 flex flex-wrap gap-x-5 gap-y-2 text-sm">
            {[["assumptions", "Assumptions"], ["other", "Other values"], ["codes", "Reason codes"],
              ["not-verified", "What this does not verify"], ["hosting", "Demo hosting notes"]].map(([id, label]) => (
              <a key={id} href={`#${id}`} className="font-medium underline">{label}</a>))}
          </nav>

          <H2 id="assumptions">Assumptions A-01 to A-{String(p.assumptions.length).padStart(2, "0")}</H2>
          <div className="card-flush mt-3 overflow-x-auto">
            <table className="tbl" data-testid="assumptions-table">
              <thead>
                <tr><th>ID</th><th>Meaning</th><th>Value</th>
                  <th>Unit</th><th>Source</th></tr>
              </thead>
              <tbody>
                {p.assumptions.map((a) => (
                  <tr key={a.id} data-testid="policy-key" data-key={a.id}>
                    <td className="whitespace-nowrap">
                      <div className="font-mono">{a.id}</div>
                      <span className="code !text-ink-3">{a.label}</span>
                    </td>
                    <td >
                      <div>{a.meaning}</div>
                      <div className="font-mono text-xs text-ink-3">{a.name}</div>
                    </td>
                    <td >
                      <Value v={a.value} />
                      {extras(a).map(([k, v]) => (
                        <div key={k} className="text-xs"><span className="text-ink-3">{humanise(k)}:</span> <Value v={v} /></div>))}
                    </td>
                    <td className="text-ink-3">{a.unit}</td>
                    <td className="text-xs text-ink-3">{a.source}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <H2 id="other">Other [PA] values stated in the PRD</H2>
          <div className="card-flush mt-3 overflow-x-auto">
            <table className="tbl">
              <thead>
                <tr><th>Name</th><th>Value</th><th>Unit</th>
                  <th>Source</th></tr>
              </thead>
              <tbody>
                {p.other_assumptions.map((a) => (
                  <tr key={a.name} data-testid="policy-key" data-key={a.name}>
                    <td >
                      <span className="capitalize">{humanise(a.name)}</span>{" "}
                      <span className="code !text-ink-3">{a.label}</span>
                    </td>
                    <td >
                      <Value v={a.value} />
                      {extras(a).map(([k, v]) => (
                        <div key={k} className="text-xs"><span className="text-ink-3">{humanise(k)}:</span> <Value v={v} /></div>))}
                    </td>
                    <td className="text-ink-3">{a.unit}</td>
                    <td className="text-xs text-ink-3">{a.source}</td>
                  </tr>
                ))}
                {Object.entries(p.p1_rules).map(([k, on]) => (
                  <tr key={k} data-testid="policy-key" data-key={`p1_rules.${k}`}>
                    <td ><span className="capitalize">P1 rule: {humanise(k)}</span></td>
                    <td >{on ? "on" : "off"}</td>
                    <td className="text-ink-3">switch</td>
                    <td className="text-xs text-ink-3">PRD §15, OD-7 (off until P0 is stable)</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <H2 id="codes">Reason codes ({p.reason_codes.length})</H2>
          <div className="card-flush mt-3 overflow-x-auto">
            <table className="tbl" data-testid="codes-table">
              <thead>
                <tr><th>Code</th><th>Severity</th><th>Stage</th>
                  <th>What the reviewer is told to do</th></tr>
              </thead>
              <tbody>
                {p.reason_codes.map((c) => (
                  <tr key={c.code} data-testid="reason-code" data-code={c.code}>
                    <td><span className="code">{c.code}</span></td>
                    <td><span className={"status " + (SEVERITY[c.severity] ?? "s-nc")}><span className="dot" aria-hidden />{c.severity}</span></td>
                    <td className="whitespace-nowrap">{c.stage} · {STAGE_NAME[c.stage_key] ?? humanise(c.stage_key)}</td>
                    <td className="text-ink-3">{c.next_action.replace("{contact}", "the trusted contact")}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <H2 id="not-verified">What this prototype does not verify (PRD §14.2)</H2>
          <div className="card-flush mt-3 overflow-x-auto">
            <table className="tbl" data-testid="not-verified">
              <thead>
                <tr><th>Not verified</th><th>What it does instead</th></tr>
              </thead>
              <tbody>
                {p.does_not_verify.map((d) => (
                  <tr key={d.not_verified}>
                    <td className="font-medium">{d.not_verified}</td><td className="text-ink-3">{d.instead}</td>
                  </tr>))}
              </tbody>
            </table>
          </div>

          <H2 id="hosting">Demo hosting notes</H2>
          <div className="card mt-3 space-y-3 text-ink-2">
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
