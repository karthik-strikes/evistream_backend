"""Row-identity composition, the derivation clause, and generated-prose hygiene.

Two template defects motivated these:

1. The row key was stated TWICE — computed into `anchor_columns` and also typed
   by hand into the field description — and the two drifted. row_then_columns
   and agentic both read anchor_columns, so only single-call depended on the
   prose, and it produced 12 rows one run and 32 the next on the same paper
   because nothing told it authoritatively whether `scale` was part of the
   identity. The key is now composed from anchor_columns for every mode.

2. The grounding block demanded that a value appear inside its own quote, while
   fields legitimately asked for derived values (SE->SD, a per-group N from a
   total). Unsatisfiable, and the model resolved it by attaching a quote that
   did not contain the value — indistinguishable from a fabrication.
"""

import json
import os
import re
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dspy_components.runtime_builders import _compose_field_desc  # noqa: E402
from core.generators.signature_gen import (  # noqa: E402
    _sanitize_generated_prose,
    _warn_on_row_key_mismatch,
)

COLS = [
    {"field_name": "comparison", "field_type": "select", "field_description": "arm 2 drug"},
    {"field_name": "timepoint", "field_type": "select", "field_description": "when"},
    {"field_name": "scale", "field_type": "text", "field_description": "instrument"},
    {"field_name": "mean_arm1", "field_type": "number", "field_description": "mean"},
    {"field_name": "sd_arm1", "field_type": "number", "field_description": "sd"},
]


def _table_field(anchors=("comparison", "timepoint", "scale"), grounded=True):
    f = {
        "name": "outcomes",
        "type": "Dict[str, Any]",
        "description": "One row per (comparison × timepoint).",   # deliberately stale
        "source_grounded": grounded,
        "subform_fields": COLS,
    }
    if anchors is not None:
        f["anchor_columns"] = list(anchors)
    return f


# --------------------------------------------------------------------------- #
# 1. Row identity composed from anchor_columns
# --------------------------------------------------------------------------- #

class TestRowIdentityBlock:
    def test_states_the_anchor_tuple_in_order(self):
        desc = _compose_field_desc(_table_field())
        assert "Row Identity (authoritative):" in desc
        assert "comparison × timepoint × scale" in desc

    def test_stale_tuple_is_removed_not_merely_outranked(self):
        """Supersedes the original "precedence, not deletion" decision.

        Precedence alone was not enough. An audit of all 53 live table fields
        found 9 whose typed tuple disagreed with their own key, and on several
        the stale tuple sat under **Rules** — which the field editor labels
        "hard constraints the AI must follow". So the model was reading two
        absolute, contradicting instructions and being asked to prefer the
        second. Removing the competing tuple at compose time is strictly
        better: one statement of row identity, and the author's stored prose is
        still untouched in the editor.

        The precedence line stays — it covers phrasings this parser cannot
        recognise.
        """
        desc = _compose_field_desc(_table_field())
        assert "THIS list wins" in desc
        assert "One row per (comparison × timepoint)." not in desc
        # ...and the authoritative tuple is the only one left standing.
        assert "comparison × timepoint × scale" in desc

    def test_authored_nuance_survives_the_removal(self):
        """Only the competing tuple goes. A single-concept clarification is
        author intent that the key cannot express, so it must be preserved."""
        f = _table_field()
        f["description"] = (
            "One row per (comparison × timepoint). "
            "If a scale is reported twice, prefer the baseline-adjusted value."
        )
        desc = _compose_field_desc(f)
        assert "One row per (comparison × timepoint)" not in desc
        assert "prefer the baseline-adjusted value" in desc

    def test_names_the_value_columns_as_not_part_of_identity(self):
        desc = _compose_field_desc(_table_field())
        i = desc.index("Row Identity")
        block = desc[i:desc.index("Table Columns")]
        assert "mean_arm1" in block and "sd_arm1" in block
        assert "never part of the row identity" in block

    def test_omitted_without_anchor_columns(self):
        """5 live table fields predate anchor detection. They must render exactly
        as before rather than getting a half-built block."""
        desc = _compose_field_desc(_table_field(anchors=None))
        assert "Row Identity" not in desc
        assert "Table Columns" in desc          # everything else intact

    def test_omitted_for_scalar_fields(self):
        scalar = {"name": "design", "type": "Dict[str, Any]", "description": "study design",
                  "source_grounded": True, "anchor_columns": ["design"]}
        assert "Row Identity" not in _compose_field_desc(scalar)

    def test_block_precedes_the_columns(self):
        """Reading order matters: what the field is -> row identity -> columns."""
        desc = _compose_field_desc(_table_field())
        assert desc.index("Row Identity") < desc.index("Table Columns")

    def test_real_production_field_names_all_five_anchors(self):
        path = ("/tmp/claude-1000/-home-ubuntu-evistream/"
                "479d0400-ff86-43dd-b205-9dbf29a72baa/scratchpad/polat_schema_def.json")
        if not os.path.exists(path):
            pytest.skip("production schema_def snapshot not present")
        of = json.load(open(path))["signatures"][0]["output_fields"][0]
        desc = _compose_field_desc(of)
        assert "comparison × outcome_type × reporter × scale × timepoint" in desc


