"use client";

// Points at the one link that starts invoice processing.
//
// The cover screen offers two links, and a first-time reviewer has no way to know which one opens
// the pipeline. This wraps the primary link in a caption, an arrow and a slow radar ping, so the
// click target reads as the way in without a second button or a changed route.
//
// It owns no routing and no copy of the link's label: the Link stays in `app/page.tsx`, where its
// href lives, and is passed in as children. Everything drawn here is white at reduced opacity,
// because the cover is the one dark surface in the product (DEC-024) and the ink tokens are
// unreadable on it. No new colour, no new font, no image asset: the arrow is inline SVG.
//
// Under prefers-reduced-motion the same caption and arrow render, static, and the ping becomes a
// plain 1px ring — nothing moves.

import { motion, useReducedMotion } from "motion/react";

// Truthful about what the link leads to: the Work Queue is where a sample is run or a PDF dropped.
const CAPTION = "Start here — process an invoice";

function Arrow() {
  return (
    <svg aria-hidden viewBox="0 0 16 16" className="h-4 w-4" fill="none"
         stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round">
      <path d="M8 2.5v11" />
      <path d="M3.5 9.5 8 14l4.5-4.5" />
    </svg>
  );
}

export function ProcessInvoiceNudge({ children }: { children: React.ReactNode }) {
  const reduceMotion = useReducedMotion();
  const caption = "text-[12px] font-semibold uppercase tracking-[0.08em] text-white/75";

  if (reduceMotion) {
    return (
      <span data-testid="invoice-nudge" data-animated="false"
            className="flex flex-col items-center gap-1.5">
        <span className={caption}>{CAPTION}</span>
        <span aria-hidden className="text-white/60"><Arrow /></span>
        <span className="relative inline-flex rounded-ctl ring-1 ring-white/40">{children}</span>
      </span>
    );
  }

  return (
    <span data-testid="invoice-nudge" data-animated="true"
          className="flex flex-col items-center gap-1.5">
      <motion.span className={caption}
                   animate={{ opacity: [0.6, 1, 0.6] }}
                   transition={{ repeat: Infinity, duration: 2.6, ease: "easeInOut" }}>
        {CAPTION}
      </motion.span>

      <motion.span aria-hidden className="text-white/60"
                   animate={{ y: [0, 4, 0] }}
                   transition={{ repeat: Infinity, duration: 1.4, ease: "easeInOut" }}>
        <Arrow />
      </motion.span>

      {/* Nothing animates the link itself. A moving click target never settles, and both
          Playwright and a user's own pointer wait for a still element — the pull comes from the
          glow and the ping behind it, which are siblings, so the link's box never moves.
          The ping stays inside the wrapper's box plus 4px scaled by 1.12, so a 360px-wide phone
          keeps its horizontal fit however wide the label renders. */}
      <span className="relative inline-flex">
        <motion.span aria-hidden
                     className="pointer-events-none absolute -inset-2 rounded-[11px] bg-white blur-md"
                     animate={{ opacity: [0.12, 0.34, 0.12] }}
                     transition={{ repeat: Infinity, duration: 2.6, ease: "easeInOut" }} />
        {[0, 1.1].map((delay) => (
          <motion.span key={delay} aria-hidden
                       className="pointer-events-none absolute -inset-1 rounded-[9px] border border-white/60"
                       initial={{ scale: 1, opacity: 0 }}
                       animate={{ scale: [1, 1.12], opacity: [0.65, 0] }}
                       transition={{ repeat: Infinity, duration: 2.2, delay, ease: "easeOut" }} />
        ))}
        <span className="relative inline-flex">{children}</span>
      </span>
    </span>
  );
}

export default ProcessInvoiceNudge;
