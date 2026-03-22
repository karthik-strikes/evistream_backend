-- Phase 2: New permissions + review settings
ALTER TABLE project_members
  ADD COLUMN IF NOT EXISTS can_adjudicate BOOLEAN NOT NULL DEFAULT false,
  ADD COLUMN IF NOT EXISTS can_qa_review BOOLEAN NOT NULL DEFAULT false,
  ADD COLUMN IF NOT EXISTS can_manage_assignments BOOLEAN NOT NULL DEFAULT false;

ALTER TABLE forms
  ADD COLUMN IF NOT EXISTS review_settings JSONB NOT NULL DEFAULT '{}';
