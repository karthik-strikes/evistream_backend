"""Unit tests for the two-stage (row-first + per-column) composite extractor.

Tests cover:
- Factory selection in build_schema_classes
- Stage 1 → Stage 2 execution and result zipping
- Failure paths (Stage 1 empty, Stage 2 column failure, short Stage 2 list)
- Backward compat: sigs without extraction_strategy use single-call path
"""

import asyncio
import json
import sys
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ---------------------------------------------------------------------------
# Minimal schema_def fixtures
# ---------------------------------------------------------------------------

def _make_sig_def(extraction_strategy=None, anchor_columns=None, subform_fields=None):
    """Build a minimal schema_def signature with one List output field."""
    of = {
        "name": "results",
        "type": "List[Dict[str, Any]]",
        "description": "Test table field.",
        "subform_fields": subform_fields or [],
    }
    if extraction_strategy:
        of["extraction_strategy"] = extraction_strategy
    if anchor_columns is not None:
        of["anchor_columns"] = anchor_columns
    return {
        "class_name": "TestSig",
        "docstring": "Test signature.",
        "input_fields": [{"name": "markdown_content", "type": "str", "desc": "paper text"}],
        "output_fields": [of],
    }


def _make_schema_def(sig_def):
    return {
        "task_name": "test",
        "signatures": [sig_def],
        "pipeline_stages": [{"stage": 1, "signatures": ["TestSig"], "requires_fields": []}],
        "fallback_structures": {},
    }


SUBFIELDS = [
    {"field_name": "outcome_name", "field_type": "text", "field_description": "Name", "hints": [], "rules": []},
    {"field_name": "followup_point", "field_type": "text", "field_description": "Timepoint", "hints": [], "rules": []},
    {"field_name": "variability_sd", "field_type": "text", "field_description": "SD", "hints": ["find SD"], "rules": ["NR if absent"]},
    {"field_name": "central_value", "field_type": "text", "field_description": "Mean", "hints": [], "rules": []},
]


# ---------------------------------------------------------------------------
# Factory selection
# ---------------------------------------------------------------------------

class TestFactorySelection:
    def test_two_stage_factory_selected(self):
        """build_schema_classes returns a _is_two_stage factory for row_then_columns."""
        import dspy  # noqa: must be importable
        from dspy_components.runtime_builders import build_schema_classes

        sig_def = _make_sig_def(
            extraction_strategy="row_then_columns",
            anchor_columns=["outcome_name", "followup_point"],
            subform_fields=SUBFIELDS,
        )
        factories = build_schema_classes(_make_schema_def(sig_def), "test")
        cls = factories["TestSig"]
        assert getattr(cls, "_is_two_stage", False) is True
        assert cls._field_name == "results"
        assert set(cls._anchor_cols) == {"outcome_name", "followup_point"}
        assert set(cls._value_cols) == {"variability_sd", "central_value"}

    def test_single_call_factory_when_no_strategy(self):
        """build_schema_classes falls back to single-call when no extraction_strategy."""
        from dspy_components.runtime_builders import build_schema_classes

        sig_def = _make_sig_def(subform_fields=SUBFIELDS)
        factories = build_schema_classes(_make_schema_def(sig_def), "test")
        cls = factories["TestSig"]
        assert getattr(cls, "_is_two_stage", False) is False

    def test_single_call_factory_when_no_anchors(self):
        """Misconfigured row_then_columns (empty anchor_columns) falls back to single-call."""
        from dspy_components.runtime_builders import build_schema_classes

        sig_def = _make_sig_def(
            extraction_strategy="row_then_columns",
            anchor_columns=[],
            subform_fields=SUBFIELDS,
        )
        factories = build_schema_classes(_make_schema_def(sig_def), "test")
        assert getattr(factories["TestSig"], "_is_two_stage", False) is False

    def test_single_call_factory_for_mixed_sig(self):
        """A sig with 2 output fields skips two-stage even if first is row_then_columns."""
        from dspy_components.runtime_builders import build_schema_classes

        sig_def = _make_sig_def(
            extraction_strategy="row_then_columns",
            anchor_columns=["outcome_name"],
            subform_fields=SUBFIELDS,
        )
        # Add a second output field
        sig_def["output_fields"].append({"name": "study_id", "type": "Dict[str, Any]", "description": "ID"})
        factories = build_schema_classes(_make_schema_def(sig_def), "test")
        assert getattr(factories["TestSig"], "_is_two_stage", False) is False


# ---------------------------------------------------------------------------
# Execution logic
# ---------------------------------------------------------------------------

