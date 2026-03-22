ALTER TABLE documents ADD COLUMN IF NOT EXISTS content_hash TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS idx_documents_project_hash
  ON documents(project_id, content_hash)
  WHERE content_hash IS NOT NULL;
