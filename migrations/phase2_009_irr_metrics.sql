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
