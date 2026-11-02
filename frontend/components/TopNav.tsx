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
];

export default function TopNav() {
  const pathname = usePathname();
  return (
    <nav className="flex h-full items-stretch gap-1" aria-label="Main">
      {TABS.map((tab) => {
        const active = pathname === tab.href || pathname.startsWith(tab.href + "/");
        return (
          <Link
            key={tab.href}
            href={tab.href}
            aria-current={active ? "page" : undefined}
            className={
              "flex items-center border-b-2 px-4 text-sm " +
              (active
                ? "border-blue-600 font-semibold text-blue-700"
                : "border-transparent text-gray-600 hover:text-gray-900")
            }
          >
            {tab.label}
          </Link>
        );
      })}
    </nav>
  );
}
