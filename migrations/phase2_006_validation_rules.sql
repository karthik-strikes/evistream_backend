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
