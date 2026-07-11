-- Migration: Create llm_history table for LLM cost & token tracking
-- Description: Stores per-call DSPy LM usage (tokens, cost, model, cache hit) so
-- spend can be aggregated by model / schema / day. The columns match what
-- utils/supabase_client.py:save_llm_history() and utils/logging.py:log_history()
-- already write — they upsert on call_hash.
-- Date: 2026-06-01

CREATE TABLE IF NOT EXISTS llm_history (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    call_hash       TEXT UNIQUE NOT NULL,
    call_uuid       TEXT,
    call_timestamp  TIMESTAMPTZ,
    model           TEXT NOT NULL,
    cost            NUMERIC(14, 8) DEFAULT 0,
    prompt_tokens   INTEGER DEFAULT 0,
    completion_tokens INTEGER DEFAULT 0,
    total_tokens    INTEGER DEFAULT 0,
    cache_hit       BOOLEAN DEFAULT FALSE,
    messages        JSONB,
    system_prompt   TEXT,
    user_prompt     TEXT,
    assistant_response TEXT,
    source_file     TEXT,
    schema_name     TEXT,
    extraction_id   UUID REFERENCES extraction_results(id) ON DELETE SET NULL,
    evaluation_id   UUID,
    metadata        JSONB DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_llm_history_model        ON llm_history(model);
CREATE INDEX IF NOT EXISTS idx_llm_history_schema       ON llm_history(schema_name);
CREATE INDEX IF NOT EXISTS idx_llm_history_extraction   ON llm_history(extraction_id);
CREATE INDEX IF NOT EXISTS idx_llm_history_created      ON llm_history(created_at);

COMMENT ON TABLE  llm_history IS 'Per-call LLM usage for cost/token aggregation. Populated by utils/logging.py:log_history().';
COMMENT ON COLUMN llm_history.call_hash IS 'md5 of messages+timestamp+uuid — uniqueness guard for upsert.';
COMMENT ON COLUMN llm_history.cost IS 'Cost in USD as reported by LiteLLM (0 if model unrecognized).';
COMMENT ON COLUMN llm_history.source_file IS 'Free-form tag: doc_id for extraction, "codegen:decompose"/"codegen:signatures" for codegen.';
