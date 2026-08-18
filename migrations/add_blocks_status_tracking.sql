-- Track the Datalab blocks (json/bbox) sidecar call independently of the
-- overall document processing status.
--
-- Background: each PDF makes two Datalab Marker calls — call 1 (markdown, the
-- extraction text) and call 2 (json + bboxes, the highlight sidecar). Call 2's
-- failure was previously swallowed (logged only), so a "markdown OK, bbox failed"
-- doc was stored as fully `completed` with no error. These columns make the
-- call-2 outcome explicit and queryable, and drive the blocks backfill path.
--
-- blocks_status — 'pending'   : sidecar not yet obtained (legacy / not-run)
--                 'completed' : sidecar present in s3_blocks_path
--                 'failed'    : call 2 or its S3 upload was attempted and failed
-- blocks_error  — human-readable reason when blocks_status='failed'.

ALTER TABLE documents
    ADD COLUMN IF NOT EXISTS blocks_status text DEFAULT 'pending',
    ADD COLUMN IF NOT EXISTS blocks_error  text;

-- Backfill existing rows: a present sidecar means the blocks call succeeded.
UPDATE documents SET blocks_status = 'completed'
    WHERE s3_blocks_path IS NOT NULL AND blocks_status = 'pending';

-- Find backfill candidates quickly (completed markdown, no/failed blocks).
CREATE INDEX IF NOT EXISTS documents_blocks_status_idx
    ON documents (blocks_status)
    WHERE blocks_status <> 'completed';
