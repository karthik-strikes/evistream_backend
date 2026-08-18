"""Unit tests for per-field table extraction mode (single_call / row_then_columns
/ agentic).

Tests cover:
- build_schema_classes: per-field "agentic" dispatch, and the legacy per-form
  table_extraction_mode fallback, both routing to the same agentic factory.
- SignatureGenerator._enrich_subform_columns_independently: strategy defaults
  to single_call at every column count (no more >5-column auto two-stage),
  a user-chosen strategy is honoured and invalid values fall back, and anchor
  detection still runs for wide tables regardless of which strategy wins.
"""

import sys
import os
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

SUBFIELDS = [
    {"field_name": "outcome_name", "field_type": "text", "field_description": "Name"},
    {"field_name": "followup_point", "field_type": "text", "field_description": "Timepoint"},
    {"field_name": "variability_sd", "field_type": "text", "field_description": "SD"},
    {"field_name": "central_value", "field_type": "text", "field_description": "Mean"},
]

WIDE_SUBFIELDS = SUBFIELDS + [
    {"field_name": f"extra_col_{i}", "field_type": "text", "field_description": f"col {i}"}
    for i in range(3)
]  # 7 columns total — over the >5 anchor-detection threshold


def _make_sig_def(extraction_strategy=None, anchor_columns=None, subform_fields=None):
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


def _make_schema_def(sig_def, table_extraction_mode=None):
    schema_def = {
        "task_name": "test",
        "signatures": [sig_def],
        "pipeline_stages": [{"stage": 1, "signatures": ["TestSig"], "requires_fields": []}],
        "fallback_structures": {},
    }
    if table_extraction_mode:
        schema_def["table_extraction_mode"] = table_extraction_mode
    return schema_def


# ---------------------------------------------------------------------------
# build_schema_classes dispatch
# ---------------------------------------------------------------------------

class TestPerFieldDispatch:
    def test_agentic_factory_selected_per_field(self):
        """A field with extraction_strategy='agentic' routes to the agentic
        extractor even though the form is not in the legacy form-level mode."""
        from dspy_components.runtime_builders import build_schema_classes

        sig_def = _make_sig_def(
            extraction_strategy="agentic",
            anchor_columns=["outcome_name", "followup_point"],
            subform_fields=SUBFIELDS,
        )
        factories = build_schema_classes(_make_schema_def(sig_def), "test")
        cls = factories["TestSig"]
        assert getattr(cls, "_is_agentic", False) is True
        assert getattr(cls, "_is_keyed_pipeline", False) is False

    def test_single_call_factory_is_default(self):
        """No extraction_strategy at all (a freshly created field) → single-call,
        not two-stage, even though this fixture has enough columns that the old
        auto-rule would have picked row_then_columns."""
        from dspy_components.runtime_builders import build_schema_classes

        sig_def = _make_sig_def(subform_fields=WIDE_SUBFIELDS)
        factories = build_schema_classes(_make_schema_def(sig_def), "test")
        cls = factories["TestSig"]
        assert getattr(cls, "_is_keyed_pipeline", False) is False
        assert getattr(cls, "_is_agentic", False) is False

    def test_row_then_columns_still_selectable(self):
        """row_then_columns remains fully reachable — it is set aside, not removed."""
        from dspy_components.runtime_builders import build_schema_classes

        sig_def = _make_sig_def(
            extraction_strategy="row_then_columns",
            anchor_columns=["outcome_name", "followup_point"],
            subform_fields=SUBFIELDS,
        )
        factories = build_schema_classes(_make_schema_def(sig_def), "test")
        cls = factories["TestSig"]
        assert getattr(cls, "_is_keyed_pipeline", False) is True

    def test_form_level_agentic_mode_is_back_compat_fallback(self):
        """Legacy forms with no per-field strategy but the old form-level
        table_extraction_mode='agentic' still route every table field agentic."""
        from dspy_components.runtime_builders import build_schema_classes

        sig_def = _make_sig_def(subform_fields=SUBFIELDS)  # no per-field strategy
        factories = build_schema_classes(
            _make_schema_def(sig_def, table_extraction_mode="agentic"), "test"
        )
        cls = factories["TestSig"]
        assert getattr(cls, "_is_agentic", False) is True

    def test_per_field_strategy_overrides_form_level_mode(self):
        """A field explicitly set to row_then_columns is NOT swept into agentic
        just because the form carries the legacy form-level agentic flag —
        per-field strategy takes precedence over the fallback."""
        from dspy_components.runtime_builders import build_schema_classes

        sig_def = _make_sig_def(
            extraction_strategy="row_then_columns",
            anchor_columns=["outcome_name", "followup_point"],
            subform_fields=SUBFIELDS,
        )
        factories = build_schema_classes(
            _make_schema_def(sig_def, table_extraction_mode="agentic"), "test"
        )
        cls = factories["TestSig"]
        assert getattr(cls, "_is_keyed_pipeline", False) is True
        assert getattr(cls, "_is_agentic", False) is False


