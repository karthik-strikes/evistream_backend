-- Phase 4: Atomic replace of AI extraction results.
-- Applied: 2026-05-12
--
-- Replaces the non-transactional DELETE+INSERT pattern in
-- backend/app/workers/extraction_tasks.py:262-277 with a single
-- transactional INSERT ... ON CONFLICT that targets the partial unique
-- index idx_extraction_results_no_role_unique (WHERE reviewer_role IS NULL,
-- from phase3_002).
--
-- Manual reviewer rows (reviewer_role IS NOT NULL) are not visible to this
-- partial index and are unaffected by this function.
--
-- Why a function: PostgREST's `on_conflict` query parameter cannot supply
-- the `WHERE reviewer_role IS NULL` predicate that Postgres requires to
-- infer a partial unique index. Wrapping the upsert in a function lets us
-- spell the predicate inline and call it via supabase.rpc().

CREATE OR REPLACE FUNCTION replace_ai_extraction_results(p_records JSONB)
RETURNS INT
LANGUAGE plpgsql
AS $$
DECLARE
    affected_count INT;
BEGIN
    INSERT INTO extraction_results (
        extraction_id, job_id, project_id, form_id,
        document_id, extracted_data, extraction_type
    )
    SELECT
        (rec->>'extraction_id')::UUID,
        (rec->>'job_id')::UUID,
        (rec->>'project_id')::UUID,
        (rec->>'form_id')::UUID,
        (rec->>'document_id')::UUID,
        rec->'extracted_data',
        rec->>'extraction_type'
    FROM jsonb_array_elements(p_records) AS rec
    ON CONFLICT (extraction_id, document_id) WHERE reviewer_role IS NULL
    DO UPDATE SET
        job_id = EXCLUDED.job_id,
        extracted_data = EXCLUDED.extracted_data,
        extraction_type = EXCLUDED.extraction_type,
        created_at = NOW();

    GET DIAGNOSTICS affected_count = ROW_COUNT;
    RETURN affected_count;
END;
$$;
