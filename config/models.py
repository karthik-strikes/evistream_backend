"""
eviStream LLM Model Manifest
============================
Single source of truth for ALL model names, rate limits, and thresholds.

HOW TO USE:
  Change a value here → it changes everywhere in the backend.
  Use env vars to override at deployment time without code changes.

FORMAT NOTE:
  All model names use LiteLLM/DSPy format: "provider/model-name"
  lm_config.get_langchain_model() converts to LangChain format as needed.
"""
import os

# ── EXTRACTION ────────────────────────────────────────────────────────
# DSPy pipeline • dspy_fallback.py • circuit_breaker.py
EXTRACTION_PRIMARY_MODEL     = os.environ.get("EXTRACTION_PRIMARY_MODEL",
                                               "anthropic/claude-sonnet-5")
EXTRACTION_FALLBACK_MODELS   = ["openai/gpt-4o", "gemini/gemini-2.0-flash-exp"]
# Output-token ceiling. Was 20000, which a wide grounded table exhausts: one row
# of an 11-column table with verbatim per-cell quotes costs ~670 output tokens,
# so 20k truncated at ~29 rows — mid-JSON, silently, producing a partial table
# that looked complete. Sonnet 5 and the Opus family accept 128k; 64k is the
# headroom that fits a ~95-row table without inviting an unbounded generation.
# Per-model clamping happens in MODEL_MAX_OUTPUT_TOKENS below — do NOT assume
# every model accepts this value.
#
# Raised 64k → 100k (Aug 13 2026). 64k was not just a row ceiling: on Sonnet 5
# adaptive thinking is ON by default, and max_tokens caps thinking AND answer
# together, so a 76-row table spent ~40% of the budget reasoning and truncated
# the row list mid-JSON (job 675b0cc7, "row discovery truncated at max_tokens").
# 100k leaves the answer room to finish after the model has thought. Still under
# Sonnet 5's 128k ceiling; smaller models are clamped by MODEL_MAX_OUTPUT_TOKENS.
EXTRACTION_MAX_TOKENS        = int(os.environ.get("EXTRACTION_MAX_TOKENS", "100000"))
EXTRACTION_TEMPERATURE       = 1.0

# How hard the model thinks before answering, and whether it thinks at all.
# Both were previously unset, which is NOT the same as off: Sonnet 5 defaults to
# adaptive thinking at effort `high`, so every call — including a one-word scalar
# field — ran at the second-highest of five levels by omission rather than by
# choice. `medium` is the deliberate setting; the level is the main cost/latency
# dial on this pipeline.
#
# ANTHROPIC-ONLY. Both parameters are rejected by the OpenAI and Gemini
# fallbacks, so every call site must gate on the model prefix — the same trap
# CachingChatAdapter has (see utils/caching_adapter.py).
EXTRACTION_EFFORT            = os.environ.get("EXTRACTION_EFFORT", "medium")
EXTRACTION_THINKING          = os.environ.get("EXTRACTION_THINKING", "adaptive")
EXTRACTION_BATCH_CONCURRENCY = 5     # papers in parallel per job
EXTRACTION_MAX_JOBS_PER_USER = 10    # soft cap — users can queue up to 10, system processes as capacity allows
EXTRACTION_MAX_DOCS_PER_JOB  = 100
EXTRACTION_TASK_CONCURRENCY  = 350  # max simultaneous LLM calls across ALL papers AND stages

# Agentic table extraction (forms with schema_def["table_extraction_mode"]=="agentic").
# Each session is a Claude Agent SDK subprocess, so this is deliberately tiny and
# deliberately separate from EXTRACTION_TASK_CONCURRENCY above — those are cheap
# in-process LLM calls, these are processes. Ceiling per box is
# (extraction worker -c) x this value.
AGENTIC_TASK_CONCURRENCY     = int(os.environ.get("AGENTIC_TASK_CONCURRENCY", "4"))

# Anthropic prompt caching for extraction calls (anthropic/* models only).
# When on, DSPy passes cache_control_injection_points to LiteLLM so the
# system message becomes a cached prefix. Combined with warm-then-fan-out
# in Stage 2, cuts repeated input-token cost on multi-row tables.
EXTRACTION_PROMPT_CACHE      = os.environ.get("EXTRACTION_PROMPT_CACHE", "0") == "1"

# ── EVALUATION ────────────────────────────────────────────────────────
# core/evaluation.py • AsyncMedicalExtractionEvaluator
EVALUATION_PRIMARY_MODEL     = os.environ.get("EVALUATION_PRIMARY_MODEL",
                                               "gemini/gemini-2.5-flash")
EVALUATION_FALLBACK_MODELS   = ["openai/gpt-4o-mini",
                                  "anthropic/claude-haiku-4-5-20251001",
                                  "gemini/gemini-2.0-flash-exp"]