# ---------------------------------------------------------------------------
# Codegen-time strategy decision (signature_gen.py)
# ---------------------------------------------------------------------------

class TestCodegenStrategyDecision:
    """_enrich_subform_columns_independently makes the strategy call. LLM-backed
    per-column enrichment and anchor classification are mocked so these stay
    hermetic unit tests, not integration tests against a real model."""

    def _make_generator(self):
        from core.generators.signature_gen import SignatureGenerator
        # Bypass __init__ (which constructs a real LLM client) — this test
        # exercises pure branch logic, not the LLM-backed helpers.
        return SignatureGenerator.__new__(SignatureGenerator)

    def _spec_dict(self, subfields, extraction_strategy=None):
        field = {
            "field_name": "results",
            "field_type": "array",
            "field_description": "Test table.",
            "subform_fields": subfields,
        }
        if extraction_strategy is not None:
            field["extraction_strategy"] = extraction_strategy
        enriched_sig = {"fields": {"results": field}}
        spec_dict = {
            "class_name": "TestSig",
            "class_docstring": "Test.",
            "output_fields": [{"field_name": "results", "description": "Test table."}],
        }
        return spec_dict, enriched_sig

    def test_default_is_single_call_at_every_width(self):
        """No stored strategy + a wide (7-column) table → single_call, not the
        old >5-column auto row_then_columns."""
        gen = self._make_generator()
        spec_dict, enriched_sig = self._spec_dict(WIDE_SUBFIELDS)
        with patch.object(gen, "_llm_detect_key_columns", return_value={"outcome_name"}) as mock_llm, \
             patch.object(gen, "enrich_new_field", return_value={"is_valid": False, "spec": None}):
            result = gen._enrich_subform_columns_independently(spec_dict, enriched_sig)
        out_field = result["output_fields"][0]
        assert out_field["extraction_strategy"] == "single_call"
        # Anchor detection still runs for wide tables even though it no longer
        # routes the field — agentic depends on it for row identity.
        mock_llm.assert_called_once()
        assert out_field["anchor_columns"] == ["outcome_name"]

    def test_narrow_table_uses_heuristic_anchors_no_llm_call(self):
        """<=5 columns: anchors come from the free keyword heuristic, not an
        LLM call — this path must not have gotten more expensive."""
        gen = self._make_generator()
        spec_dict, enriched_sig = self._spec_dict(SUBFIELDS)  # 4 columns
        with patch.object(gen, "_llm_detect_key_columns") as mock_llm, \
             patch.object(gen, "enrich_new_field", return_value={"is_valid": False, "spec": None}):
            result = gen._enrich_subform_columns_independently(spec_dict, enriched_sig)
        out_field = result["output_fields"][0]
        assert out_field["extraction_strategy"] == "single_call"
        mock_llm.assert_not_called()
        assert out_field["anchor_columns"]  # heuristic always yields at least one

    def test_user_chosen_agentic_strategy_is_preserved(self):
        """A user's prior choice of 'agentic' survives re-enrichment (this is
        the codegen-regenerate path) instead of being reset to single_call."""
        gen = self._make_generator()
        spec_dict, enriched_sig = self._spec_dict(SUBFIELDS, extraction_strategy="agentic")
        with patch.object(gen, "enrich_new_field", return_value={"is_valid": False, "spec": None}):
            result = gen._enrich_subform_columns_independently(spec_dict, enriched_sig)
        assert result["output_fields"][0]["extraction_strategy"] == "agentic"

    def test_user_chosen_row_then_columns_is_preserved_on_wide_table(self):
        gen = self._make_generator()
        spec_dict, enriched_sig = self._spec_dict(WIDE_SUBFIELDS, extraction_strategy="row_then_columns")
        with patch.object(gen, "_llm_detect_key_columns", return_value={"outcome_name"}), \
             patch.object(gen, "enrich_new_field", return_value={"is_valid": False, "spec": None}):
            result = gen._enrich_subform_columns_independently(spec_dict, enriched_sig)
        assert result["output_fields"][0]["extraction_strategy"] == "row_then_columns"

    def test_invalid_stored_strategy_falls_back_to_single_call(self):
        """Defensive: an unrecognized value on the field (e.g. stale data)
        must not silently reach the runtime dispatcher."""
        gen = self._make_generator()
        spec_dict, enriched_sig = self._spec_dict(SUBFIELDS, extraction_strategy="two_stage_typo")
        with patch.object(gen, "enrich_new_field", return_value={"is_valid": False, "spec": None}):
            result = gen._enrich_subform_columns_independently(spec_dict, enriched_sig)
        assert result["output_fields"][0]["extraction_strategy"] == "single_call"


