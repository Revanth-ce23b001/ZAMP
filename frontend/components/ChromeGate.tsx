"use client";

// The cover screen at "/" is full-bleed: it carries its own branding on a dark backdrop, so the
// light header, the backend-status strip and the page container would fight it. Everywhere else
// the chrome is exactly as it was.

import { usePathname } from "next/navigation";

const COVER = "/";

export function ChromeGate({ children }: { children: React.ReactNode }) {
  return usePathname() === COVER ? null : <>{children}</>;
}

export function MainShell({ children }: { children: React.ReactNode }) {
  const cover = usePathname() === COVER;
  return <main className={cover ? "" : "mx-auto max-w-7xl px-6 py-8"}>{children}</main>;
}
