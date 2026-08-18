"""
Centralized LLM configuration for eviStreams.
All model initialization happens here with automatic fallback support.
"""

import dspy
import logging
from typing import Callable, Any, List, Optional
from dotenv import load_dotenv
from langchain.chat_models import init_chat_model

from core.config import (
    DEFAULT_MODEL,
    MAX_TOKENS,
    TEMPERATURE,
    FALLBACK_MODELS,
    EVALUATION_FALLBACK_MODELS,
    ENABLE_MODEL_FALLBACK
)
from config.models import CODEGEN_DECOMPOSITION_MODEL, EXTRACTION_PROMPT_CACHE

load_dotenv()
logger = logging.getLogger(__name__)

# Install CachingChatAdapter globally when prompt caching is enabled. The
# adapter is a no-op for signatures that don't contain the `row_anchor` field,
# so it's safe to use everywhere — only Stage 2 row calls actually get the
# content-block split with the cache breakpoint.
#
# The startup log is intentionally loud in both states. Silent "off" gave us a
# false sense the cache was working when the env var was missing from AWS
# Secrets Manager — make that failure mode visible at boot.
if EXTRACTION_PROMPT_CACHE:
    try:
        from utils.caching_adapter import CachingChatAdapter
        dspy.configure(adapter=CachingChatAdapter())
        active_adapter = type(dspy.settings.adapter).__name__ if dspy.settings.adapter else "None"
        logger.info(
            "EXTRACTION_PROMPT_CACHE=1 — installed CachingChatAdapter (active=%s)",
            active_adapter,
        )
    except Exception as _e:
        logger.warning("Failed to install CachingChatAdapter: %s", _e)
else:
    logger.warning(
        "EXTRACTION_PROMPT_CACHE=0 — Anthropic prompt caching DISABLED. "
        "Stage 2 fan-out will re-send the full paper on every row call. "
        "Set EXTRACTION_PROMPT_CACHE=1 (env var or AWS secret) to enable."
    )

try:
    from utils.gemini_json_unwrap import install_gemini_json_unwrap
    install_gemini_json_unwrap()
except Exception as _e:
    logger.warning("Failed to install Gemini JSON unwrap shim: %s", _e)


# Anthropic models from Claude Sonnet 5 / Opus 4.7 onward removed the sampling
# parameters (temperature / top_p / top_k). Sending any of them returns HTTP 400
# ("`temperature` is deprecated for this model"). Older Anthropic models
# (opus-4-6, sonnet-4-6, haiku-4-5) and every other provider still accept them.
# Extraction dodges this because EXTRACTION_TEMPERATURE is 1.0 (the accepted
# default), but codegen passes 0.2/0.3 — hence the decomposition failures.
_MODELS_REJECTING_SAMPLING_PARAMS = (
    "claude-sonnet-5",
    "claude-opus-4-7",
    "claude-opus-4-8",
    "claude-fable-5",
    "claude-mythos-5",
)


def _model_rejects_temperature(model_name: str) -> bool:
    """True for Anthropic models that 400 on any temperature/top_p/top_k."""
    m = (model_name or "").lower()
    return any(tag in m for tag in _MODELS_REJECTING_SAMPLING_PARAMS)


def retry_with_model_fallback(
    primary_model: str,
    fallback_models: List[str],
    operation: Callable,
    operation_name: str = "LLM operation",
    enable_fallback: bool = ENABLE_MODEL_FALLBACK,
    **kwargs
) -> Any:
    """
    Retry an operation with automatic model fallback on failure.

    Args:
        primary_model: Primary model to try first
        fallback_models: List of fallback models to try in order
        operation: Function to execute (receives model_name as first arg)
        operation_name: Name for logging
        enable_fallback: Whether to enable fallback (default from config)
        **kwargs: Additional arguments passed to operation

    Returns:
        Result from successful operation

    Raises:
        Exception: If all models fail
    """
    models_to_try = [primary_model]
    if enable_fallback:
        models_to_try.extend(fallback_models)

    last_error = None

    for idx, model in enumerate(models_to_try):
        try:
            logger.info(f"{operation_name}: Attempting with model {model} ({idx + 1}/{len(models_to_try)})")
            result = operation(model, **kwargs)

            if idx > 0:
                logger.warning(f"{operation_name}: Succeeded with fallback model {model} after {idx} failures")

            return result

        except Exception as e:
            last_error = e
            logger.error(f"{operation_name}: Failed with model {model}: {str(e)}")

            if idx < len(models_to_try) - 1:
                logger.info(f"{operation_name}: Trying fallback model...")
            else:
                logger.error(f"{operation_name}: All {len(models_to_try)} models failed")

    raise Exception(
        f"{operation_name} failed with all {len(models_to_try)} models. "
        f"Last error: {str(last_error)}"
    )


