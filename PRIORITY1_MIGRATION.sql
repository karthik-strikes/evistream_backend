-- ============================================================================
-- Priority 1 Fixes - Database Migration
-- ============================================================================
-- Run this in Supabase SQL Editor to apply all Priority 1 database changes
-- Date: 2026-02-02
-- ============================================================================

-- ============================================================================
-- 1. ADD SCHEMAS TABLE (Issue #2: Schema Registry Persistence)
-- ============================================================================
-- Stores dynamically generated schemas for multi-worker persistence

CREATE TABLE IF NOT EXISTS schemas (
  schema_name TEXT PRIMARY KEY,
  task_name TEXT NOT NULL,
  signature_names JSONB NOT NULL,
  pipeline_stages JSONB NOT NULL,
  task_dir TEXT,
  form_id UUID REFERENCES forms(id) ON DELETE SET NULL,
  form_name TEXT,
  created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
  updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Indexes for performance
CREATE INDEX IF NOT EXISTS idx_schemas_form_id ON schemas(form_id);
CREATE INDEX IF NOT EXISTS idx_schemas_task_name ON schemas(task_name);
CREATE INDEX IF NOT EXISTS idx_schemas_created_at ON schemas(created_at DESC);

-- Trigger for auto-updating updated_at
DROP TRIGGER IF EXISTS update_schemas_updated_at ON schemas;
CREATE TRIGGER update_schemas_updated_at
    BEFORE UPDATE ON schemas
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- ============================================================================
-- 2. ADD EXTRACTIONS TABLE (Issue #1: Missing Extractions Table)
-- ============================================================================
-- Tracks extraction jobs (separate from extraction results)

CREATE TABLE IF NOT EXISTS extractions (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  form_id UUID NOT NULL REFERENCES forms(id) ON DELETE CASCADE,
  status VARCHAR(50) DEFAULT 'pending',
  created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Indexes for performance
CREATE INDEX IF NOT EXISTS idx_extractions_project_id ON extractions(project_id);
CREATE INDEX IF NOT EXISTS idx_extractions_form_id ON extractions(form_id);
CREATE INDEX IF NOT EXISTS idx_extractions_status ON extractions(status);
CREATE INDEX IF NOT EXISTS idx_extractions_created_at ON extractions(created_at DESC);

-- ============================================================================
-- VERIFICATION
-- ============================================================================
-- Verify tables were created

SELECT 'Migration complete! Verifying tables...' as status;

SELECT table_name,
       (SELECT COUNT(*) FROM information_schema.columns WHERE table_name = t.table_name) as column_count
FROM information_schema.tables t
WHERE table_schema = 'public'
  AND table_name IN ('schemas', 'extractions')
ORDER BY table_name;

-- Show all tables
SELECT 'All tables in database:' as status;
SELECT table_name
FROM information_schema.tables
WHERE table_schema = 'public'
  AND table_name IN ('users', 'projects', 'documents', 'forms', 'schemas', 'jobs', 'extractions', 'extraction_results')
ORDER BY table_name;

-- ============================================================================
-- NEXT STEPS
-- ============================================================================
-- After running this migration:
--
-- 1. Install dependencies:
--    pip install slowapi==0.1.9
--
-- 2. Restart FastAPI backend:
--    python -m app.main
--
-- 3. Start Celery workers:
--    ./start_workers.sh
--
-- ============================================================================
