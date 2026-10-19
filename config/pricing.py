"""Per-model USD pricing for cost computation.

LiteLLM populates `cost` natively for DSPy extraction calls. LangChain
codegen calls leave `cost=0` — we fill it in using this table when serving
the /usage endpoints. Unknown models stay at 0 and surface as "?" in the UI.

Rates are USD per 1M tokens (list prices as of 2026-06). Update when
provider pricing changes.
"""

from __future__ import annotations

from typing import Dict

# {model_name: (input_per_1m, output_per_1m)}
PRICING: Dict[str, Dict[str, float]] = {
    # Anthropic
    "anthropic/claude-opus-4-7":       {"in": 15.0,  "out": 75.0},
    "anthropic/claude-opus-4":         {"in": 15.0,  "out": 75.0},
    "anthropic/claude-sonnet-5":       {"in":  3.0,  "out": 15.0},
    "anthropic/claude-sonnet-4-6":     {"in":  3.0,  "out": 15.0},
    "anthropic/claude-sonnet-4":       {"in":  3.0,  "out": 15.0},
    "anthropic/claude-haiku-4-5":      {"in":  0.8,  "out":  4.0},
    "anthropic/claude-haiku-4":        {"in":  0.8,  "out":  4.0},
    # OpenAI
    "openai/gpt-4o":                   {"in":  2.5,  "out": 10.0},
    "openai/gpt-4o-mini":              {"in":  0.15, "out":  0.6},
    "openai/gpt-4-turbo":              {"in": 10.0,  "out": 30.0},
    # Google
    "gemini/gemini-2.0-flash":         {"in":  0.075,"out":  0.30},
    "gemini/gemini-2.5-pro":           {"in":  1.25, "out":  5.0},
    "gemini/gemini-2.5-flash":         {"in":  0.30, "out":  2.50},
    "gemini/gemini-3.5-flash":         {"in":  0.30, "out":  2.50},
    "gemini/gemini-3.1-pro":           {"in":  1.25, "out":  5.0},
    "gemini/gemini-3.1-pro-preview":   {"in":  1.25, "out":  5.0},
    # OpenAI open-weight (via AWS Bedrock)
    "bedrock/openai.gpt-oss-20b-1:0":  {"in":  0.07, "out":  0.30},
}


def compute_cost(
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    cache_creation_input_tokens: int = 0,
    cache_read_input_tokens: int = 0,
) -> float:
    """Return USD cost for a single call. 0 if model not in PRICING.

    Anthropic prompt-cache pricing (5-min TTL): cache writes billed at 1.25x
    the input rate, cache reads at 0.1x. prompt_tokens here is the residual
    non-cached input portion as reported by LiteLLM, so the three terms are
    additive — no double counting.
    """
    if not model:
        return 0.0
    # Normalize bare model name (LangChain sometimes returns "claude-opus-4" without provider).
    candidates = [model, model.lower()]
    for prov in ("anthropic", "openai", "gemini"):
        candidates.append(f"{prov}/{model}")
        candidates.append(f"{prov}/{model.lower()}")
    rates = next((PRICING[c] for c in candidates if c in PRICING), None)
    if not rates:
        return 0.0
    in_rate = rates["in"]
    out_rate = rates["out"]
    return (
        prompt_tokens * in_rate
        + completion_tokens * out_rate
        + cache_creation_input_tokens * in_rate * 1.25
        + cache_read_input_tokens * in_rate * 0.1
    ) / 1_000_000


def is_priced(model: str) -> bool:
    return compute_cost(model, 1, 0) > 0 or compute_cost(model, 0, 1) > 0


def is_row_priced(row: Dict[str, float]) -> bool:
    """True if we have any cost signal for this row.

    A row counts as priced when either:
      - LiteLLM (or any other writer) pre-filled `cost` with a positive value, OR
      - the model name is in our local PRICING table so we can compute cost.

    This is the right metric for the "unpriced calls" counter — `is_priced(model)`
    alone is misleading because LiteLLM often prices models we don't know about.
    """
    if float(row.get("cost") or 0) > 0:
        return True
    return is_priced(row.get("model") or "")
