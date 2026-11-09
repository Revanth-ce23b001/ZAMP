"use client";

// A one-line status that shimmers while something is in progress. Used by the Live Run view while
// the model is reading the invoice. It is a status indicator, not a step: it shows and hides from
// the existing SSE stage state and owns none of its own.
//
// The gradient is built from the project's own ink tokens (globals.css): muted ink at rest, full
// ink as the moving highlight. No new colours, no new fonts.

import { motion, useReducedMotion } from "motion/react";

import { cn } from "@/lib/utils";

export function ShiningText({ text, className }: { text: string; className?: string }) {
  const reduceMotion = useReducedMotion();
  const shared = "block max-w-full truncate font-normal text-ink-3";

  // prefers-reduced-motion: the same words, static and muted, with nothing animating.
  if (reduceMotion) {
    return (
      <span role="status" aria-live="polite" data-testid="shining-text" data-animated="false"
            className={cn(shared, className)}>
        {text}
      </span>
    );
  }

  return (
    <motion.span
      role="status"
      aria-live="polite"
      data-testid="shining-text"
      data-animated="true"
      className={cn(shared, "bg-clip-text text-transparent", className)}
      style={{
        backgroundImage:
          "linear-gradient(110deg, var(--color-ink-3) 35%, var(--color-ink) 50%, var(--color-ink-3) 65%)",
        backgroundSize: "200% 100%",
      }}
      initial={{ backgroundPosition: "200% 0" }}
      animate={{ backgroundPosition: "-200% 0" }}
      transition={{ repeat: Infinity, duration: 2, ease: "linear" }}
    >
      {text}
    </motion.span>
  );
}

export default ShiningText;