def get_dspy_model(
    model_name: str = DEFAULT_MODEL,
    max_tokens: int = MAX_TOKENS,
    temperature: float = TEMPERATURE,
    enable_fallback: bool = ENABLE_MODEL_FALLBACK,
    fallback_models: Optional[List[str]] = None
):
    """
    Get and configure DSPy model with automatic fallback support.

    Args:
        model_name: LLM model identifier
        max_tokens: Maximum tokens in response
        temperature: Sampling temperature
        enable_fallback: Enable automatic fallback to alternative models
        fallback_models: Custom fallback model list (default: FALLBACK_MODELS)

    Returns:
        Configured DSPy LM instance
    """
    if fallback_models is None:
        fallback_models = FALLBACK_MODELS

    def _create_dspy_model(model: str, max_tokens: int, temperature: float):
        # Belt-and-suspenders caching strategy:
        # 1. cache_control_injection_points (LiteLLM layer): proven-working
        #    fallback that injects cache_control on the LAST system content
        #    block regardless of whether our DSPy adapter is active in the
        #    current context. This is what actually delivered the 2:23 PM
        #    Stage 2 cost drop ($3.42 → $1.48).
        # 2. CachingChatAdapter installed at module load (dspy.configure):
        #    additionally places cache_control on the *paper* content for
        #    cross-signature scalar caching (Branch B) and at the row_anchor
        #    boundary for two-stage caching (Branch A). Requires the adapter
        #    to survive dspy.context() entry — handled in ModelRouter.
        # The two can coexist: Anthropic allows up to 4 cache breakpoints
        # per request, and duplicate markers on the same content are no-ops.
        extra: dict = {}
        if EXTRACTION_PROMPT_CACHE and model.startswith("anthropic/"):
            extra["cache_control_injection_points"] = [
                {"location": "message", "role": "system"},
            ]
        # Thinking + effort, gated to Anthropic (the fallbacks 400 on them).
        from config.models import EXTRACTION_LLM_TIMEOUT_SECONDS, reasoning_kwargs
        extra.update(reasoning_kwargs(model))
        extra.setdefault("timeout", EXTRACTION_LLM_TIMEOUT_SECONDS)
        lm = dspy.LM(model, max_tokens=max_tokens, temperature=temperature, **extra)
        # NOTE: We intentionally do NOT call dspy.configure(lm=lm) here.
        # The ModelRouter uses dspy.context(lm=...) per-coroutine for
        # concurrency-safe model switching. Calling dspy.configure() would
        # mutate global state and corrupt concurrent DSPy calls.
        return lm

    return retry_with_model_fallback(
        primary_model=model_name,
        fallback_models=fallback_models,
        operation=_create_dspy_model,
        operation_name="DSPy model initialization",
        enable_fallback=enable_fallback,
        max_tokens=max_tokens,
        temperature=temperature
    )


# Initialize DSPy with default settings on module load
try:
    get_dspy_model()
except Exception as e:
    logger.error(f"Failed to initialize default DSPy model: {e}")


def get_langchain_model(
    model_name: str = None,
    temperature: float = 0.2,
    max_tokens: int = 4000,
    enable_fallback: bool = ENABLE_MODEL_FALLBACK,
    fallback_models: Optional[List[str]] = None
):
    """
    Get configured LangChain model for code generation tasks with fallback support.

    Args:
        model_name: LLM model identifier
        temperature: Sampling temperature (0.0 = deterministic, 1.0 = creative)
        max_tokens: Maximum tokens in response
        enable_fallback: Enable automatic fallback to alternative models
        fallback_models: Custom fallback model list (default: FALLBACK_MODELS)

    Returns:
        Configured LangChain ChatModel instance
    """
    if model_name is None:
        model_name = CODEGEN_DECOMPOSITION_MODEL
    if fallback_models is None:
        fallback_models = FALLBACK_MODELS

    def _create_langchain_model(model: str, temperature: float, max_tokens: int):
        # Only pass `temperature` to models that still accept it. Sonnet 5 /
        # Opus 4.7+ / Fable 5 reject it with a 400; langchain-anthropic drops
        # any payload key whose value is None, so omitting it is the safe path.
        kwargs: dict = {"max_tokens": max_tokens}
        if not _model_rejects_temperature(model):
            kwargs["temperature"] = temperature

        # LangChain's init_chat_model doesn't understand "provider/model" format.
        # Split into model_provider and model_name.
        if "/" in model:
            provider, model_id = model.split("/", 1)
            # Map LiteLLM provider names to LangChain provider names
            provider_map = {
                "anthropic": "anthropic",
                "openai": "openai",
                "gemini": "google_genai",
                "google": "google_genai",
            }
            lc_provider = provider_map.get(provider, provider)
            return init_chat_model(
                model=model_id,
                model_provider=lc_provider,
                **kwargs,
            )
        return init_chat_model(model=model, **kwargs)

    return retry_with_model_fallback(
        primary_model=model_name,
        fallback_models=fallback_models,
        operation=_create_langchain_model,
        operation_name="LangChain model initialization",
        enable_fallback=enable_fallback,
        temperature=temperature,
        max_tokens=max_tokens
    )
