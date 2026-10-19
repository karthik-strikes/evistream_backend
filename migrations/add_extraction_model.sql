-- Multi-Model Extraction (Beta) — per-user model preference + per-result model tracking.
-- Applied: 2026-06-08
--
-- 1. Adds extraction_model to user_settings so each user can pick which LLM
--    their extractions run against. Validated against AVAILABLE_MODELS in
--    backend/config/models.py at write time.
-- 2. Adds model to extraction_results so each row records which LLM produced
--    it. Useful for filtering and per-model accuracy comparisons later.
-- 3. Updates replace_ai_extraction_results RPC to carry the model column.

ALTER TABLE user_settings
  ADD COLUMN IF NOT EXISTS extraction_model TEXT;

ALTER TABLE extraction_results
  ADD COLUMN IF NOT EXISTS model TEXT;

CREATE INDEX IF NOT EXISTS idx_extraction_results_model
  ON extraction_results(model);

CREATE OR REPLACE FUNCTION replace_ai_extraction_results(p_records JSONB)
RETURNS INT
LANGUAGE plpgsql
AS $$
DECLARE
    affected_count INT;
BEGIN
    INSERT INTO extraction_results (
        extraction_id, job_id, project_id, form_id,
        document_id, extracted_data, extraction_type, model
    )
    SELECT
        (rec->>'extraction_id')::UUID,
        (rec->>'job_id')::UUID,
        (rec->>'project_id')::UUID,
        (rec->>'form_id')::UUID,
        (rec->>'document_id')::UUID,
        rec->'extracted_data',
        rec->>'extraction_type',
        rec->>'model'
    FROM jsonb_array_elements(p_records) AS rec
    ON CONFLICT (extraction_id, document_id) WHERE reviewer_role IS NULL
    DO UPDATE SET
        job_id = EXCLUDED.job_id,
        extracted_data = EXCLUDED.extracted_data,
        extraction_type = EXCLUDED.extraction_type,
        model = EXCLUDED.model,
        created_at = NOW();

    GET DIAGNOSTICS affected_count = ROW_COUNT;
    RETURN affected_count;
END;
$$;
