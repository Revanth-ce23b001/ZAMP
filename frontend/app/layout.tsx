import type { Metadata } from "next";
import Link from "next/link";

import BackendPrewarm from "@/components/BackendPrewarm";
import { ChromeGate, MainShell } from "@/components/ChromeGate";
import ServerStatus from "@/components/ServerStatus";
import TopNav from "@/components/TopNav";
import { APP_DESCRIPTION, APP_MARK, APP_NAME } from "@/lib/brand";
import { BACKEND } from "@/lib/client";

import "./globals.css";

export const metadata: Metadata = {
  title: APP_NAME,
  description: APP_DESCRIPTION,
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <head>
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="" />
        {/* The backend is a different origin on deploy. Opening the connection while the HTML is
            still parsing takes the DNS and TLS handshake off the first call's critical path. */}
        <link rel="preconnect" href={BACKEND} crossOrigin="" />
        <link rel="stylesheet"
              href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&family=JetBrains+Mono:wght@400;500&display=swap" />
      </head>
      <body className="min-h-screen">
        {/* Outside the gate on purpose: the cover at "/" renders no chrome, but it must still start
            waking the backend. Renders nothing. */}
        <BackendPrewarm />
        <ChromeGate>
        <header className="h-14 border-b border-line bg-surface">
          <div className="mx-auto flex h-full max-w-7xl items-center gap-6 px-6">
            <Link href="/work-queue" className="flex items-center gap-2 no-underline">
              <span aria-hidden className="flex h-7 w-7 items-center justify-center rounded-ctl bg-accent text-[11px] font-semibold text-white">{APP_MARK}</span>
              <span className="text-[15px] font-semibold tracking-tight text-ink">{APP_NAME}</span>
            </Link>
            <TopNav />
            <div className="ml-auto flex items-center gap-3">
              <span className="hidden rounded-full border border-line px-3 py-1 text-xs font-medium text-ink-2 sm:inline-block">Demo data</span>
              {/* PRD §10: the top bar carries "Reset demo data". Wireframe screen 11 is its page. */}
              <Link href="/demo-reset" className="btn btn-secondary">
                Reset demo data
              </Link>
            </div>
          </div>
        </header>
        <ServerStatus />
        </ChromeGate>
        <MainShell>{children}</MainShell>
      </body>
    </html>
  );
}
