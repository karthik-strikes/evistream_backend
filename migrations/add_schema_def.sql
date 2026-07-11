-- Phase A: Add schema_def JSONB column to forms and schemas tables.
--
-- schema_def stores the complete JSON representation of all DSPy Signatures
-- and pipeline stages, enabling runtime class construction without disk files.
-- New forms populate this column during code generation.
-- Existing active forms are backfilled by scripts/backfill_schema_def.py.

ALTER TABLE forms
  ADD COLUMN IF NOT EXISTS schema_def JSONB;

ALTER TABLE schemas
  ADD COLUMN IF NOT EXISTS schema_def JSONB;

COMMENT ON COLUMN forms.schema_def IS
  'JSON schema definition for runtime DSPy class construction. '
  'Populated by code-gen workflow (Phase A+). '
  'When USE_RUNTIME_BUILDERS=true, extraction uses this instead of disk .py files.';

COMMENT ON COLUMN schemas.schema_def IS
  'Mirrors forms.schema_def for fast cache-layer access without joining forms table.';

CREATE INDEX IF NOT EXISTS idx_forms_schema_def_not_null
  ON forms ((schema_def IS NOT NULL))
  WHERE schema_def IS NOT NULL;
