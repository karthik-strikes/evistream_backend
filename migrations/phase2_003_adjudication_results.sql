-- Phase 2: Adjudication results table
CREATE TABLE IF NOT EXISTS adjudication_results (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  form_id UUID NOT NULL REFERENCES forms(id) ON DELETE CASCADE,
  document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  adjudicator_id UUID NOT NULL REFERENCES users(id),
  reviewer_1_result_id UUID REFERENCES extraction_results(id),
  reviewer_2_result_id UUID REFERENCES extraction_results(id),
  field_resolutions JSONB NOT NULL DEFAULT '{}',
  agreed_count INT NOT NULL DEFAULT 0,
  disagreed_count INT NOT NULL DEFAULT 0,
  total_fields INT NOT NULL DEFAULT 0,
  agreement_pct NUMERIC(5,2),
  status TEXT NOT NULL DEFAULT 'in_progress' CHECK (status IN ('in_progress', 'completed')),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE(project_id, form_id, document_id)
);
