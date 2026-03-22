-- Phase 2: Add reviewer identity to extraction_results
ALTER TABLE extraction_results
  ADD COLUMN IF NOT EXISTS extracted_by UUID REFERENCES users(id),
  ADD COLUMN IF NOT EXISTS reviewer_role TEXT CHECK (reviewer_role IN ('reviewer_1', 'reviewer_2', 'adjudicator', 'qa_reviewer'));

CREATE INDEX IF NOT EXISTS idx_extraction_results_extracted_by ON extraction_results(extracted_by);
CREATE UNIQUE INDEX IF NOT EXISTS idx_extraction_results_reviewer_doc_form
  ON extraction_results(document_id, form_id, reviewer_role)
  WHERE reviewer_role IS NOT NULL AND extraction_type = 'manual';
