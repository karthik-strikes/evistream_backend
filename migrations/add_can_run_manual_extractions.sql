-- Split `can_run_extractions` into two perms:
--   can_run_extractions         → trigger AI extraction jobs (existing)
--   can_run_manual_extractions  → save manual extraction results (NEW)
--
-- Backfill copies the existing flag forward so nobody loses access on day one.

ALTER TABLE project_members
  ADD COLUMN IF NOT EXISTS can_run_manual_extractions BOOLEAN NOT NULL DEFAULT FALSE;

UPDATE project_members
   SET can_run_manual_extractions = TRUE
 WHERE can_run_extractions = TRUE
   AND can_run_manual_extractions = FALSE;

ALTER TABLE project_invitations
  ADD COLUMN IF NOT EXISTS can_run_manual_extractions BOOLEAN NOT NULL DEFAULT FALSE;

UPDATE project_invitations
   SET can_run_manual_extractions = TRUE
 WHERE can_run_extractions = TRUE
   AND can_run_manual_extractions = FALSE;
