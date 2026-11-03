-- 0002: commit plan stored with each decision.
--
-- commit_approval (FR-090..FR-094, §12.6) must recheck "this invoice's paired lines" against
-- current consumption at commit time, possibly long after the decision (a reviewer override).
-- The plan records what the rules engine paired: {po_number, taxable_value, lines:[{po_line_id,
-- quantity, value}]} with values in paise. NULL when the decision cannot be committed (no
-- explicit or confirmed PO). SQLite adaptation: ADD COLUMN with a json_valid CHECK.
ALTER TABLE decisions ADD COLUMN commit_plan TEXT CHECK (commit_plan IS NULL OR json_valid(commit_plan));