EVALUATION_MAX_TOKENS        = 4000
EVALUATION_TEMPERATURE       = 0.0   # must be 0 — deterministic matching
EVALUATION_CONCURRENCY       = 20    # simultaneous semantic comparisons

# ── CODE GENERATION ───────────────────────────────────────────────────
# LangGraph workflow • signature_gen.py • decomposition.py • module_gen.py
CODEGEN_DECOMPOSITION_MODEL  = os.environ.get("CODEGEN_DECOMPOSITION_MODEL",
                                               "anthropic/claude-sonnet-5")
CODEGEN_SIGNATURE_MODEL      = os.environ.get("CODEGEN_SIGNATURE_MODEL",
                                               "anthropic/claude-sonnet-5")
CODEGEN_MODULE_MODEL         = os.environ.get("CODEGEN_MODULE_MODEL",
                                               "anthropic/claude-sonnet-5")
CODEGEN_REVIEW_MODEL         = os.environ.get("CODEGEN_REVIEW_MODEL",
                                               "gemini/gemini-2.0-flash-exp")
CODEGEN_MAX_TOKENS           = 8000
CODEGEN_TEMPERATURE          = 0.3   # slightly creative for code gen
CODEGEN_MAX_ATTEMPTS         = 3
CODEGEN_MAX_DECOMPOSITION_TOKENS = 40000

# ── RATE LIMITS ───────────────────────────────────────────────────────
# Requests per minute — used by circuit_breaker.py for routing decisions
MODEL_RPM_LIMITS: dict = {
    "anthropic/claude-sonnet-5":           4000,
    "anthropic/claude-opus-4-6":           500,
    "anthropic/claude-haiku-4-5-20251001": 8000,
    "openai/gpt-4o":                        500,
    "openai/gpt-4o-mini":                  5000,
    "gemini/gemini-3-pro-preview":          360,
    "gemini/gemini-2.5-flash":             1500,
    "gemini/gemini-2.0-flash-exp":         1000,
    "bedrock/qwen.qwen3-next-80b-a3b":      200,
    "bedrock/openai.gpt-oss-20b-1:0":       200,
}

# ── PER-MODEL OUTPUT CEILINGS ─────────────────────────────────────────
# EXTRACTION_MAX_TOKENS is sized for the primary (Claude) model, but a request
# whose max_tokens exceeds a model's own output cap is a hard 400 — so asking
# GPT-4o for 64k output fails the request outright rather than degrading. Every
# LM is therefore built with min(requested, cap) — see circuit_breaker.py.
#
# Matched by longest prefix, so a family entry ("anthropic/claude-opus") covers
# every version without needing a row per release. Anything unmatched falls back
# to MODEL_MAX_OUTPUT_DEFAULT, which is deliberately conservative: an unknown
# model is far more likely to reject a large ceiling than to need one.
MODEL_MAX_OUTPUT_TOKENS: dict = {
    # Anthropic — 128k on the Sonnet-5 / Opus families, 64k on Haiku 4.5.
    "anthropic/claude-sonnet-5":   128000,
    "anthropic/claude-sonnet-4-6": 128000,
    "anthropic/claude-opus":       128000,
    "anthropic/claude-fable-5":    128000,
    "anthropic/claude-haiku":       64000,
    # OpenAI — gpt-4o is the hard constraint here at 16384.
    "openai/gpt-4o":                16384,
    "openai/gpt-5":                 64000,
    # Gemini — 2.0-flash-exp is 8192; 2.5+ is far higher.
    "gemini/gemini-2.0-flash-exp":   8192,
    "gemini/gemini-2.5":            65536,
    "gemini/gemini-3":              65536,
    # Bedrock-hosted third-party models: no documented parity, stay conservative.
    "bedrock/":                      8192,
}
MODEL_MAX_OUTPUT_DEFAULT = 8192


def reasoning_kwargs(model: str) -> dict:
    """Thinking + effort kwargs for `model`, or `{}` if it doesn't take them.

    `output_config` and `thinking` are Anthropic-only. Sending either to the
    OpenAI or Gemini fallback is a hard 400, and the fallback exists precisely
    for the moments the primary is failing — so an ungated kwarg would turn a
    recoverable outage into a dead run. One helper, so both LM constructors and
    any future call site share the same gate.

    Set EXTRACTION_THINKING=disabled to turn thinking off. Note that on Sonnet 5
    that is not a pure saving: with thinking off the model writes its reasoning
    into the visible answer instead (measured: 144 output tokens vs 57 for the
    same question), so lowering EXTRACTION_EFFORT is the better cost lever.
    """
    if not (model or "").startswith("anthropic/"):
        return {}
    kwargs: dict = {}
    if EXTRACTION_THINKING:
        kwargs["thinking"] = {"type": EXTRACTION_THINKING}
    if EXTRACTION_EFFORT:
        kwargs["output_config"] = {"effort": EXTRACTION_EFFORT}
    return kwargs


