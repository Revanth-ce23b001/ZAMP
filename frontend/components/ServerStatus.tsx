"use client";

// Day 9B: starts waking the backend as soon as any page opens, and explains the wait while it starts,
// so a sleeping free-tier server reads as "starting" rather than as a frozen page.

import { useEffect, useState } from "react";

import { backendFetch, onBackendWaiting } from "@/lib/backendStatus";
import { BACKEND } from "@/lib/client";

export default function ServerStatus() {
  const [waiting, setWaiting] = useState(false);
  useEffect(() => {
    const off = onBackendWaiting(setWaiting);
    backendFetch(`${BACKEND}/api/health`, { cache: "no-store" }).catch(() => undefined);   // wake it now
    return () => { off(); };
  }, []);
  if (!waiting) return null;
  return (
    <div role="status" data-testid="server-waking" className="border-b border-line bg-hold-bg">
      <div className="mx-auto flex max-w-7xl items-center gap-3 px-6 py-2.5 text-sm text-hold">
        <span className="dot s-hold animate-pulse" aria-hidden />
        <span>
          <b>Starting the server.</b> The free hosting puts the backend to sleep after about 15 minutes without
          use; waking it takes 30 to 45 seconds. This page continues on its own.
        </span>
      </div>
    </div>
  );
}
