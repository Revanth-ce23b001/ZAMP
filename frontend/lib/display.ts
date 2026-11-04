// Frontend-only display timing. Nothing here changes what the backend decides or records.

// Live screen (Day 5B): each stage row stays on "Running" for at least this long before it shows its
// final status, so fast stages remain visible. Stored stage durations and timestamps are unchanged.
export const MIN_STAGE_DISPLAY_MS = 350;
