"""
True async DSPy forward pass using DSPy's native async support.

Replaces run_in_executor(None, sync_dspy_call) with cot_instance.acall(),
which is a genuinely async coroutine in DSPy 2.5+. Zero threads consumed.
"""

import logging

import dspy

from config.models import EXTRACTION_PROMPT_CACHE

logger = logging.getLogger(__name__)

# One-shot self-test: if the flag is on but the first N calls all show zero
# cache tokens, the wiring is broken (litellm too old, non-Anthropic fallback,
# prompt under the 1024-token Sonnet minimum, etc.). Warn once and stop checking.
_SELF_TEST_WINDOW = 5
_self_test_remaining = _SELF_TEST_WINDOW if EXTRACTION_PROMPT_CACHE else 0
_self_test_saw_cache = False
_self_test_warned = False


def _log_cache_usage(cot_instance=None, lm=None, tag: str = "") -> None:
    """Pull cache_creation/cache_read counts from the most recent LM history entry
    and emit ONE summary line per call. Quiet by design — the diagnostic CACHEDBG
    instrumentation has been removed now that the cache chain is proven.
    """
    global _self_test_remaining, _self_test_saw_cache, _self_test_warned

    if lm is None:
        lm = getattr(cot_instance, "lm", None) or dspy.settings.lm
    try:
        hist = getattr(lm, "history", None)
    except Exception:
        return
    if not isinstance(hist, list) or len(hist) == 0:
        return

    last = hist[-1]
    usage = None
    if isinstance(last, dict):
        resp = last.get("response")
        if hasattr(resp, "get"):
            try:
                usage = resp.get("usage")
            except Exception:
                usage = None
        if usage is None:
            usage = last.get("usage")
    if not isinstance(usage, dict):
        # LiteLLM ModelResponse exposes .usage as a pydantic-style object
        resp_obj = last.get("response") if isinstance(last, dict) else None
        if hasattr(resp_obj, "usage"):
            try:
                u_obj = resp_obj.usage
                if hasattr(u_obj, "model_dump"):
                    usage = u_obj.model_dump()
                elif hasattr(u_obj, "dict"):
                    usage = u_obj.dict()
                else:
                    usage = dict(u_obj) if u_obj is not None else None
            except Exception:
                usage = None
    if not isinstance(usage, dict):
        return

    cc = usage.get("cache_creation_input_tokens", 0) or 0
    cr = usage.get("cache_read_input_tokens", 0) or 0
    if not cr:
        # OpenAI/Gemini report cache reads under prompt_tokens_details.cached_tokens
        # instead of the Anthropic-shaped top-level field.
        details = usage.get("prompt_tokens_details") or {}
        if isinstance(details, dict):
            cr = details.get("cached_tokens", 0) or 0

    if cc or cr:
        logger.info("prompt_cache write=%d read=%d", cc, cr)
        _self_test_saw_cache = True

    if _self_test_remaining > 0:
        _self_test_remaining -= 1
        if _self_test_remaining == 0 and not _self_test_saw_cache and not _self_test_warned:
            _self_test_warned = True
            logger.warning(
                "prompt_cache self-test FAILED: EXTRACTION_PROMPT_CACHE=1 but "
                "first %d calls reported zero cache tokens. Likely causes: "
                "(a) prompt below 1024-token Sonnet minimum, "
                "(b) non-Anthropic provider in active fallback, "
                "(c) litellm too old for cache_control_injection_points (bump to recent), "
                "(d) CachingChatAdapter not preserved across dspy.context() (check ModelRouter).",
                _SELF_TEST_WINDOW,
            )


async def async_dspy_forward(cot_instance, **inputs) -> dict:
    """
    Replace run_in_executor(None, dspy_call) with truly async cot_instance.acall().

    DSPy 2.5+ exposes native async via ChainOfThought.acall() / Predict.acall(),
    which calls litellm.acompletion internally — no thread pool needed.

    Args:
        cot_instance: A dspy.ChainOfThought (or dspy.Predict) instance
        **inputs: Input field values (markdown_content, + any required upstream fields)

    Returns:
        dspy.Prediction — dict-like, supports .get("field", default)
    """
    result = await cot_instance.acall(**inputs)
    if EXTRACTION_PROMPT_CACHE:
        _log_cache_usage(cot_instance)
    return result


__all__ = ["async_dspy_forward", "_log_cache_usage"]
