-- Add best-effort bibliographic identity (DOI) to documents.
--
-- content_hash remains the ONLY guaranteed identity key (exact file bytes —
-- see add_content_hash.sql). DOI is a nullable, best-effort extra layer used
-- for duplicate *detection* across differently-byte'd copies of the same
-- paper (a re-download, a different PDF variant). It does not replace
-- content_hash and is never used to auto-merge/dedupe storage — same DOI can
-- legitimately be an author-accepted manuscript vs. the published version.
--
-- doi_source records how (if at all) the value was obtained, and doubles as
-- the "have we tried yet" marker consumed by the backfill batch endpoint:
--   NULL       — extraction not yet attempted (legacy docs pre-dating this column)
--   'metadata' — found in embedded PDF Info/XMP metadata
--   'text'     — found via regex over page-1 text
--   'crossref' — resolved via a Crossref bibliographic title search
--   'none'     — attempted, nothing resolved (do not keep retrying)
--
-- pmid is reserved for a future PubMed cross-reference; not populated by the
-- current DOI extraction cascade.

ALTER TABLE documents
    ADD COLUMN IF NOT EXISTS doi        text,
    ADD COLUMN IF NOT EXISTS doi_source text,
    ADD COLUMN IF NOT EXISTS title      text,
    ADD COLUMN IF NOT EXISTS pmid       text;

-- Non-unique: the same DOI can legitimately recur. Used to flag possible
-- duplicates for user review, never to auto-merge.
CREATE INDEX IF NOT EXISTS idx_documents_project_doi
    ON documents (project_id, doi)
    WHERE doi IS NOT NULL;

-- Backfill candidates: markdown already processed, DOI extraction never attempted.
CREATE INDEX IF NOT EXISTS documents_doi_backfill_idx
    ON documents (processing_status)
    WHERE doi_source IS NULL;
