"use client";

// Starts waking the free-tier backend the moment any page opens — including the cover at "/".
//
// Why this is separate from ServerStatus: the wake-up ping used to live there, and ServerStatus is
// rendered inside <ChromeGate>, which renders nothing on the cover. So the first page every visitor
// lands on never touched the backend, and the 30-45 s Render cold start only began when they
// clicked "Open the work queue" — the whole wait landed on them in one block, after the click.
//
// Fired here, outside the gate, the wake runs while they are still reading the cover, so the click
// lands on a server that is already starting (or already up). It does not make Render's cold start
// shorter; it stops the demo from paying for all of it at once.
//
// It renders nothing: the explanatory strip stays in ServerStatus, so the cover keeps the clean
// dark surface DEC-024 gave it.

import { useEffect } from "react";

import { backendFetch } from "@/lib/backendStatus";
import { BACKEND } from "@/lib/client";

export default function BackendPrewarm() {
  useEffect(() => {
    // Through backendFetch, so a cold start is retried and the "starting" strip knows a call is
    // already pending when the Work Queue mounts a moment later.
    backendFetch(`${BACKEND}/api/health`, { cache: "no-store" }).catch(() => undefined);
  }, []);
  return null;
}
