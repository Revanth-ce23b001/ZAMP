"use client";

// Resolution drafts on the decision screen (Day 10, DEC-021). One section per recipient type.
// Every draft is a draft: there is no Send button, no mailto link and no recipient address. The
// text comes from the backend, which builds it from the stored decision; this component renders it
// and copies it. An APPROVE renders nothing at all, not an empty card.

import { useEffect, useState } from "react";

import { CodeChip } from "@/components/ui";
import { getDrafts, type RecipientType, type ResolutionDraft } from "@/lib/client";

const RECIPIENTS: { key: RecipientType; label: string; note?: string }[] = [
  { key: "VENDOR", label: "To the vendor" },
  { key: "BUYER_INTERNAL", label: "Internal" },
  {
    key: "TRUSTED_CONTACT",
    label: "Bank details",
    // FR-042, DEC-008: the contact itself is never shown here, and never leaves the backend.
    note: "Send only through the contact on file. Do not reply to the invoice.",
  },
];

function CopyButton({ draft }: { draft: ResolutionDraft }) {
  const [state, setState] = useState<"idle" | "copied" | "failed">("idle");
  const copy = async () => {
    const text = `Subject: ${draft.subject}\n\n${draft.body}`;
    try {
      await navigator.clipboard.writeText(text);
      setState("copied");
    } catch {
      setState("failed");
    }
    setTimeout(() => setState("idle"), 2000);
  };
  return (
    <button type="button" className="btn btn-secondary" data-testid="draft-copy" onClick={copy}
            aria-label={`Copy the draft to ${draft.recipient_type.toLowerCase()}`}>
      {state === "copied" ? "Copied" : state === "failed" ? "Press Ctrl+C" : "Copy"}
    </button>
  );
}

export default function ResolutionDrafts({ runId, status }: { runId: string; status: string }) {
  const [drafts, setDrafts] = useState<ResolutionDraft[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState<RecipientType | null>(null);

  useEffect(() => {
    if (status === "APPROVE") { setDrafts([]); return; }
    let live = true;
    getDrafts(runId)
      .then((d) => { if (live) { setDrafts(d); setOpen(d[0]?.recipient_type ?? null); } })
      .catch((e) => { if (live) setError(e?.message ?? "Drafts are unavailable"); });
    return () => { live = false; };
  }, [runId, status]);

  // Nothing to resolve: render nothing, not an empty card.
  if (status === "APPROVE" || (drafts !== null && drafts.length === 0)) return null;
  if (error) {
    return (
      <div className="card mt-5" data-testid="drafts-card">
        <h2 className="card-title">Resolution drafts</h2>
        <p role="alert" className="notice notice-hold mt-3">{error}</p>
      </div>
    );
  }
  if (drafts === null) {
    return (
      <div className="card mt-5" data-testid="drafts-card">
        <h2 className="card-title">Resolution drafts</h2>
        <div className="mt-3 h-24 animate-pulse rounded-card bg-hover" />
      </div>
    );
  }

  const shown = drafts.find((d) => d.recipient_type === open) ?? drafts[0];
  return (
    <div className="card mt-5" data-testid="drafts-card">
      <div className="flex flex-wrap items-center gap-3">
        <h2 className="card-title">Resolution drafts</h2>
        <span className="status s-nc" data-testid="draft-not-sent"><span className="dot" aria-hidden />Draft, not sent</span>
      </div>
      <p className="mt-1 text-[13px] text-ink-3">
        Written from the recorded evidence for this decision. Nothing is sent from here, and nothing is stored.
      </p>

      <div className="mt-4 flex flex-wrap gap-1 border-b border-line" role="tablist">
        {RECIPIENTS.filter((r) => drafts.some((d) => d.recipient_type === r.key)).map((r) => (
          <button key={r.key} type="button" role="tab" aria-selected={shown.recipient_type === r.key}
                  data-testid={`draft-tab-${r.key}`} onClick={() => setOpen(r.key)}
                  className={"-mb-px min-h-11 border-b-2 px-3 text-sm " +
                    (shown.recipient_type === r.key
                      ? "border-accent font-semibold text-ink"
                      : "border-transparent font-medium text-ink-3 hover:text-ink")}>
            {r.label}
          </button>))}
      </div>

      <div className="mt-4" data-testid="draft-panel">
        {RECIPIENTS.find((r) => r.key === shown.recipient_type)?.note && (
          <p role="note" data-testid="draft-channel-note" className="notice notice-bad mb-3">
            <strong>{RECIPIENTS.find((r) => r.key === shown.recipient_type)!.note}</strong>
          </p>
        )}
        <div className="flex flex-wrap items-start gap-x-3 gap-y-2">
          <div className="min-w-0 flex-1">
            <div className="micro">Subject</div>
            <div className="mt-1 break-words text-[15px] font-medium" data-testid="draft-subject">{shown.subject}</div>
          </div>
          <CopyButton draft={shown} />
        </div>
        <div className="mt-3 flex flex-wrap items-center gap-1.5">
          <span className="micro">Covers</span>
          {shown.codes_covered.map((c) => <CodeChip key={c} code={c} />)}
        </div>
        <pre data-testid="draft-body"
             className="mt-3 whitespace-pre-wrap break-words rounded-card border border-line bg-hover p-4 font-sans text-[14px] leading-6 text-ink">
{shown.body}
        </pre>
      </div>
    </div>
  );
}
