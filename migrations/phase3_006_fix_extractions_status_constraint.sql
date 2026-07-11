-- Phase 3: Narrow the extractions status unique index to manual/consensus only
-- Problem: extractions_project_form_status_unique was created as a full (non-partial)
-- index on (project_id, form_id, status). This prevents a second AI extraction run
-- from ever transitioning to status='failed' when a prior run for the same project+form
-- already has status='failed', raising 23505 and leaving the extraction stuck in
-- 'pending' with no retry button available in the UI.
--
-- The constraint was only ever needed for manual/consensus grouping rows in results.py
-- (which upserts on conflict of project_id+form_id+status). AI extraction rows
-- (status: pending → completed/failed) need no such constraint — each run gets its
-- own row with a fresh id.
--
-- Fix: drop the full index, recreate it as partial for manual/consensus only.
-- The results.py upsert (on_conflict="project_id,form_id,status") continues to work
-- because the inserted status is always "manual" or "consensus", which satisfies the
-- partial index predicate.

DROP INDEX IF EXISTS extractions_project_form_status_unique;

CREATE UNIQUE INDEX extractions_project_form_status_unique
  ON extractions (project_id, form_id, status)
  WHERE status IN ('manual', 'consensus');
