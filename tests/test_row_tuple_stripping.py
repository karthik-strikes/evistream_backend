"""A table field must state row identity exactly once.

The composite key is what the pipeline enforces, and `_compose_field_desc`
renders it as the "Row Identity (authoritative)" block. A tuple an author typed
into the prose is a second, competing definition — and across 53 live table
fields, 9 disagreed with their own key, several with the stale tuple under
Rules, which the editor presents as a hard constraint.

These tests pin the two halves of the rule:
  · a multi-part tuple is removed (it competes)
  · a single-concept clarification survives (it does not)
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dspy_components.runtime_builders import _compose_field_desc  # noqa: E402
from utils.table_schema import find_row_tuple, strip_row_tuple  # noqa: E402


class TestFindRowTuple:
    def test_parenthesised_multiplication_sign(self):
        assert find_row_tuple("One row per (comparison × outcome × timepoint).") == [
            "comparison", "outcome", "timepoint",
        ]

    def test_bare_letter_x_and_no_parens(self):
        assert find_row_tuple("One row per population x arm x outcome") == [
            "population", "arm", "outcome",
        ]

    def test_single_concept_is_not_a_tuple(self):
        """'one row per timepoint' clarifies; it does not redefine row identity,
        so it must not be treated as a competing tuple."""
        assert find_row_tuple(
            "For an outcome reported at multiple timepoints, create one row per timepoint."
        ) is None

    def test_absent(self):
        assert find_row_tuple("Extract every adverse event.") is None
        assert find_row_tuple("") is None
        assert find_row_tuple(None) is None


class TestStripRowTuple:
    def test_removes_only_the_tuple_sentence(self):
        text = (
            "One row per (comparison × outcome × timepoint). "
            "For an outcome reported at multiple timepoints, create one row per timepoint. "
            "If ibuprofen is compared to more than one drug, create separate rows per comparison."
        )
        out = strip_row_tuple(text)
        assert "One row per (comparison" not in out
        # the nuance and the unrelated guidance both survive
        assert "create one row per timepoint" in out
        assert "separate rows per comparison" in out

    def test_leaves_prose_without_a_tuple_untouched(self):
        text = "Capture rescue analgesia use and adverse effect counts."
        assert strip_row_tuple(text) == text

    def test_no_stray_punctuation_left_behind(self):
        out = strip_row_tuple("One row per a × b × c. Then extract counts.")
        assert out == "Then extract counts."


def _field(description, rules=None, key=None):
    return {
        "name": "outcomes",
        "description": description,
        "rules": rules or [],
        "key_columns": key or [],
        "subform_fields": [
            {"field_name": c, "field_type": "text", "field_description": c}
            for c in ["arm1_label", "arm2_label", "comparison", "outcome", "timepoint", "events"]
        ],
    }


class TestComposedPromptStatesIdentityOnce:
    KEY = ["arm1_label", "arm2_label", "comparison", "outcome", "timepoint"]

    def test_stale_tuple_in_description_is_dropped(self):
        desc = _compose_field_desc(
            _field("One row per (comparison × outcome × timepoint).", key=self.KEY)
        )
        assert "One row per (comparison × outcome × timepoint)" not in desc
        assert "Row Identity (authoritative)" in desc
        assert "arm1_label × arm2_label × comparison × outcome × timepoint" in desc

    def test_stale_tuple_in_rules_is_dropped(self):
        """The worst case: the editor labels Rules 'hard constraints the AI must
        follow', so a stale tuple there reads as absolute."""
        desc = _compose_field_desc(
            _field(
                "Extract outcome rows.",
                rules=[
                    "Create one row per unique comparison × outcome × timepoint combination",
                    "Use NR when the paper does not report it",
                ],
                key=self.KEY,
            )
        )
        assert "unique comparison" not in desc
        assert "Use NR when the paper does not report it" in desc  # unrelated rule kept

    def test_without_a_key_the_authored_prose_is_left_alone(self):
        """No key means no authoritative block, so the author's sentence is the
        only statement of row identity there is — removing it would delete the
        instruction entirely."""
        desc = _compose_field_desc(
            _field("One row per (comparison × outcome × timepoint).", key=[])
        )
        assert "One row per (comparison × outcome × timepoint)" in desc
        assert "Row Identity (authoritative)" not in desc
