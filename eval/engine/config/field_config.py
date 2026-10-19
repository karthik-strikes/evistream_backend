"""
Shared types + strategy constants for the eval pipeline.

Per-form field lists + metadata live in each `forms/<form>.py` module.
The assembled `FORM_REGISTRY` is exposed from `forms/__init__.py`.
"""

from dataclasses import dataclass
from typing import Optional

# Papers used during prompt development / calibration.
# If non-empty, these are excluded from held-out eval to avoid train/eval contamination.
PROMPT_DEV_PAPERS: list[str] = []

STRATEGY_EXACT_NORMALIZE    = "exact_normalize"
STRATEGY_NUMERIC_EXACT      = "numeric_exact"
STRATEGY_NUMERIC_TOLERANCE  = "numeric_tolerance"
STRATEGY_DURATION           = "duration_normalize"   # unit-aware duration compare (months/weeks/days)
STRATEGY_DATE_RANGE         = "date_range_parse"
STRATEGY_SET_TERMS          = "set_compare_terms"
STRATEGY_PARSE_COUNTS       = "parse_counts"
STRATEGY_PARSE_PERCENT      = "parse_percent"
STRATEGY_LLM_JUDGE          = "llm_judge"
STRATEGY_SKIP               = "skip"
STRATEGY_EXCLUDE            = "exclude"   # broken/incomplete data, never scored


@dataclass
class FieldSpec:
    ai_col:      str
    strategy:    str
    tolerance:   float           = 0.0
    vocab:       Optional[str]   = None    # key in key_term_vocab.py
