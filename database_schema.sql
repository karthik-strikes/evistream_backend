-- ============================================================================
-- eviStream Database Schema for Supabase
-- ============================================================================
-- This file contains all table definitions for the eviStream platform
-- Run this in Supabase SQL Editor: Dashboard > SQL Editor > New Query
-- ============================================================================

-- NOTE: The 'users' table already exists from auth setup
-- If you need to recreate it, uncomment below:

/*
CREATE TABLE IF NOT EXISTS users (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  email TEXT UNIQUE NOT NULL,
  hashed_password TEXT NOT NULL,
  full_name TEXT,
  is_active BOOLEAN DEFAULT TRUE,
  created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);
*/

-- ============================================================================
-- PROJECTS TABLE
-- ============================================================================
-- Stores user projects (systematic reviews, research studies, etc.)

CREATE TABLE IF NOT EXISTS projects (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  name VARCHAR(255) NOT NULL,
  description TEXT,
  created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
  updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Indexes for performance
CREATE INDEX IF NOT EXISTS idx_projects_user_id ON projects(user_id);
CREATE INDEX IF NOT EXISTS idx_projects_created_at ON projects(created_at DESC);

-- ============================================================================
-- DOCUMENTS TABLE
-- ============================================================================
-- Stores uploaded PDF documents and their processing status

CREATE TABLE IF NOT EXISTS documents (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  filename VARCHAR(255) NOT NULL,
  unique_filename VARCHAR(255),
  s3_pdf_path VARCHAR(512),
  s3_markdown_path VARCHAR(512),
  processing_status VARCHAR(50) DEFAULT 'pending',
  processing_error TEXT,
  created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Indexes for performance
CREATE INDEX IF NOT EXISTS idx_documents_project_id ON documents(project_id);
CREATE INDEX IF NOT EXISTS idx_documents_status ON documents(processing_status);
CREATE INDEX IF NOT EXISTS idx_documents_created_at ON documents(created_at DESC);

-- ============================================================================
-- FORMS TABLE
-- ============================================================================
-- Stores custom extraction forms and their generated code

CREATE TABLE IF NOT EXISTS forms (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  form_name VARCHAR(255) NOT NULL,
  form_description TEXT,
  fields JSONB NOT NULL,
  status VARCHAR(50) DEFAULT 'draft',
  schema_name VARCHAR(255),
  task_dir VARCHAR(512),
  statistics JSONB,
  error TEXT,
  created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
  updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Indexes for performance
CREATE INDEX IF NOT EXISTS idx_forms_project_id ON forms(project_id);
CREATE INDEX IF NOT EXISTS idx_forms_status ON forms(status);
CREATE INDEX IF NOT EXISTS idx_forms_created_at ON forms(created_at DESC);

-- GIN index for JSONB fields column (for field searches)
CREATE INDEX IF NOT EXISTS idx_forms_fields ON forms USING GIN (fields);

-- ============================================================================
-- JOBS TABLE
-- ============================================================================
-- Tracks background jobs (PDF processing, code generation, extraction)

CREATE TABLE IF NOT EXISTS jobs (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  project_id UUID REFERENCES projects(id) ON DELETE CASCADE,
  job_type VARCHAR(50) NOT NULL,
  status VARCHAR(50) DEFAULT 'pending',
  progress INTEGER DEFAULT 0,
  celery_task_id VARCHAR(255),
  input_data JSONB,
  result_data JSONB,
  error_message TEXT,
  started_at TIMESTAMP WITH TIME ZONE,
  completed_at TIMESTAMP WITH TIME ZONE,
  created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Indexes for performance
CREATE INDEX IF NOT EXISTS idx_jobs_user_id ON jobs(user_id);
CREATE INDEX IF NOT EXISTS idx_jobs_project_id ON jobs(project_id);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_jobs_type ON jobs(job_type);
CREATE INDEX IF NOT EXISTS idx_jobs_celery_task_id ON jobs(celery_task_id);
CREATE INDEX IF NOT EXISTS idx_jobs_created_at ON jobs(created_at DESC);

-- ============================================================================
-- SCHEMAS TABLE
-- ============================================================================
-- Stores dynamically generated schemas (replaces in-memory registry)

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

-- ============================================================================
-- EXTRACTIONS TABLE
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
-- EXTRACTION_RESULTS TABLE
-- ============================================================================
-- Stores extraction results for each document-form combination

CREATE TABLE IF NOT EXISTS extraction_results (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  job_id UUID NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
  project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  form_id UUID NOT NULL REFERENCES forms(id) ON DELETE CASCADE,
  document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  extracted_data JSONB NOT NULL,
  evaluation_metrics JSONB,
  created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Indexes for performance
CREATE INDEX IF NOT EXISTS idx_extraction_results_job_id ON extraction_results(job_id);
CREATE INDEX IF NOT EXISTS idx_extraction_results_project_id ON extraction_results(project_id);
CREATE INDEX IF NOT EXISTS idx_extraction_results_form_id ON extraction_results(form_id);
CREATE INDEX IF NOT EXISTS idx_extraction_results_document_id ON extraction_results(document_id);
CREATE INDEX IF NOT EXISTS idx_extraction_results_created_at ON extraction_results(created_at DESC);

-- GIN index for JSONB extracted_data column (for result searches)
CREATE INDEX IF NOT EXISTS idx_extraction_results_data ON extraction_results USING GIN (extracted_data);

-- ============================================================================
-- HELPER FUNCTIONS
-- ============================================================================

-- Function to automatically update updated_at timestamp
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ language 'plpgsql';

-- Triggers to auto-update updated_at on projects
DROP TRIGGER IF EXISTS update_projects_updated_at ON projects;
CREATE TRIGGER update_projects_updated_at
    BEFORE UPDATE ON projects
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- Triggers to auto-update updated_at on forms
DROP TRIGGER IF EXISTS update_forms_updated_at ON forms;
CREATE TRIGGER update_forms_updated_at
    BEFORE UPDATE ON forms
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- Triggers to auto-update updated_at on schemas
DROP TRIGGER IF EXISTS update_schemas_updated_at ON schemas;
CREATE TRIGGER update_schemas_updated_at
    BEFORE UPDATE ON schemas
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- ============================================================================
-- VERIFICATION QUERIES
-- ============================================================================
-- Run these to verify tables were created successfully

SELECT 'Tables created:' as status;

SELECT table_name
FROM information_schema.tables
WHERE table_schema = 'public'
  AND table_name IN ('users', 'projects', 'documents', 'forms', 'schemas', 'jobs', 'extractions', 'extraction_results')
ORDER BY table_name;

-- Count records in each table
SELECT
  'users' as table_name, COUNT(*) as count FROM users
UNION ALL
SELECT 'projects', COUNT(*) FROM projects
UNION ALL
SELECT 'documents', COUNT(*) FROM documents
UNION ALL
SELECT 'forms', COUNT(*) FROM forms
UNION ALL
SELECT 'schemas', COUNT(*) FROM schemas
UNION ALL
SELECT 'jobs', COUNT(*) FROM jobs
UNION ALL
SELECT 'extractions', COUNT(*) FROM extractions
UNION ALL
SELECT 'extraction_results', COUNT(*) FROM extraction_results;

-- ============================================================================
-- ROLE COLUMN MIGRATION
-- ============================================================================
-- Run this once in Supabase SQL Editor to add role-based access control

ALTER TABLE users
  ADD COLUMN IF NOT EXISTS role TEXT NOT NULL DEFAULT 'user'
  CHECK (role IN ('admin', 'user'));

CREATE INDEX IF NOT EXISTS idx_users_role ON users(role);

-- ============================================================================
-- CLEANUP (if needed)
-- ============================================================================
-- Uncomment these lines if you need to drop all tables and start fresh
-- WARNING: This will delete ALL data!

/*
DROP TABLE IF EXISTS extraction_results CASCADE;
DROP TABLE IF EXISTS extractions CASCADE;
DROP TABLE IF EXISTS jobs CASCADE;
DROP TABLE IF EXISTS schemas CASCADE;
DROP TABLE IF EXISTS forms CASCADE;
DROP TABLE IF EXISTS documents CASCADE;
DROP TABLE IF EXISTS projects CASCADE;
-- DROP TABLE IF EXISTS users CASCADE;  -- Be careful with this!
DROP FUNCTION IF EXISTS update_updated_at_column CASCADE;
*/

-- ============================================================================
-- PROJECT MEMBERS TABLE
-- ============================================================================
-- Stores per-user permission flags per project for collaborative access

CREATE TABLE IF NOT EXISTS project_members (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  can_view_docs BOOLEAN NOT NULL DEFAULT TRUE,
  can_upload_docs BOOLEAN NOT NULL DEFAULT FALSE,
  can_create_forms BOOLEAN NOT NULL DEFAULT FALSE,
  can_run_extractions BOOLEAN NOT NULL DEFAULT FALSE,
  can_view_results BOOLEAN NOT NULL DEFAULT TRUE,
  invited_by UUID REFERENCES users(id),
  created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
  UNIQUE(project_id, user_id)
);

CREATE INDEX IF NOT EXISTS idx_project_members_project ON project_members(project_id);
CREATE INDEX IF NOT EXISTS idx_project_members_user ON project_members(user_id);
