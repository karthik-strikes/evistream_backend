"""
DSPy extraction wrapper with automatic model fallback.

Provides retry logic for DSPy signatures with automatic model switching on failure.
"""

import logging
import dspy
from typing import Any, Dict, Optional, List, Callable

from core.config import (
    DEFAULT_MODEL,
    FALLBACK_MODELS,
    EVALUATION_MODEL,
    EVALUATION_FALLBACK_MODELS,
    ENABLE_MODEL_FALLBACK,
    MAX_TOKENS,
    TEMPERATURE,
    EVALUATION_TEMPERATURE
)
from utils.circuit_breaker import _is_rate_limit_error

logger = logging.getLogger(__name__)


def call_dspy_with_fallback(
    dspy_callable: Callable,
    primary_model: str = DEFAULT_MODEL,
    fallback_models: Optional[List[str]] = None,
    max_tokens: int = MAX_TOKENS,
    temperature: float = TEMPERATURE,
    enable_fallback: bool = ENABLE_MODEL_FALLBACK,
    operation_name: str = "DSPy extraction",
    **dspy_kwargs
) -> Any:
    """
    Call DSPy signature/module with automatic model fallback on failure.

    Args:
        dspy_callable: DSPy signature or module to call
        primary_model: Primary model to try first
        fallback_models: List of fallback models (default: FALLBACK_MODELS)
        max_tokens: Maximum tokens in response
        temperature: Sampling temperature
        enable_fallback: Enable automatic fallback
        operation_name: Name for logging
        **dspy_kwargs: Arguments passed to DSPy callable

    Returns:
        Result from DSPy callable

    Raises:
        Exception: If all models fail

    Example:
        result = call_dspy_with_fallback(
            dspy_callable=my_signature,
            markdown_content=text,
            operation_name="Extract patient data"
        )
    """
    if fallback_models is None:
        fallback_models = FALLBACK_MODELS

    models_to_try = [primary_model]
    if enable_fallback:
        models_to_try.extend(fallback_models)

    last_error = None

    for idx, model in enumerate(models_to_try):
        try:
            logger.info(f"{operation_name}: Attempting with model {model} ({idx + 1}/{len(models_to_try)})")

            # Configure DSPy with this model using context (safe for concurrent use)
            lm = dspy.LM(model, max_tokens=max_tokens, temperature=temperature, num_retries=0)
            with dspy.context(lm=lm):
                result = dspy_callable(**dspy_kwargs)

            if idx > 0:
                logger.warning(
                    f"{operation_name}: Succeeded with fallback model {model} "
                    f"after {idx} failures"
                )

            return result

        except Exception as e:
            # Only switch models for rate limit errors (HTTP 429).
            # For other errors (bad API key, context too long, etc.) fail fast.
            if not _is_rate_limit_error(e):
                logger.error(
                    f"{operation_name}: Non-rate-limit error on {model}: "
                    f"{type(e).__name__}: {e}. Not trying fallback models."
                )
                raise
            last_error = e
            logger.warning(f"{operation_name}: Rate limit on {model}, trying next fallback model...")

            if idx < len(models_to_try) - 1:
                logger.info(f"{operation_name}: Trying fallback model...")
            else:
                logger.error(f"{operation_name}: All {len(models_to_try)} models failed")

    raise Exception(
        f"{operation_name} failed with all {len(models_to_try)} models. "
        f"Last error: {str(last_error)}"
    )


async def async_call_dspy_with_fallback(
    dspy_callable: Callable,
    primary_model: str = DEFAULT_MODEL,
    fallback_models: Optional[List[str]] = None,
    max_tokens: int = MAX_TOKENS,
    temperature: float = TEMPERATURE,
    enable_fallback: bool = ENABLE_MODEL_FALLBACK,
    operation_name: str = "Async DSPy extraction",
    **dspy_kwargs
) -> Any:
    """
    Async version of call_dspy_with_fallback.

    Args:
        dspy_callable: Async DSPy module to call
        primary_model: Primary model to try first
        fallback_models: List of fallback models (default: FALLBACK_MODELS)
        max_tokens: Maximum tokens in response
        temperature: Sampling temperature
        enable_fallback: Enable automatic fallback
        operation_name: Name for logging
        **dspy_kwargs: Arguments passed to DSPy callable

    Returns:
        Result from DSPy callable

    Raises:
        Exception: If all models fail
    """
    if fallback_models is None:
        fallback_models = FALLBACK_MODELS

    models_to_try = [primary_model]
    if enable_fallback:
        models_to_try.extend(fallback_models)

    last_error = None

    for idx, model in enumerate(models_to_try):
        try:
            logger.info(f"{operation_name}: Attempting with model {model} ({idx + 1}/{len(models_to_try)})")

            # Configure DSPy with this model using context (safe for concurrent use)
            lm = dspy.LM(model, max_tokens=max_tokens, temperature=temperature, num_retries=0)
            with dspy.context(lm=lm):
                result = await dspy_callable(**dspy_kwargs)

            if idx > 0:
                logger.warning(
                    f"{operation_name}: Succeeded with fallback model {model} "
                    f"after {idx} failures"
                )

            return result

        except Exception as e:
            # Only switch models for rate limit errors (HTTP 429).
            # For other errors (bad API key, context too long, etc.) fail fast.
            if not _is_rate_limit_error(e):
                logger.error(
                    f"{operation_name}: Non-rate-limit error on {model}: "
                    f"{type(e).__name__}: {e}. Not trying fallback models."
                )
                raise
            last_error = e
            logger.warning(f"{operation_name}: Rate limit on {model}, trying next fallback model...")

            if idx < len(models_to_try) - 1:
                logger.info(f"{operation_name}: Trying fallback model...")
            else:
                logger.error(f"{operation_name}: All {len(models_to_try)} models failed")

    raise Exception(
        f"{operation_name} failed with all {len(models_to_try)} models. "
        f"Last error: {str(last_error)}"
    )


def call_evaluation_with_fallback(
    evaluator_callable: Callable,
    enable_fallback: bool = ENABLE_MODEL_FALLBACK,
    **eval_kwargs
) -> Any:
    """
    Call DSPy evaluator with fallback optimized for evaluation models.

    Uses EVALUATION_MODEL and EVALUATION_FALLBACK_MODELS configuration.

    Args:
        evaluator_callable: Evaluation DSPy signature/module
        enable_fallback: Enable automatic fallback
        **eval_kwargs: Arguments passed to evaluator

    Returns:
        Evaluation result
    """
    return call_dspy_with_fallback(
        dspy_callable=evaluator_callable,
        primary_model=EVALUATION_MODEL,
        fallback_models=EVALUATION_FALLBACK_MODELS,
        temperature=EVALUATION_TEMPERATURE,
        enable_fallback=enable_fallback,
        operation_name="Evaluation",
        **eval_kwargs
    )
