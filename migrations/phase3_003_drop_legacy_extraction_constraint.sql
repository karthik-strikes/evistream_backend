-- Phase 3: Drop legacy non-partial UNIQUE constraint on extraction_results
-- Applied: 2026-05-04
--
-- Background: An early migration created a full (non-partial) UNIQUE constraint
-- named "extraction_results_extraction_document_unique" on (extraction_id, document_id).
-- This predates the reviewer_role column and was never explicitly dropped when
-- phase2_001 and phase3_002 added partial-index replacements:
--   idx_extraction_results_no_role_unique    → (extraction_id, document_id) WHERE reviewer_role IS NULL
--   idx_extraction_results_reviewer_doc_form → (document_id, form_id, reviewer_role) WHERE reviewer_role IS NOT NULL
--
-- The legacy constraint blocked INSERTing a second row for the same (extraction_id, document_id)
-- even with a different reviewer_role, making dual-reviewer saves impossible.
--
-- Verify first:
--   SELECT conname, pg_get_constraintdef(oid)
--   FROM pg_constraint
--   WHERE conrelid = 'public.extraction_results'::regclass AND contype = 'u';

ALTER TABLE public.extraction_results
  DROP CONSTRAINT IF EXISTS extraction_results_extraction_document_unique;
