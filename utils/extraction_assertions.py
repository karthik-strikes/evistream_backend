"""
DSPy extraction validation using dspy.Refine (DSPy 3.x).

Provides:
- extraction_reward(): Reward function for dspy.Refine that scores extraction quality
- validate_extraction_output(): Standalone validation for use outside dspy.Refine

Key design principle:
    "NR" (Not Reported) is a VALID answer when the paper doesn't contain
    the information. We must NOT penalize NR so heavily that the LLM is
    pressured into hallucinating values. We only penalize truly empty fields
    (None, blank) and broken JSON. NR gets partial credit.

dspy.Refine wraps a ChainOfThought module, runs it up to N times,
and returns the best result above a threshold. When results score poorly,
it automatically generates feedback for the next attempt.

Usage in extractor modules:
    from utils.extraction_assertions import extraction_reward

    class AsyncFooExtractor(dspy.Module):
        def __init__(self):
            super().__init__()
            cot = dspy.ChainOfThought(FooSignature)
            self.extract = dspy.Refine(
                module=cot,
                N=3,
                reward_fn=extraction_reward,
                threshold=0.5,
            )
"""

import json
import logging
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)

# Values considered as "not reported" / empty
NR_INDICATORS = frozenset({
    "nr", "n/a", "na", "not reported", "not available",
    "unknown", "unclear", "none", "",
})

# JSON strings that represent empty data
EMPTY_JSON_STRINGS = frozenset({
    "{}", "[]",
    '{"value": "nr"}',
    '{"value": "nr", "source_text": "nr"}',
    '{"value": "NR"}',
    '{"value": "NR", "source_text": "NR"}',
})


def _is_nr(value_str: str) -> bool:
    """Check if a string value represents Not Reported."""
    lower = value_str.lower()
    return lower in NR_INDICATORS or lower in EMPTY_JSON_STRINGS


def _is_valid_json(value_str: str) -> bool:
    """Check if a JSON-like string is valid JSON."""
    try:
        json.loads(value_str)
        return True
    except (json.JSONDecodeError, ValueError):
        return False


def extraction_reward(args: dict, pred) -> float:
    """
    Reward function for dspy.Refine.

    Scoring philosophy:
    - Substantive value (real data + valid JSON if applicable) → full credit (1.0)
    - NR / Not Reported → partial credit (0.7) — the LLM made a deliberate decision
    - Empty / None → no credit (0.0) — the LLM failed to process the field
    - Invalid JSON → no credit (0.0) — the output is broken

    This prevents the LLM from being pressured to hallucinate when
    a paper genuinely lacks information for certain fields.

    Args:
        args: Input arguments dict (markdown_content, etc.)
        pred: DSPy Prediction object with output fields

    Returns:
        Float score between 0.0 and 1.0
    """
    output_fields = [
        k for k in vars(pred).keys()
        if not k.startswith("_")
    ]

    if not output_fields:
        return 0.0

    field_scores = []

    for field in output_fields:
        value = getattr(pred, field, None)

        # Empty / None → 0.0
        if value is None or str(value).strip() == "":
            field_scores.append(0.0)
            continue

        value_str = str(value).strip()

        # NR → 0.7 (valid deliberate answer, not a failure)
        if _is_nr(value_str):
            field_scores.append(0.7)
            continue

        # JSON-like → check validity
        if value_str.startswith("{") or value_str.startswith("["):
            if _is_valid_json(value_str):
                field_scores.append(1.0)  # Valid JSON with data
            else:
                field_scores.append(0.0)  # Broken JSON
            continue

        # Substantive non-JSON value → full credit
        field_scores.append(1.0)

    return sum(field_scores) / len(field_scores)


def validate_extraction_output(
    result: Dict[str, Any],
    expected_fields: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    Standalone validation for extraction results (used at pipeline level).

    Scoring philosophy:
    - score: proportion of fields with substantive data (0.0-1.0)
    - all_failed: True only when EVERY field is empty/NR (extractor totally failed)

    The pipeline should retry only when all_failed=True, not based on score,
    because a low score with some real data means the paper simply doesn't
    contain all the information — that's normal, not a failure.

    Args:
        result: Extraction result dict
        expected_fields: Optional list of expected field names

    Returns:
        Dict with:
        - score: float 0.0-1.0 (substantive fields / total)
        - all_failed: bool — True if zero fields have real data
        - empty_fields: list of field names with no value
        - nr_fields: list of field names with NR-like values
        - invalid_json_fields: list of fields with malformed JSON
        - substantive_fields: list of fields with real data
    """
    if not isinstance(result, dict):
        return {"score": 0.0, "all_failed": True, "empty_fields": [],
                "nr_fields": [], "invalid_json_fields": [], "substantive_fields": []}

    fields = expected_fields or list(result.keys())

    empty_fields = []
    nr_fields = []
    invalid_json_fields = []
    substantive_fields = []

    for field in fields:
        value = result.get(field)

        if value is None or str(value).strip() == "":
            empty_fields.append(field)
            continue

        value_str = str(value).strip()

        if _is_nr(value_str):
            nr_fields.append(field)
            continue

        # Check JSON validity for JSON-like strings
        if isinstance(value, str) and (value_str.startswith("{") or value_str.startswith("[")):
            if not _is_valid_json(value_str):
                invalid_json_fields.append(field)
                continue

        substantive_fields.append(field)

    total = len(fields)
    score = len(substantive_fields) / total if total > 0 else 0.0

    return {
        "score": score,
        "all_failed": len(substantive_fields) == 0,
        "empty_fields": empty_fields,
        "nr_fields": nr_fields,
        "invalid_json_fields": invalid_json_fields,
        "substantive_fields": substantive_fields,
    }
