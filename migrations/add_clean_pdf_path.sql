-- Adds the storage path for the annotation-stripped variant of the PDF.
--
-- Author-added markup (yellow highlights, sticky notes, underlines, etc.)
-- baked into uploaded PDFs visually competes with our own source-text
-- overlay and confuses reviewers. We strip those markings server-side via
-- pikepdf/PyMuPDF + ghostscript and store the cleaned copy under a separate
-- key so the original is still recoverable if needed.
--
-- s3_clean_pdf_path is set by the `clean_pdf_document` Celery task, which
-- runs:
--   - eagerly after `process_pdf_document` succeeds (new uploads)
--   - lazily from the `/documents/{id}/file` endpoint when the column is
--     NULL but `s3_pdf_path` is set (legacy uploads)
--
-- When the cleaner produces a no-op result (no annotations found, no
-- ghostscript improvement), the task writes `s3_pdf_path` into this column
-- so we don't keep re-attempting on every viewer load.

ALTER TABLE documents
    ADD COLUMN IF NOT EXISTS s3_clean_pdf_path text;
