-- Add Datalab positional/quality metadata to documents.
-- Each column is nullable so existing rows (which only have s3_markdown_path)
-- remain valid. New uploads populate all of these via pdf_tasks.process_pdf_document.
--
-- s3_blocks_path        — sidecar JSON in S3 with per-block {page, bbox, type, text}
--                         from Datalab's output_format=json response.
-- parse_quality_score   — 0-5 quality signal returned by Datalab. Lets us flag
--                         poorly-parsed docs and optionally re-run in accurate mode.
-- datalab_checkpoint_id — opaque ID returned when save_checkpoint=true. Lets the
--                         /extract and /segment endpoints reuse the parse for free.
-- datalab_request_id    — original /convert request_id. Required key for the
--                         /thumbnails/{lookup_key} endpoint (page thumbnails).
-- page_count            — number of pages Datalab actually processed.

ALTER TABLE documents
    ADD COLUMN IF NOT EXISTS s3_blocks_path        text,
    ADD COLUMN IF NOT EXISTS parse_quality_score   numeric,
    ADD COLUMN IF NOT EXISTS datalab_checkpoint_id text,
    ADD COLUMN IF NOT EXISTS datalab_request_id    text,
    ADD COLUMN IF NOT EXISTS page_count            integer;

-- Optional: index parse_quality_score so we can quickly find low-quality parses
-- (e.g., to surface a "low-quality parse" warning in the UI or batch-reparse).
CREATE INDEX IF NOT EXISTS documents_parse_quality_score_idx
    ON documents (parse_quality_score)
    WHERE parse_quality_score IS NOT NULL;
