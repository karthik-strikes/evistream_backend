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
                                               "anthropic/claude-sonnet-4-6")
EXTRACTION_FALLBACK_MODELS   = ["openai/gpt-4o", "gemini/gemini-2.0-flash-exp"]
EXTRACTION_MAX_TOKENS        = 20000
EXTRACTION_TEMPERATURE       = 1.0
EXTRACTION_BATCH_CONCURRENCY = 5     # papers in parallel per job
EXTRACTION_MAX_JOBS_PER_USER = 10    # soft cap — users can queue up to 10, system processes as capacity allows
EXTRACTION_MAX_DOCS_PER_JOB  = 100
EXTRACTION_TASK_CONCURRENCY  = 350  # max simultaneous LLM calls across ALL papers AND stages

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
                                               "anthropic/claude-sonnet-4-6")
CODEGEN_SIGNATURE_MODEL      = os.environ.get("CODEGEN_SIGNATURE_MODEL",
                                               "anthropic/claude-sonnet-4-6")
CODEGEN_MODULE_MODEL         = os.environ.get("CODEGEN_MODULE_MODEL",
                                               "anthropic/claude-sonnet-4-6")
CODEGEN_REVIEW_MODEL         = os.environ.get("CODEGEN_REVIEW_MODEL",
                                               "gemini/gemini-2.0-flash-exp")
CODEGEN_MAX_TOKENS           = 8000
CODEGEN_TEMPERATURE          = 0.3   # slightly creative for code gen
CODEGEN_MAX_ATTEMPTS         = 3
CODEGEN_MAX_DECOMPOSITION_TOKENS = 40000

# ── RATE LIMITS ───────────────────────────────────────────────────────
# Requests per minute — used by circuit_breaker.py for routing decisions
MODEL_RPM_LIMITS: dict = {
    "anthropic/claude-sonnet-4-6":        4000,
    "anthropic/claude-opus-4-6":           500,
    "anthropic/claude-haiku-4-5-20251001": 8000,
    "openai/gpt-4o":                        500,
    "openai/gpt-4o-mini":                  5000,
    "gemini/gemini-3-pro-preview":          360,
    "gemini/gemini-2.5-flash":             1500,
    "gemini/gemini-2.0-flash-exp":         1000,
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
