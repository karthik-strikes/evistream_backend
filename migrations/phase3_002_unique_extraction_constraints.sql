-- Phase 3: Add unique constraints to prevent duplicate extraction rows from race conditions
-- Applied: 2026-04-30

-- 1. Prevent duplicate synthetic grouping rows in extractions
--    for the same (project, form, status='manual'|'consensus').
--    Without this, two concurrent saves can both INSERT → non-deterministic reads.
CREATE UNIQUE INDEX IF NOT EXISTS extractions_project_form_status_unique
  ON extractions (project_id, form_id, status);

-- 2. Prevent duplicate manual extraction results for the same document
--    when no reviewer is assigned (reviewer_role IS NULL).
--    Complements phase2_001 which covers reviewer_role IS NOT NULL.
--    If a race hits, PostgreSQL will raise a unique constraint violation (safe error)
--    instead of silently storing two rows.
CREATE UNIQUE INDEX IF NOT EXISTS idx_extraction_results_no_role_unique
  ON extraction_results (extraction_id, document_id)
  WHERE reviewer_role IS NULL;
