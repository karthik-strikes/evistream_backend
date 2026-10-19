-- Phase 2: Add reviewer identity to extraction_results
ALTER TABLE extraction_results
  ADD COLUMN IF NOT EXISTS extracted_by UUID REFERENCES users(id),
  ADD COLUMN IF NOT EXISTS reviewer_role TEXT CHECK (reviewer_role IN ('reviewer_1', 'reviewer_2', 'adjudicator', 'qa_reviewer'));

CREATE INDEX IF NOT EXISTS idx_extraction_results_extracted_by ON extraction_results(extracted_by);
CREATE UNIQUE INDEX IF NOT EXISTS idx_extraction_results_reviewer_doc_form
  ON extraction_results(document_id, form_id, reviewer_role)
  WHERE reviewer_role IS NOT NULL AND extraction_type = 'manual';
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
-- Phase 2: Controlled vocabularies
CREATE TABLE IF NOT EXISTS controlled_vocabularies (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id UUID REFERENCES projects(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  description TEXT,
  terms JSONB NOT NULL DEFAULT '[]',
  source TEXT DEFAULT 'custom',
  created_by UUID REFERENCES users(id),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_controlled_vocabularies_project ON controlled_vocabularies(project_id);

CREATE TABLE IF NOT EXISTS field_vocabulary_mappings (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  form_id UUID NOT NULL REFERENCES forms(id) ON DELETE CASCADE,
  field_name TEXT NOT NULL,
  vocabulary_id UUID NOT NULL REFERENCES controlled_vocabularies(id) ON DELETE CASCADE,
  validation_mode TEXT NOT NULL DEFAULT 'suggest' CHECK (validation_mode IN ('suggest', 'strict', 'warn')),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE(form_id, field_name, vocabulary_id)
);
-- Phase 2: Validation rules
CREATE TABLE IF NOT EXISTS validation_rules (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  form_id UUID NOT NULL REFERENCES forms(id) ON DELETE CASCADE,
  field_name TEXT NOT NULL,
  rule_type TEXT NOT NULL CHECK (rule_type IN ('range', 'format', 'required', 'cross_field', 'regex')),
  rule_config JSONB NOT NULL,
  severity TEXT NOT NULL DEFAULT 'warning' CHECK (severity IN ('error', 'warning', 'info')),
  message TEXT NOT NULL,
  is_active BOOLEAN NOT NULL DEFAULT true,
  created_by UUID REFERENCES users(id),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_validation_rules_form ON validation_rules(form_id, is_active);
-- Phase 2: Audit trail
CREATE TABLE IF NOT EXISTS audit_trail (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL REFERENCES users(id),
  project_id UUID REFERENCES projects(id),
  entity_type TEXT NOT NULL,
  entity_id UUID NOT NULL,
  action TEXT NOT NULL,
  field_name TEXT,
  old_value JSONB,
  new_value JSONB,
  metadata JSONB DEFAULT '{}',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_audit_trail_entity ON audit_trail(entity_type, entity_id);
CREATE INDEX IF NOT EXISTS idx_audit_trail_project ON audit_trail(project_id, created_at DESC);
-- Phase 2: New permissions + review settings
ALTER TABLE project_members
  ADD COLUMN IF NOT EXISTS can_adjudicate BOOLEAN NOT NULL DEFAULT false,
  ADD COLUMN IF NOT EXISTS can_qa_review BOOLEAN NOT NULL DEFAULT false,
  ADD COLUMN IF NOT EXISTS can_manage_assignments BOOLEAN NOT NULL DEFAULT false;

ALTER TABLE forms
  ADD COLUMN IF NOT EXISTS review_settings JSONB NOT NULL DEFAULT '{}';
-- Phase 2: IRR metrics cache
CREATE TABLE IF NOT EXISTS irr_metrics (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  form_id UUID NOT NULL REFERENCES forms(id) ON DELETE CASCADE,
  metric_type TEXT NOT NULL CHECK (metric_type IN ('cohens_kappa', 'percent_agreement', 'icc')),
  scope TEXT NOT NULL DEFAULT 'overall',
  scope_key TEXT,
  value NUMERIC(6,4),
  confidence_interval JSONB,
  sample_size INT,
  computed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  metadata JSONB DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_irr_metrics_project_form ON irr_metrics(project_id, form_id, metric_type);
