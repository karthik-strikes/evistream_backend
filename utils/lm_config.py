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
from config.models import CODEGEN_DECOMPOSITION_MODEL

load_dotenv()
logger = logging.getLogger(__name__)


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
        lm = dspy.LM(model, max_tokens=max_tokens, temperature=temperature)
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
                temperature=temperature,
                max_tokens=max_tokens,
            )
        return init_chat_model(
            model=model,
            temperature=temperature,
            max_tokens=max_tokens
        )

    return retry_with_model_fallback(
        primary_model=model_name,
        fallback_models=fallback_models,
        operation=_create_langchain_model,
        operation_name="LangChain model initialization",
        enable_fallback=enable_fallback,
        temperature=temperature,
        max_tokens=max_tokens
    )
