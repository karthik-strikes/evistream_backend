"""Heuristic anchor (row-identity) detection — the fallback for _llm_detect_key_columns.

Anchors are the row key for two-stage table extraction. Missing one MERGES rows
that should be distinct, and the merge is silent: on the CD015432
continuous-outcomes table the old keyword-match rule returned only
{outcome_type, timepoint}, dropping `comparison` and `reporter` — which would
have collapsed all four comparator arms into one row and discarded three
quarters of the table with no warning.

The rule is now inverted: a column is an anchor UNLESS it looks like a
measurement. That asymmetry is the point — too few anchors destroys data, too
many only splits rows more finely.
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.generators.signature_gen import _auto_detect_key_columns  # noqa: E402

ZFORMS = os.path.join(os.path.dirname(__file__), "..", "..", "zforms")


def _cols(*specs):
    """specs: (name, field_type) tuples."""
    return [{"field_name": n, "field_type": t} for n, t in specs]


class TestRegression:
    """The exact failure this rewrite exists to prevent."""

    def test_identity_columns_without_keywords_are_anchors(self):
        """`comparison` and `reporter` match no identity keyword, yet both are
        row-identity columns. The old rule dropped them."""
        cols = _cols(
            ("comparison", "select"), ("outcome_type", "select"),
            ("reporter", "select"), ("timepoint", "select"), ("scale", "text"),
            ("mean_arm1", "number"), ("sd_arm1", "number"), ("n_arm1", "number"),
            ("mean_arm2", "number"), ("sd_arm2", "number"), ("n_arm2", "number"),
        )
        anchors = _auto_detect_key_columns(cols)
        assert "comparison" in anchors, "dropping this merges all comparator arms"
        assert "reporter" in anchors

    def test_matches_the_production_llm_classification(self):
        """On the real shipped form the heuristic must now agree with what the
        LLM classifier chose in production — otherwise a classifier failure
        silently changes the row key."""
        path = os.path.join(ZFORMS, "ibuprofen", "cd015432_outcomes_continuous.json")
        if not os.path.exists(path):
            pytest.skip("zforms fixture not present")
        form = json.load(open(path))
        subfields = form["fields"][0]["subform_fields"]
        assert sorted(_auto_detect_key_columns(subfields)) == [
            "comparison", "outcome_type", "reporter", "scale", "timepoint",
        ]

    def test_no_measurement_column_is_ever_an_anchor(self):
        """Anchoring on a measured value fragments rows per number and leaves
        Stage 2 with nothing to extract."""
        cols = _cols(
            ("arm_label", "text"),
            ("mean_arm1", "number"), ("sd_arm2", "number"), ("n_arm1", "number"),
            ("change_group2", "number"), ("pvalue", "number"), ("median_score", "number"),
        )
        anchors = _auto_detect_key_columns(cols)
        assert anchors == {"arm_label"}


class TestValueDetection:
    @pytest.mark.parametrize("name", [
        "mean_arm1", "sd_arm2", "n_arm1", "change_group2", "value_g1",
    ])
    def test_per_arm_suffix_is_a_value(self, name):
        cols = _cols(("row_label", "text"), (name, "text"))
        assert name not in _auto_detect_key_columns(cols)

    @pytest.mark.parametrize("name", [
        "mean_change", "median_duration", "sd_overall", "pct_responders",
        "count_events", "total_dose",
    ])
    def test_leading_statistic_token_is_a_value(self, name):
        cols = _cols(("row_label", "text"), (name, "text"))
        assert name not in _auto_detect_key_columns(cols)

    def test_numeric_type_is_a_value_even_without_a_telling_name(self):
        """`age` / `dose_mg` carry no statistic token, but a number column is a
        measured quantity, not a row identity."""
        cols = _cols(("arm_label", "text"), ("age", "number"), ("dose_mg", "number"))
        assert _auto_detect_key_columns(cols) == {"arm_label"}

    def test_non_numeric_identity_columns_survive(self):
        cols = _cols(
            ("population", "select"), ("condition", "text"), ("instrument", "text"),
            ("mean_arm1", "number"),
        )
        anchors = _auto_detect_key_columns(cols)
        assert anchors == {"population", "condition", "instrument"}


class TestDegenerateGuards:
    def test_never_returns_every_column(self):
        """Two-stage builds Stage 2 from the non-anchor columns; if every column
        were an anchor it would have no output fields at all."""
        cols = _cols(("alpha", "text"), ("beta", "text"), ("gamma", "text"))
        anchors = _auto_detect_key_columns(cols)
        assert 0 < len(anchors) < len(cols)

    def test_all_anchor_like_falls_back_to_keyword_subset(self):
        """When nothing looks like a measurement, the narrower keyword rule picks
        the anchors so the remainder become values."""
        cols = _cols(("outcome_name", "text"), ("timepoint", "text"), ("freetext", "text"))
        anchors = _auto_detect_key_columns(cols)
        assert "outcome_name" in anchors and "timepoint" in anchors
        assert "freetext" not in anchors

    def test_all_value_like_still_yields_one_anchor(self):
        """Every column a measurement is itself degenerate — return one anchor so
        Stage 1 has something to discover rows by, rather than an empty key."""
        cols = _cols(("mean_arm1", "number"), ("sd_arm1", "number"), ("n_arm1", "number"))
        anchors = _auto_detect_key_columns(cols)
        assert len(anchors) == 1

    def test_empty_and_malformed_input(self):
        assert _auto_detect_key_columns([]) == set()
        assert _auto_detect_key_columns([{"no_name": 1}, None, "junk"]) == set()

    def test_single_column_table(self):
        cols = _cols(("only_col", "text"))
        assert _auto_detect_key_columns(cols) == {"only_col"}
