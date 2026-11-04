"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

// Global tabs per the wireframe, plus Policy (FR-117). See DECISIONS.md DEC-010.
const TABS = [
  { href: "/work-queue", label: "Work Queue" },
  { href: "/pos", label: "POs" },
  { href: "/vendors", label: "Vendors" },
  { href: "/reports", label: "Reports" },
  { href: "/policy", label: "Policy" },
  { href: "/accuracy", label: "Accuracy" },
];

export default function TopNav() {
  const pathname = usePathname();
  return (
    <nav className="flex items-center gap-1" aria-label="Main">
      {TABS.map((tab) => {
        const active = pathname === tab.href || pathname.startsWith(tab.href + "/");
        return (
          <Link
            key={tab.href}
            href={tab.href}
            aria-current={active ? "page" : undefined}
            className={
              "flex min-h-11 items-center rounded-[6px] px-3 text-sm no-underline " +
              (active ? "bg-hover font-semibold text-accent" : "font-medium text-ink-2 hover:bg-hover hover:text-ink")
            }
          >
            {tab.label}
          </Link>
        );
      })}
    </nav>
  );
}
