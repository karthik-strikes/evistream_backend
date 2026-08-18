-- Phase 4: Project-level review settings (blinding configuration)
-- Adds a review_settings JSONB column to projects, scoped correctly to match
-- how review_assignments already works (project + document, not per-form —
-- see phase3_001_assignment_per_project.sql). The existing forms.review_settings
-- column (phase2_008_permissions_review_settings.sql) has no write path and no
-- live data anywhere, so it's left untouched/legacy rather than migrated.

ALTER TABLE projects
  ADD COLUMN IF NOT EXISTS review_settings JSONB NOT NULL DEFAULT '{}';
