-- Phase 2: QA reviews table
CREATE TABLE IF NOT EXISTS qa_reviews (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  form_id UUID NOT NULL REFERENCES forms(id) ON DELETE CASCADE,
  document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  qa_reviewer_id UUID NOT NULL REFERENCES users(id),
  source_result_id UUID REFERENCES extraction_results(id),
  source_adjudication_id UUID REFERENCES adjudication_results(id),
  status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'in_progress', 'passed', 'flagged')),
  field_comments JSONB NOT NULL DEFAULT '{}',
  overall_comment TEXT,
  flagged_field_count INT NOT NULL DEFAULT 0,
  total_fields_reviewed INT NOT NULL DEFAULT 0,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE(project_id, form_id, document_id)
);
