"""
True async DSPy forward pass using DSPy's native async support.

Replaces run_in_executor(None, sync_dspy_call) with cot_instance.acall(),
which is a genuinely async coroutine in DSPy 2.5+. Zero threads consumed.
"""

import dspy


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
    return await cot_instance.acall(**inputs)


__all__ = ["async_dspy_forward"]
