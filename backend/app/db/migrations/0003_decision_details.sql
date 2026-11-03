-- 0003: structured detail for the decision details screen (FR-110, PRD §10.5).
--
-- duplicate_matches: the ledger entries the duplicate stage matched, with tier, so the Duplicates
--   tab can show them side by side and link to the original.
-- po_detail: the PO header and line diff as evaluated (PO total, previously approved, this
--   invoice, remaining, allowed overrun, and per line quantity and unit-price checks), so the PO
--   match tab shows exactly what the rules saw rather than recomputing it later.
-- SQLite adaptation: ADD COLUMN with json_valid CHECKs.
ALTER TABLE decisions ADD COLUMN duplicate_matches TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(duplicate_matches));
ALTER TABLE decisions ADD COLUMN po_detail TEXT CHECK (po_detail IS NULL OR json_valid(po_detail));
