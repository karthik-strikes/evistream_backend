-- Records how many extracted figure images are stored in S3 for a document.
--
-- Datalab returns the figures it pulled out of a PDF as a {filename: base64}
-- map alongside the markdown. We now store each one as its own S3 object under
--   images/{project_id}/{content_hash}/{filename}
-- which is exactly the bare filename the markdown references, so
-- `![](<hash>_img.jpg)` links resolve against that prefix.
--
-- The prefix is fully derivable from (project_id, content_hash), so no path
-- column is needed — but "how many are stored" is not derivable without an S3
-- list call per document, and that is the question the backfill needs to answer
-- cheaply. Hence a count rather than a path:
--
--   NULL -> images were never attempted for this document (backfill candidate)
--   0    -> attempted, and the paper genuinely has no extractable figures
--   n    -> n image objects are stored under the prefix
--
-- Written by `process_pdf_document` (new uploads), by `backfill_document_blocks`
-- (which gets the same images from the json/bbox call at no extra Datalab cost),
-- and by zscripts/backfill_document_images.py for historical documents.

ALTER TABLE documents
    ADD COLUMN IF NOT EXISTS image_count integer;
