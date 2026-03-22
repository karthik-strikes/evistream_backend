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
