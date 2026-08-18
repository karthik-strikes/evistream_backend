"""The agentic table path must speak the same absence vocabulary as the DSPy path.

Before this, the two diverged in ways no consumer handled: agentic emitted
"extracted"/"partial" statuses nothing recognised, never stamped per-cell status
at all, and appended "NR" to every enum regardless of what the author declared.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dspy_components import agentic_table as T  # noqa: E402
from utils import absence as A  # noqa: E402


class TestStatusVocabulary:
    def test_model_facing_enum_is_canonical(self):
        assert T._STATUS_VALUES == [
            A.REPORTED,
            A.NOT_REPORTED,
            A.NOT_APPLICABLE,
            A.PARTIAL,
        ]

    def test_legacy_extracted_still_validates(self):
        """Cached responses and older prompts must not start failing validation."""
        assert A.normalize_status("extracted") == A.REPORTED

    def test_prompt_legend_no_longer_teaches_extracted(self):
        """The legend the model reads must match the vocabulary we store."""
        import inspect

        legend = inspect.getsource(T.build_prompt)
        assert '`status` is "reported"' in legend
        assert '"extracted"' not in legend
        assert '"extracted"' not in T.SYSTEM_APPEND


class TestCellEnums:
    def test_routed_column_offers_both_na_and_nr(self):
        """A RoB 2 signalling question needs NA (design) and NR (silence)."""
        schema = T._cell_value_schema(
            {"field_type": "select", "options": ["Yes", "No", "Not applicable"]}
        )
        assert schema["enum"] == ["Yes", "No", "Not applicable", "NR"]

    def test_funding_none_column_still_gets_nr(self):
        """"None" is a substantive answer and cannot double as the NR token."""
        schema = T._cell_value_schema(
            {"field_type": "select", "options": ["Industry", "Public", "None"]}
        )
        assert schema["enum"] == ["Industry", "Public", "None", "NR"]

    def test_author_declared_nr_is_not_duplicated(self):
        schema = T._cell_value_schema({"field_type": "select", "options": ["Yes", "NR"]})
        assert schema["enum"] == ["Yes", "NR"]

    def test_multiselect_items_follow_the_same_rule(self):
        schema = T._cell_value_schema(
            {"field_type": "select", "multiple": True, "options": ["a", "b"]}
        )
        assert schema["items"]["enum"] == ["a", "b", "NR"]


def _envelope(cells, status="extracted"):
    return {
        "tbl": {
            "value": [cells],
            "source_text": "Table 2 reports the outcomes.",
            "status": status,
        }
    }


FIELD_DEF = {
    "subform_fields": [
        {"field_name": "funding", "options": ["Industry", "Public", "None"]},
        {"field_name": "route", "options": ["Yes", "No", "Not applicable"]},
        {"field_name": "sd"},
    ]
}


class TestNormalizeEnvelope:
    def test_outer_status_is_canonicalized(self):
        out = T.normalize_envelope(_envelope({"sd": {"value": "5", "source_text": "SD 5"}}), "tbl")
        assert out["tbl"]["status"] == A.REPORTED

    def test_per_cell_status_is_stamped_with_declared_options(self):
        env = _envelope(
            {
                "funding": {"value": "none", "source_text": "No funding was received."},
                "route": {"value": "Not applicable", "source_text": "Parallel-group trial."},
                "sd": {"value": "NR", "source_text": "NR"},
            }
        )
        row = T.normalize_envelope(env, "tbl", FIELD_DEF)["tbl"]["value"][0]
        # A declared substantive option is a finding, canonicalized to its spelling.
        assert row["funding"]["status"] == A.REPORTED
        assert row["funding"]["value"] == "none"
        # A declared inapplicability option keeps the NA meaning.
        assert row["route"]["status"] == A.NOT_APPLICABLE
        # A plain absence token is a reporting gap.
        assert row["sd"]["status"] == A.NOT_REPORTED

    def test_empty_table_is_explicit_not_reported(self):
        env = {"tbl": {"value": [], "source_text": "", "status": "extracted"}}
        assert T.normalize_envelope(env, "tbl")["tbl"] == {
            "value": "NR",
            "source_text": "NR",
            "status": A.NOT_REPORTED,
        }

    def test_whole_field_nr_is_not_reported(self):
        env = {"tbl": {"value": "NR", "status": "extracted"}}
        out = T.normalize_envelope(env, "tbl")["tbl"]
        assert out["status"] == A.NOT_REPORTED
        assert out["source_text"] == "NR"

    def test_works_without_a_field_def(self):
        """Call sites that have no field_def must not crash."""
        env = _envelope({"sd": {"value": "NA", "source_text": ""}})
        row = T.normalize_envelope(env, "tbl")["tbl"]["value"][0]
        # Ungrounded, undeclared NA falls back to the safer claim.
        assert row["sd"]["status"] == A.NOT_REPORTED


class TestFailureEnvelopes:
    def test_validator_accepts_canonical_and_legacy(self):
        schema = T.build_output_schema({"name": "tbl", "subform_fields": []})
        for status in (A.REPORTED, "extracted", A.NOT_APPLICABLE):
            env = {"tbl": {"value": "NR", "source_text": "NR", "status": status}}
            problems = T.validate_envelope(env, "tbl", schema)
            assert not [p for p in problems if "status must be" in p], (status, problems)

    def test_validator_rejects_an_unknown_status(self):
        schema = T.build_output_schema({"name": "tbl", "subform_fields": []})
        env = {"tbl": {"value": "NR", "source_text": "NR", "status": "wat"}}
        problems = T.validate_envelope(env, "tbl", schema)
        assert any("status must be" in p for p in problems)
