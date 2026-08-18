-- ClinicalTrials.gov import support: a document can now originate from CT.gov
-- instead of an uploaded PDF (see backend/app/api/v1/clinical_trials.py:import).
-- Mirrors the additive, all-nullable style of add_doi.sql.

ALTER TABLE documents ADD COLUMN IF NOT EXISTS source_type VARCHAR(20) DEFAULT 'upload';
ALTER TABLE documents ADD COLUMN IF NOT EXISTS nct_id TEXT;
ALTER TABLE documents ADD COLUMN IF NOT EXISTS trial_status TEXT;  -- e.g. "COMPLETED" (row badge)
ALTER TABLE documents ADD COLUMN IF NOT EXISTS trial_phase TEXT;   -- e.g. "PHASE4" (row badge)

-- Unlike doi's non-unique index (a DOI can legitimately recur across a preprint
-- vs. published version), an NCT ID genuinely is a stable unique trial
-- identifier — enforce real dedup, scoped per project like content_hash's index.
CREATE UNIQUE INDEX IF NOT EXISTS idx_documents_project_nct_id
  ON documents(project_id, nct_id) WHERE nct_id IS NOT NULL;