def resolve_max_output_tokens(model: str, requested: int) -> int:
    """Clamp `requested` to what `model` actually accepts as max_tokens.

    Longest-prefix match against MODEL_MAX_OUTPUT_TOKENS. Returning the
    clamped value (rather than raising) keeps a fallback model usable at its
    own ceiling instead of failing the whole request.
    """
    cap = MODEL_MAX_OUTPUT_DEFAULT
    best = -1
    for prefix, limit in MODEL_MAX_OUTPUT_TOKENS.items():
        if (model or "").startswith(prefix) and len(prefix) > best:
            cap, best = limit, len(prefix)
    return min(requested, cap)

# ── CIRCUIT BREAKER ───────────────────────────────────────────────────
# utils/circuit_breaker.py ModelCircuitBreaker
CB_ENABLED              = True
CB_FAILURE_THRESHOLD    = 3     # consecutive 429s before circuit opens
CB_RECOVERY_TIMEOUT     = 60    # seconds before retrying a tripped model
CB_HALF_OPEN_SUCCESSES  = 2     # successes needed to close circuit

# ── TIMEOUTS ──────────────────────────────────────────────────────────
LLM_TIMEOUT_SECONDS     = 600   # 10 min; generation jobs are long

# Per-LLM-call timeout for EXTRACTION, separate from the codegen figure above.
# Raising EXTRACTION_MAX_TOKENS without raising this turns truncation into
# failure: Sonnet 5 generates ~140 tok/s, so a call that actually uses 100k
# output tokens runs ~12 minutes and would be killed by litellm's 600s default
# mid-generation. Measured worst case at 64k was 453s (job 3ba0c293).
EXTRACTION_LLM_TIMEOUT_SECONDS = int(os.environ.get("EXTRACTION_LLM_TIMEOUT_SECONDS", "1500"))

# ── BACKWARD-COMPAT ALIASES ───────────────────────────────────────────
# These names are imported throughout existing code — do not rename them
DEFAULT_MODEL             = EXTRACTION_PRIMARY_MODEL
EVALUATION_MODEL          = EVALUATION_PRIMARY_MODEL
FALLBACK_MODELS           = EXTRACTION_FALLBACK_MODELS
EVALUATION_FALLBACK_MODELS_COMPAT = EVALUATION_FALLBACK_MODELS
MAX_TOKENS                = EXTRACTION_MAX_TOKENS
TEMPERATURE               = EXTRACTION_TEMPERATURE

# ── USER-FACING MODEL PICKER (Beta) ───────────────────────────────────
# Surfaced via GET /api/v1/settings/models. Saved per-user under
# user_settings.extraction_model and used by the extraction worker when
# starting a job. ANY id added here must have its API key configured
# (ANTHROPIC_API_KEY / OPENAI_API_KEY / GEMINI_API_KEY in env; bedrock/* needs
# AWS_BEARER_TOKEN_BEDROCK + AWS_REGION_NAME, or AWS_ACCESS_KEY_ID/SECRET + region
# on a principal with bedrock:InvokeModel — litellm picks these up automatically).
AVAILABLE_MODELS: list = [
    {"id": "anthropic/claude-sonnet-5",           "label": "Claude Sonnet 5",    "provider": "anthropic"},
    {"id": "anthropic/claude-haiku-4-5-20251001", "label": "Claude Haiku 4.5",   "provider": "anthropic"},
    {"id": "openai/gpt-5.5",                      "label": "GPT-5.5",            "provider": "openai"},
    {"id": "openai/gpt-5.4",                      "label": "GPT-5.4",            "provider": "openai"},
    {"id": "openai/gpt-5.4-mini",                 "label": "GPT-5.4 mini",       "provider": "openai"},
    {"id": "gemini/gemini-3.1-pro-preview",       "label": "Gemini 3.1 Pro (Preview)", "provider": "google"},
    {"id": "gemini/gemini-3.5-flash",             "label": "Gemini 3.5 Flash",   "provider": "google"},
    {"id": "gemini/gemini-2.5-pro",               "label": "Gemini 2.5 Pro",     "provider": "google"},
    {"id": "gemini/gemini-2.5-flash",             "label": "Gemini 2.5 Flash",   "provider": "google"},
    {"id": "bedrock/qwen.qwen3-next-80b-a3b",     "label": "Qwen3 Next 80B (Bedrock)", "provider": "bedrock"},
    {"id": "bedrock/openai.gpt-oss-20b-1:0",      "label": "GPT-OSS 20B (Bedrock)", "provider": "bedrock"},
]
AVAILABLE_MODEL_IDS: set = {m["id"] for m in AVAILABLE_MODELS}
