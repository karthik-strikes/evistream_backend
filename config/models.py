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
EXTRACTION_MAX_TOKENS        = 20000
EXTRACTION_TEMPERATURE       = 1.0
EXTRACTION_BATCH_CONCURRENCY = 5     # papers in parallel per job
EXTRACTION_MAX_JOBS_PER_USER = 10    # soft cap — users can queue up to 10, system processes as capacity allows
EXTRACTION_MAX_DOCS_PER_JOB  = 100
EXTRACTION_TASK_CONCURRENCY  = 350  # max simultaneous LLM calls across ALL papers AND stages

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

# ── CIRCUIT BREAKER ───────────────────────────────────────────────────
# utils/circuit_breaker.py ModelCircuitBreaker
CB_ENABLED              = True
CB_FAILURE_THRESHOLD    = 3     # consecutive 429s before circuit opens
CB_RECOVERY_TIMEOUT     = 60    # seconds before retrying a tripped model
CB_HALF_OPEN_SUCCESSES  = 2     # successes needed to close circuit

# ── TIMEOUTS ──────────────────────────────────────────────────────────
LLM_TIMEOUT_SECONDS     = 600   # 10 min; generation jobs are long

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
