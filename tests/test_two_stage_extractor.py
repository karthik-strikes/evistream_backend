"""Unit tests for the two-stage (anchor discovery + per-row) extractor.

Tests cover:
- Factory selection in build_schema_classes
- Stage 1 → Stage 2 execution with stable row identities and source evidence
- Failure paths (no rows, failed row, omitted cell)
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
            if "row_anchor" not in kwargs:
                # Stage 1 call
                return {"results": s1_rows}
            anchor = json.loads(kwargs["row_anchor"])
            i = {"Pain VAS": 0, "Bone level": 1}[anchor["outcome_name"]]
            assert anchor["followup_point"] == ["7 days", "6 months"][i]
            return {"variability_sd": sd_vals[i], "central_value": cv_vals[i]}

        async def run():
            with patch("dspy_components.runtime_builders.async_dspy_forward", side_effect=mock_forward):
                extractor = ExtractorCls()
                return await extractor("paper text here")

        result = asyncio.get_event_loop().run_until_complete(run())
        rows = result["results"]["value"]

        assert len(rows) == 2
        assert rows[0]["outcome_name"]["value"] == "Pain VAS"
        assert rows[1]["followup_point"]["value"] == "6 months"
        assert rows[0]["variability_sd"]["value"] == "12"
        assert rows[1]["variability_sd"]["value"] == "0.1"
        assert rows[0]["central_value"]["value"] == "34"
        assert rows[1]["variability_sd"]["source_text"] == "SD 0.1"

    def test_stage1_empty_is_explicitly_not_reported(self):
        ExtractorCls = self._make_extractor()

        async def mock_forward(predictor, **kwargs):
            return {"results": []}

        async def run():
            with patch("dspy_components.runtime_builders.async_dspy_forward", side_effect=mock_forward):
                return await ExtractorCls()("paper")

        result = asyncio.get_event_loop().run_until_complete(run())
        assert result["results"]["value"] == "NR"
        assert result["results"]["status"] == "not_reported"

    def test_stage1_exception_reaches_retry_handler(self):
        ExtractorCls = self._make_extractor()

        async def mock_forward(predictor, **kwargs):
            raise RuntimeError("LLM timeout")

        async def run():
            with patch("dspy_components.runtime_builders.async_dspy_forward", side_effect=mock_forward):
                return await ExtractorCls()("paper")

        with pytest.raises(RuntimeError, match="LLM timeout"):
            asyncio.get_event_loop().run_until_complete(run())

    def test_stage2_row_failure_is_marked_as_error(self):
        """A failed row remains distinguishable from truly unreported values."""
        ExtractorCls = self._make_extractor()

        s1_rows = [{"outcome_name": "Pain VAS", "followup_point": "7 days"}]

        async def mock_forward(predictor, **kwargs):
            if "row_anchor" not in kwargs:
                return {"results": s1_rows}
            raise RuntimeError("stage 2 failure")

        async def run():
            with patch("dspy_components.runtime_builders.async_dspy_forward", side_effect=mock_forward):
                return await ExtractorCls()("paper")

        result = asyncio.get_event_loop().run_until_complete(run())
        rows = result["results"]["value"]
        assert len(rows) == 1
        for col in ["variability_sd", "central_value"]:
            assert rows[0][col]["value"] == "NR"
            assert rows[0][col]["status"] == "error"
            assert "stage 2 failure" in rows[0][col]["error"]

    def test_missing_cell_does_not_shift_other_rows(self):
        """A missing cell in B must not receive A's value."""
        ExtractorCls = self._make_extractor()

        s1_rows = [
            {"outcome_name": "A", "followup_point": "7d"},
            {"outcome_name": "B", "followup_point": "30d"},
        ]

        async def mock_forward(predictor, **kwargs):
            if "row_anchor" not in kwargs:
                return {"results": s1_rows}
            anchor = json.loads(kwargs["row_anchor"])
            if anchor["outcome_name"] == "A":
                return {"variability_sd": {"value": "5", "source_text": "5"},
                        "central_value": {"value": "10", "source_text": "10"}}
            return {"central_value": {"value": "20", "source_text": "20"}}

        async def run():
            with patch("dspy_components.runtime_builders.async_dspy_forward", side_effect=mock_forward):
                return await ExtractorCls()("paper")

        result = asyncio.get_event_loop().run_until_complete(run())
        rows = result["results"]["value"]
        assert len(rows) == 2
        assert rows[0]["variability_sd"]["value"] == "5"
        assert rows[1]["variability_sd"] == {"value": "NR", "source_text": "NR", "status": "missing"}
        assert rows[1]["central_value"]["value"] == "20"
