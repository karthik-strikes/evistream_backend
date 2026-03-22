-- Phase 2: Review assignments table
CREATE TABLE IF NOT EXISTS review_assignments (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  form_id UUID NOT NULL REFERENCES forms(id) ON DELETE CASCADE,
  document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  reviewer_user_id UUID NOT NULL REFERENCES users(id),
  reviewer_role TEXT NOT NULL CHECK (reviewer_role IN ('reviewer_1', 'reviewer_2', 'adjudicator')),
  status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'in_progress', 'completed', 'skipped')),
  assigned_by UUID REFERENCES users(id),
  assigned_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  started_at TIMESTAMPTZ,
  completed_at TIMESTAMPTZ,
  is_training BOOLEAN NOT NULL DEFAULT false,
  gold_standard_result_id UUID REFERENCES extraction_results(id),
  UNIQUE(project_id, form_id, document_id, reviewer_role)
);

CREATE INDEX IF NOT EXISTS idx_review_assignments_reviewer ON review_assignments(reviewer_user_id, status);
CREATE INDEX IF NOT EXISTS idx_review_assignments_project ON review_assignments(project_id, form_id, status);
