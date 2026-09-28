-- Issue 6 — wall-clock per production run, from llm_history.
-- duration_ms / document_id / step live in metadata JSONB (utils/llm_call_labels.py),
-- NOT as columns. Run in the Supabase SQL editor.
--
-- Set the window to the production run dates behind Table 1 before running.

-- 1) Per schema (form) x model: total machine time and cost.
SELECT
    schema_name,
    model,
    COUNT(*)                                              AS calls,
    COUNT(DISTINCT metadata->>'document_id')              AS papers,
    ROUND(SUM((metadata->>'duration_ms')::numeric)/1000.0, 1)          AS total_s,
    ROUND(SUM((metadata->>'duration_ms')::numeric)/1000.0
          / NULLIF(COUNT(DISTINCT metadata->>'document_id'), 0), 1)    AS mean_s_per_paper,
    ROUND(SUM(cost), 2)                                   AS cost_usd
FROM llm_history
WHERE metadata->>'duration_ms' IS NOT NULL
  AND created_at BETWEEN '2026-07-01' AND '2026-07-12'   -- <<< set to your run window
GROUP BY schema_name, model
ORDER BY schema_name, model;

-- 2) Per paper: median and spread. The 5-run local sample ranged 29s-344s,
--    so report a median and range, never a bare mean.
WITH per_paper AS (
    SELECT
        schema_name,
        model,
        metadata->>'document_id' AS doc_id,
        SUM((metadata->>'duration_ms')::numeric)/1000.0 AS paper_s
    FROM llm_history
    WHERE metadata->>'duration_ms' IS NOT NULL
      AND created_at BETWEEN '2026-07-01' AND '2026-07-12'
    GROUP BY 1, 2, 3
)
SELECT
    schema_name, model,
    COUNT(*)                                                       AS papers,
    ROUND(MIN(paper_s), 1)                                         AS min_s,
    ROUND(PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY paper_s)::numeric, 1) AS median_s,
    ROUND(MAX(paper_s), 1)                                         AS max_s
FROM per_paper
GROUP BY schema_name, model
ORDER BY schema_name, model;

-- 3) Where the time goes, by pipeline step (record discovery / slot fill / refill).
SELECT
    metadata->>'step' AS step,
    COUNT(*)          AS calls,
    ROUND(SUM((metadata->>'duration_ms')::numeric)/1000.0, 1) AS total_s,
    ROUND(100.0 * SUM((metadata->>'duration_ms')::numeric)
          / SUM(SUM((metadata->>'duration_ms')::numeric)) OVER (), 1) AS pct
FROM llm_history
WHERE metadata->>'duration_ms' IS NOT NULL
  AND created_at BETWEEN '2026-07-01' AND '2026-07-12'
GROUP BY 1
ORDER BY total_s DESC;