class TestCodegenInputPreservesFieldMode:
    """A regenerate must carry a table field's stored mode from forms.fields all
    the way into the enriched signature.

    The guard for this lives in decomposition._enrich_signatures_with_metadata,
    which copies extraction_strategy/key_columns off the form field. But it
    reads form_data, and form_data is FormDataInput.model_dump()
    (code_generation_service.py). Pydantic ignores undeclared keys, so while
    FormFieldDefinition did not declare extraction_strategy the value was
    dropped at validation and the guard never saw it — every regenerate quietly
    reset a Rigorous table field to Fast, with no error and no log line.

    The tests above all passed throughout that window, because they hand-build
    enriched_sig and never cross the validator. These two close that gap: they
    start from a raw form-field dict, exactly as forms.fields stores it.
    """

    FORM_DATA = {
        "form_name": "Regression form",
        "form_description": "A form carrying one keyed table field.",
        "fields": [
            {
                "field_name": "outcomes",
                "field_description": "Outcome rows.",
                "field_type": "array",
                "extraction_strategy": "row_then_columns",
                "key_columns": ["outcome_name", "followup_point"],
                "anchor_columns": ["outcome_name", "followup_point"],
                "hints": ["one row per outcome x timepoint"],
                "rules": ["NR when the paper does not report it"],
                "subform_fields": SUBFIELDS,
            }
        ],
    }

    def test_validator_does_not_strip_mode_or_key(self):
        from core.generators.validators import validate_form_data
        from utils.table_schema import field_key_columns

        field = validate_form_data(self.FORM_DATA).model_dump()["fields"][0]

        assert field["extraction_strategy"] == "row_then_columns"
        assert field_key_columns(field) == ["outcome_name", "followup_point"]
        # Same failure mode, same fix: these were being dropped too, which is
        # why regenerated forms came back with prose-baked column descriptions.
        assert field["hints"] == ["one row per outcome x timepoint"]
        assert field["rules"] == ["NR when the paper does not report it"]

    def test_mode_survives_into_the_enriched_signature(self):
        from types import SimpleNamespace

        from core.generators.decomposition import _enrich_signatures_with_metadata
        from core.generators.validators import validate_form_data
        from utils.table_schema import field_key_columns, resolve_strategy, DISCOVER_THEN_FILL

        form_data = validate_form_data(self.FORM_DATA).model_dump()
        sig = SimpleNamespace(name="ExtractOutcomes", field_names=["outcomes"], depends_on=[])

        enriched = _enrich_signatures_with_metadata([sig], form_data)
        field = enriched[0]["fields"]["outcomes"]

        assert resolve_strategy(field["extraction_strategy"]) == DISCOVER_THEN_FILL
        assert field_key_columns(field) == ["outcome_name", "followup_point"]
