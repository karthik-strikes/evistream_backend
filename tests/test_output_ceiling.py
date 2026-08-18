"""Output-token ceiling: per-model clamping and truncation detection.

Context: EXTRACTION_MAX_TOKENS was 20000, which a wide grounded table exhausts
(~670 output tokens per 11-column row with verbatim per-cell quotes → truncation
at ~29 rows). The cutoff was silent: the parser salvaged whole rows and dropped
the rest, so a truncated table was indistinguishable from a short one. Raising
the ceiling required per-model clamping, because several fallback/picker models
cap output far below the primary's limit and a too-large max_tokens is a hard
400, not a soft degrade.
"""

import sys
import os

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from config.models import (  # noqa: E402
    EXTRACTION_MAX_TOKENS,
    MODEL_MAX_OUTPUT_DEFAULT,
    resolve_max_output_tokens,
)
from utils import absence  # noqa: E402
from utils.dspy_async import was_truncated  # noqa: E402


# --------------------------------------------------------------------------- #
# Per-model clamping
# --------------------------------------------------------------------------- #

class TestResolveMaxOutputTokens:
    @pytest.mark.parametrize("model,expected", [
        # Primary and Opus family accept the full requested ceiling.
        ("anthropic/claude-sonnet-5", 64000),
        ("anthropic/claude-opus-5", 64000),
        ("anthropic/claude-haiku-4-5-20251001", 64000),   # 64k cap == request
        # The two configured extraction fallbacks — both below the primary.
        ("openai/gpt-4o", 16384),
        ("gemini/gemini-2.0-flash-exp", 8192),
        # Higher-capacity Gemini.
        ("gemini/gemini-2.5-flash", 64000),
        # Bedrock-hosted third-party models stay conservative.
        ("bedrock/qwen.qwen3-next-80b-a3b", 8192),
        ("bedrock/openai.gpt-oss-20b-1:0", 8192),
    ])
    def test_clamps_to_model_cap(self, model, expected):
        assert resolve_max_output_tokens(model, 64000) == expected

    def test_unknown_model_gets_conservative_default(self):
        """An unrecognized model is likelier to reject a big ceiling than to need
        one, so the default must be low rather than the requested value."""
        assert resolve_max_output_tokens("some/never-seen", 64000) == MODEL_MAX_OUTPUT_DEFAULT

    def test_longest_prefix_wins(self):
        """Family entries must not shadow a more specific one. 'anthropic/claude-haiku'
        (64k) is a distinct, lower cap than the sonnet/opus families (128k)."""
        assert resolve_max_output_tokens("anthropic/claude-haiku-4-5", 128000) == 64000
        assert resolve_max_output_tokens("anthropic/claude-sonnet-5", 128000) == 128000

    def test_never_raises_a_smaller_request(self):
        """Clamping is one-directional — asking for less than the cap keeps less."""
        assert resolve_max_output_tokens("anthropic/claude-sonnet-5", 4096) == 4096

    def test_configured_ceiling_is_the_raised_one(self):
        """Guards the regression this whole module exists for: a 20000 ceiling
        truncates wide grounded tables at ~29 rows."""
        assert EXTRACTION_MAX_TOKENS > 20000

    def test_every_configured_extraction_model_is_capped_safely(self):
        """The primary + both fallbacks must each resolve to something their API
        will accept, or the request 400s instead of degrading."""
        from config.models import DEFAULT_MODEL, FALLBACK_MODELS
        for model in [DEFAULT_MODEL, *FALLBACK_MODELS]:
            got = resolve_max_output_tokens(model, EXTRACTION_MAX_TOKENS)
            assert 0 < got <= EXTRACTION_MAX_TOKENS


