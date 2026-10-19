-- Add Anthropic prompt-cache token columns to llm_history.
--
-- Pairs with EXTRACTION_PROMPT_CACHE / CachingChatAdapter in the extraction
-- pipeline. cache_creation_input_tokens is billed at 1.25x input rate (5-min
-- TTL); cache_read_input_tokens is billed at 0.1x input rate. prompt_tokens
-- from LiteLLM continues to represent only the non-cached input portion.

ALTER TABLE llm_history
  ADD COLUMN IF NOT EXISTS cache_creation_input_tokens INT DEFAULT 0;

ALTER TABLE llm_history
  ADD COLUMN IF NOT EXISTS cache_read_input_tokens INT DEFAULT 0;
