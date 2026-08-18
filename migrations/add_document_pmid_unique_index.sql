-- PubMed import support. The `pmid` column already exists on `documents`
-- (added by add_doi.sql, previously unused/reserved) — only the unique
-- index is new here, mirroring idx_documents_project_nct_id exactly.
CREATE UNIQUE INDEX IF NOT EXISTS idx_documents_project_pmid
  ON documents(project_id, pmid) WHERE pmid IS NOT NULL;
