-- Migration: Add metadata column to forms table
-- Description: Stores workflow thread_id and decomposition data for human review
-- Date: 2026-02-14

-- Add metadata column to forms table
ALTER TABLE forms
ADD COLUMN IF NOT EXISTS metadata JSONB;

-- Add GIN index for metadata JSONB queries
CREATE INDEX IF NOT EXISTS idx_forms_metadata ON forms USING GIN (metadata);

-- Add comment
COMMENT ON COLUMN forms.metadata IS 'Stores workflow state including thread_id for human review resumption';
