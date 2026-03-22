-- Phase 3: Change review_assignments from per-form to per-project (per-document)
-- Assignments are now scoped to (project_id, document_id, reviewer_role) instead of
-- (project_id, form_id, document_id, reviewer_role).
-- Applied: 2026-03-19

-- Drop existing constraint and index that include form_id
ALTER TABLE review_assignments DROP CONSTRAINT IF EXISTS review_assignments_project_id_form_id_document_id_reviewer_role_key;
DROP INDEX IF EXISTS idx_review_assignments_project;

-- Drop form_id column
ALTER TABLE review_assignments DROP COLUMN IF EXISTS form_id;

-- Add new unique constraint and index
ALTER TABLE review_assignments ADD CONSTRAINT review_assignments_project_document_role_unique
  UNIQUE(project_id, document_id, reviewer_role);
CREATE INDEX IF NOT EXISTS idx_review_assignments_project ON review_assignments(project_id, status);
