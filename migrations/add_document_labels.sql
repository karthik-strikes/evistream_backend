ALTER TABLE documents ADD COLUMN IF NOT EXISTS labels TEXT[] DEFAULT '{}';
CREATE INDEX IF NOT EXISTS idx_documents_labels ON documents USING GIN (labels);