# --------------------------------------------------------------------------- #
# 2. Derivation clause
# --------------------------------------------------------------------------- #

class TestDerivationClause:
    def test_present_in_table_grounding(self):
        assert "DERIVED values:" in _compose_field_desc(_table_field())

    def test_present_in_scalar_grounding(self):
        scalar = {"name": "age", "type": "Dict[str, Any]",
                  "description": "mean age", "source_grounded": True}
        assert "DERIVED values:" in _compose_field_desc(scalar)

    def test_absent_when_field_is_not_source_grounded(self):
        assert "DERIVED values:" not in _compose_field_desc(
            _table_field(grounded=False)
        )

    def test_requires_quote_on_inputs_not_the_computed_number(self):
        """source_text must stay verbatim — source_linker.locate_source matches it
        against the paper to resolve a page/bbox for the PDF highlight."""
        desc = _compose_field_desc(_table_field())
        i = desc.index("DERIVED values:")
        clause = desc[i:i + 900]
        assert "INPUTS" in clause
        assert '"derived"' in clause
        assert "never invent inputs" in clause

    def test_does_not_contradict_the_substring_rule(self):
        """Both statements coexist; the derivation clause is the documented
        exception rather than a second, conflicting rule."""
        desc = _compose_field_desc(_table_field())
        assert "MUST appear as a substring of its source_text" in desc
        assert desc.index("MUST appear as a substring") < desc.index("DERIVED values:")


# --------------------------------------------------------------------------- #
# 3. Codegen row-key mismatch warning
# --------------------------------------------------------------------------- #

class TestRowKeyMismatchWarning:
    def test_warns_when_prose_omits_an_anchor(self, caplog):
        with caplog.at_level("WARNING"):
            _warn_on_row_key_mismatch(
                "outcomes", ["comparison", "outcome_type", "reporter", "scale", "timepoint"],
                ["One row per (comparison × outcome_type × reporter × timepoint). More text."],
            )
        assert "Row-key mismatch" in caplog.text
        assert "scale" in caplog.text

    def test_silent_when_they_agree(self, caplog):
        with caplog.at_level("WARNING"):
            _warn_on_row_key_mismatch(
                "outcomes", ["comparison", "timepoint"],
                ["One row per (comparison × timepoint). More text."],
            )
        assert "Row-key mismatch" not in caplog.text

    @pytest.mark.parametrize("anchors,texts", [
        (["a", "b"], ["A description with no row key at all."]),          # nothing to compare
        ([], ["One row per (a × b)."]),                                    # no anchors computed
        (["comparison"], ["One row per (zzz × qqq)."]),                    # unrelated prose
    ])
    def test_silent_otherwise(self, caplog, anchors, texts):
        with caplog.at_level("WARNING"):
            _warn_on_row_key_mismatch("outcomes", anchors, texts)
        assert "Row-key mismatch" not in caplog.text


# --------------------------------------------------------------------------- #
# 4. Generated-prose hygiene
# --------------------------------------------------------------------------- #

class TestSanitizeGeneratedProse:
    def test_strips_the_control_character_that_shipped_live(self, caplog):
        bad = "Check table column headers for repeated-measures data \row a distinct timepoint"
        with caplog.at_level("WARNING"):
            out = _sanitize_generated_prose([bad], "outcomes.timepoint", "hints")
        assert not re.search(r"[\x00-\x08\x0b-\x1f]", out[0])
        assert "Sanitized malformed" in caplog.text

    def test_collapses_the_doubled_word_that_shipped_live(self):
        bad = "Do not invent a bin when the outcome is unspecified, use use 'overall' instead"
        out = _sanitize_generated_prose([bad], "outcomes.timepoint", "rules")
        assert "use use" not in out[0]
        assert "use 'overall'" in out[0]

    def test_leaves_clean_prose_untouched(self, caplog):
        good = ["Look for time labels such as '30 min', '2 h', or 'Day 1'."]
        with caplog.at_level("WARNING"):
            assert _sanitize_generated_prose(good, "f.c", "hints") == good
        assert "Sanitized" not in caplog.text

    def test_passes_non_string_members_through(self):
        """examples are dicts — they must survive unchanged."""
        exs = [{"value": "20", "source_text": "n = 20"}]
        assert _sanitize_generated_prose(exs, "f.c", "examples") == exs

    def test_handles_a_bare_string_and_other_types(self):
        assert _sanitize_generated_prose("a  b", "f.c", "hints") == "a b"
        assert _sanitize_generated_prose(None, "f.c", "hints") is None
