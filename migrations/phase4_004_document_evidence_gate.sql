-- Phase 4.004 — Evidence gate for thin-evidence documents
--
-- Problem: extraction selected on `processing_status = 'completed'` with no
-- check on whether full text actually existed. An abstract-only PubMed record
-- and a registration-only ClinicalTrials.gov record were both marked
-- `completed`, so a run read 250 words of abstract as if it were a full paper.
-- Every full-text-only field (randomisation, blinding, per-arm outcomes) came
-- back NR, indistinguishable from a genuine extraction failure — which also
-- silently depresses eval F1.
--
-- Fix: a third status, `metadata_only`, plus an explicit reviewer approval.
-- Approval is a SEPARATE column on purpose: the document stays `metadata_only`
-- forever, so results, exports and eval can always tell the evidence was thin.

ALTER TABLE documents
  ADD COLUMN IF NOT EXISTS metadata_extraction_approved BOOLEAN NOT NULL DEFAULT false;

-- Reclassify imports that were wrongly `completed`.
--   pubmed: no PDF → abstract only (a PMC-backed record has fullText in its
--           sidecar, which SQL can't see — see the note below).
--   ctgov:  no PDF ever; results-vs-registration lives in the sidecar too.
--   ris:    deliberately EXCLUDED — a `completed` no-PDF RIS row means PMC
--           structured full text was retrieved, which is genuine full text.
--   endnote/upload: already correct (needs_pdf / real PDFs).
UPDATE documents
   SET processing_status = 'metadata_only'
 WHERE processing_status = 'completed'
   AND s3_pdf_path IS NULL
   AND source_type IN ('pubmed', 'ctgov');

-- Grandfather ONLY documents that have already been extracted. Anything not
-- yet used surfaces in "Needs attention" for a real decision, rather than being
-- silently blessed. Non-destructive: no existing extraction_results are
-- invalidated and no in-flight project changes behaviour.
UPDATE documents d
   SET metadata_extraction_approved = true
 WHERE d.processing_status = 'metadata_only'
   AND EXISTS (SELECT 1 FROM extraction_results er WHERE er.document_id = d.id);

-- Matches the extraction selector.
CREATE INDEX IF NOT EXISTS idx_documents_extractable
  ON documents(project_id)
  WHERE processing_status = 'completed'
     OR (processing_status = 'metadata_only' AND metadata_extraction_approved);

-- NOTE (accepted imprecision): the `pubmed` reclassification above cannot tell
-- a PMC-backed record from an abstract-only one, because `full_text_source` is
-- computed at import time and never stored. It therefore errs toward holding
-- documents back — the safe direction. Any PMC-backed row that gets caught is
-- a one-time accept in the UI. New imports classify correctly at the source
-- (pubmed.py gates on full_text_source, clinical_trials.py on status.hasResults).