class TestModelRouterClamps:
    def test_lm_cache_uses_per_model_ceiling(self):
        """ModelRouter pre-builds one LM per model; each must carry its own cap,
        not the primary's."""
        import utils.circuit_breaker as cb
        router = cb.ModelRouter(
            primary_model="anthropic/claude-sonnet-5",
            fallback_models=["openai/gpt-4o", "gemini/gemini-2.0-flash-exp"],
            max_tokens=64000,
            temperature=1.0,
            failure_threshold=3,
            recovery_timeout=60,
            half_open_successes=2,
        )
        assert router._lm_cache["anthropic/claude-sonnet-5"].kwargs["max_tokens"] == 64000
        assert router._lm_cache["openai/gpt-4o"].kwargs["max_tokens"] == 16384
        assert router._lm_cache["gemini/gemini-2.0-flash-exp"].kwargs["max_tokens"] == 8192

    def test_lazily_registered_picker_model_is_clamped(self):
        """The Settings model picker can select a low-cap model that was never in
        the constructor list — that path must clamp too."""
        import utils.circuit_breaker as cb
        router = cb.ModelRouter(
            primary_model="anthropic/claude-sonnet-5",
            fallback_models=[],
            max_tokens=64000,
            temperature=1.0,
            failure_threshold=3,
            recovery_timeout=60,
            half_open_successes=2,
        )
        router._ensure_model_registered("bedrock/qwen.qwen3-next-80b-a3b")
        lm = router._lm_cache["bedrock/qwen.qwen3-next-80b-a3b"]
        assert lm.kwargs["max_tokens"] == 8192


# --------------------------------------------------------------------------- #
# Truncation detection
# --------------------------------------------------------------------------- #

class _FakeLM:
    def __init__(self, finish_reason=None, history=True):
        if not history:
            self.history = []
        else:
            self.history = [{"response": {"choices": [{"finish_reason": finish_reason}]}}]


class TestWasTruncated:
    @pytest.mark.parametrize("reason", ["length", "max_tokens", "LENGTH", "MAX_TOKENS"])
    def test_detects_truncation(self, reason):
        """litellm normalizes Anthropic's 'max_tokens' to OpenAI's 'length', but
        accept both rather than depend on that mapping holding."""
        assert was_truncated(lm=_FakeLM(reason)) is True

    @pytest.mark.parametrize("reason", ["stop", "end_turn", "tool_use", None, ""])
    def test_normal_completions_are_not_truncated(self, reason):
        assert was_truncated(lm=_FakeLM(reason)) is False

    def test_no_history_is_not_truncated(self):
        """Never guess a truncation that didn't happen — an unreadable finish
        reason must read as 'not truncated', not as an error."""
        assert was_truncated(lm=_FakeLM(history=False)) is False

    def test_malformed_history_is_not_truncated(self):
        class Broken:
            history = [{"no_response_key": 1}]
        assert was_truncated(lm=Broken()) is False


class TestExtractorMarksTruncatedOutputPartial:
    """The load-bearing behavior: a truncated table must not reach a reviewer
    labelled `reported`, because it looks like a complete short table."""

    def _run(self, truncated):
        import asyncio
        from unittest.mock import patch
        import dspy_components.runtime_builders as rb

        sig_def = {
            "class_name": "TruncSig",
            "docstring": "Extract a table.",
            "input_fields": [{"name": "markdown_content", "type": "str", "desc": "paper"}],
            "output_fields": [{
                "name": "rows", "type": "Dict[str, Any]", "description": "table",
                "source_grounded": True,
                "subform_fields": [{"field_name": "c1", "field_type": "text",
                                    "field_description": "col"}],
            }],
        }
        schema_def = {
            "task_name": "trunc", "signatures": [sig_def],
            "pipeline_stages": [{"stage": 1, "signatures": ["TruncSig"], "requires_fields": []}],
            "fallback_structures": {},
        }
        ExtractorCls = rb.build_schema_classes(schema_def, "trunc")["TruncSig"]

        async def fake_forward(predictor, **kwargs):
            return {"rows": {"value": [{"c1": {"value": "x", "source_text": "x"}}],
                             "source_text": "caption"}}

        async def go():
            with patch.object(rb, "async_dspy_forward", side_effect=fake_forward), \
                 patch.object(rb, "was_truncated", return_value=truncated):
                return await ExtractorCls()("paper text")

        return asyncio.run(go())["rows"]

    def test_truncated_output_is_partial_and_flagged(self):
        env = self._run(truncated=True)
        assert env["status"] == absence.PARTIAL
        assert env["truncated"] is True
        # The salvaged rows are real data — flagging must not discard them.
        assert isinstance(env["value"], list) and len(env["value"]) == 1

    def test_untruncated_output_is_unchanged(self):
        env = self._run(truncated=False)
        assert env["status"] == absence.REPORTED
        assert "truncated" not in env
