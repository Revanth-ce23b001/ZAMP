import type { Metadata } from "next";
import Link from "next/link";

import TopNav from "@/components/TopNav";

import "./globals.css";

export const metadata: Metadata = {
  title: "ClearMatch AP",
  description: "AI-assisted invoice processing prototype (synthetic data)",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="min-h-screen">
        <header className="h-14 border-b border-gray-200 bg-white">
          <div className="mx-auto flex h-full max-w-7xl items-stretch gap-6 px-6">
            <Link href="/work-queue" className="flex items-center">
              <span className="rounded bg-blue-700 px-3 py-1.5 text-sm font-semibold text-white">ClearMatch AP</span>
            </Link>
            <TopNav />
            <div className="ml-auto flex items-center">
              {/* PRD §10: the top bar carries "Reset demo data". Wireframe screen 11 is its page. */}
              <Link href="/demo-reset" className="text-sm text-gray-600 hover:text-gray-900">
                Reset demo data
              </Link>
            </div>
          </div>
        </header>
        <main className="mx-auto max-w-7xl px-6 py-8">{children}</main>
      </body>
    </html>
  );
}