class TestTwoStageExecution:
    """Patch async_dspy_forward so we never hit a real LLM."""

    def _make_extractor(self):
        from dspy_components.runtime_builders import build_two_stage_extractor_class
        sig_def = _make_sig_def(
            extraction_strategy="row_then_columns",
            anchor_columns=["outcome_name", "followup_point"],
            subform_fields=SUBFIELDS,
        )
        return build_two_stage_extractor_class(sig_def, sig_def["output_fields"][0], "test")

    def test_stage1_and_stage2_called_correctly(self):
        ExtractorCls = self._make_extractor()

        s1_rows = [
            {"outcome_name": {"value": "Pain VAS", "source_text": "Pain VAS"}, "followup_point": {"value": "7 days", "source_text": "7 days"}},
            {"outcome_name": {"value": "Bone level", "source_text": "Bone level"}, "followup_point": {"value": "6 months", "source_text": "6 months"}},
        ]
        sd_vals = [{"value": "12", "source_text": "SD 12"}, {"value": "0.1", "source_text": "SD 0.1"}]
        cv_vals = [{"value": "34", "source_text": "mean 34"}, {"value": "0.3", "source_text": "mean 0.3"}]

        async def mock_forward(predictor, **kwargs):
            if "row_anchors" not in kwargs:
                # Stage 1 call
                return {"results": s1_rows}
            # Stage 2 — return both cols; each _run_col picks its own key
            return {"variability_sd": sd_vals, "central_value": cv_vals}

        async def run():
            with patch("dspy_components.runtime_builders.async_dspy_forward", side_effect=mock_forward):
                extractor = ExtractorCls()
                return await extractor("paper text here")

        result = asyncio.get_event_loop().run_until_complete(run())
        rows = result["results"]

        assert len(rows) == 2
        assert rows[0]["outcome_name"]["value"] == "Pain VAS"
        assert rows[1]["followup_point"]["value"] == "6 months"
        assert rows[0]["variability_sd"]["value"] == "12"
        assert rows[1]["variability_sd"]["value"] == "0.1"
        assert rows[0]["central_value"]["value"] == "34"

    def test_stage1_empty_returns_empty(self):
        ExtractorCls = self._make_extractor()

        async def mock_forward(predictor, **kwargs):
            return {"results": []}

        async def run():
            with patch("dspy_components.runtime_builders.async_dspy_forward", side_effect=mock_forward):
                return await ExtractorCls()("paper")

        result = asyncio.get_event_loop().run_until_complete(run())
        assert result == {"results": []}

    def test_stage1_exception_returns_empty(self):
        ExtractorCls = self._make_extractor()

        async def mock_forward(predictor, **kwargs):
            raise RuntimeError("LLM timeout")

        async def run():
            with patch("dspy_components.runtime_builders.async_dspy_forward", side_effect=mock_forward):
                return await ExtractorCls()("paper")

        result = asyncio.get_event_loop().run_until_complete(run())
        assert result == {"results": []}

    def test_stage2_column_failure_fills_nr(self):
        """All Stage 2 columns fail → every value col is NR for every row."""
        ExtractorCls = self._make_extractor()

        s1_rows = [{"outcome_name": "Pain VAS", "followup_point": "7 days"}]

        async def mock_forward(predictor, **kwargs):
            if "row_anchors" not in kwargs:
                return {"results": s1_rows}
            raise RuntimeError("stage 2 failure")

        async def run():
            with patch("dspy_components.runtime_builders.async_dspy_forward", side_effect=mock_forward):
                return await ExtractorCls()("paper")

        result = asyncio.get_event_loop().run_until_complete(run())
        rows = result["results"]
        assert len(rows) == 1
        assert rows[0]["variability_sd"] == {"value": "NR", "source_text": "NR"}
        assert rows[0]["central_value"] == {"value": "NR", "source_text": "NR"}

    def test_stage2_short_list_fills_nr(self):
        """Stage 2 returns fewer values than rows → tail rows get NR for that col."""
        ExtractorCls = self._make_extractor()

        s1_rows = [
            {"outcome_name": "A", "followup_point": "7d"},
            {"outcome_name": "B", "followup_point": "30d"},
        ]

        async def mock_forward(predictor, **kwargs):
            if "row_anchors" not in kwargs:
                return {"results": s1_rows}
            # Return only 1 value for 2 rows for all Stage 2 cols
            return {
                "variability_sd": [{"value": "5", "source_text": "5"}],
                "central_value": [{"value": "10", "source_text": "10"}, {"value": "20", "source_text": "20"}],
            }

        async def run():
            with patch("dspy_components.runtime_builders.async_dspy_forward", side_effect=mock_forward):
                return await ExtractorCls()("paper")

        result = asyncio.get_event_loop().run_until_complete(run())
        rows = result["results"]
        assert len(rows) == 2
        assert rows[0]["variability_sd"]["value"] == "5"
        assert rows[1]["variability_sd"] == {"value": "NR", "source_text": "NR"}
        assert rows[1]["central_value"]["value"] == "20"
