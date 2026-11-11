"use client";

// Day 9B: explains the wait while a sleeping free-tier server starts, so it reads as "starting"
// rather than as a frozen page.
//
// The wake-up ping itself moved to BackendPrewarm, which is mounted outside <ChromeGate> so it also
// runs on the cover at "/" — this component is inside the gate and never renders there. This one
// only reports; it no longer starts anything.

import { useEffect, useState } from "react";

import { onBackendWaiting } from "@/lib/backendStatus";

export default function ServerStatus() {
  const [waiting, setWaiting] = useState(false);
  useEffect(() => onBackendWaiting(setWaiting), []);
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
