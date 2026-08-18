"""Unit tests for the canonical absence classifier (utils/absence.py).

The distinction under test is methodological, not cosmetic:
  - "not reported" is a finding *about the paper* (feeds reporting completeness)
  - "not applicable" is a claim about the study *design*
  - "None" / 0 are *results*
  - a pipeline failure is no claim at all
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from utils import absence as A  # noqa: E402


class TestDeclaredOptionsWin:
    """Invariant 2: the form author's vocabulary outranks the system sentinel.

    These are the 47 fields in the shipped zforms/ fixtures that declare an
    absence-like value as a legitimate select option.
    """

    @pytest.mark.parametrize(
        "value,options",
        [
            # An unfunded study is a COI/RoB finding, not a reporting gap.
            ("None", ["Industry", "Public", "None"]),
            ("none", ["Industry", "None"]),
            ("Yes", ["Yes", "No", "Not applicable"]),
        ],
    )
    def test_declared_substantive_option_is_reported(self, value, options):
        assert A.classify(value, options) == A.REPORTED

    @pytest.mark.parametrize(
        "value,options",
        [
            # Cochrane RoB 2 routes signalling questions to "Not applicable".
            ("Not applicable", ["Yes", "No", "Not applicable"]),
            ("NA", ["Yes", "No", "NA"]),
            ("  not applicable ", ["Yes", "Not applicable"]),
        ],
    )
    def test_declared_na_option_keeps_the_na_meaning(self, value, options):
        """The author's spelling is honoured *and* stays machine-readable, so
        coverage can exclude it and adjudication can flag NA-vs-NR."""
        assert A.classify(value, options) == A.NOT_APPLICABLE

    def test_declared_na_option_needs_no_grounding(self):
        """It is part of the author's schema, unlike an NA the model invented."""
        cell = A.stamp({"value": "Not applicable", "source_text": ""},
                       ["Yes", "No", "Not applicable"])
        assert cell["status"] == A.NOT_APPLICABLE

    def test_multiselect_all_declared_is_reported(self):
        assert A.classify(["a", "b"], ["a", "b", "c"]) == A.REPORTED

    def test_undeclared_value_still_classified_by_token(self):
        # "NA" is not among the options → falls through to token matching.
        assert A.classify("NA", ["Yes", "No"]) == A.NOT_APPLICABLE

    def test_empty_multiselect_is_not_reported(self):
        assert A.classify([], ["a", "b"]) == A.NOT_REPORTED


class TestTokenClassification:
    @pytest.mark.parametrize(
        "value,expected",
        [
            ("NR", A.NOT_REPORTED),
            ("not reported", A.NOT_REPORTED),
            ("NOT_REPORTED", A.NOT_REPORTED),
            ("", A.NOT_REPORTED),
            (None, A.NOT_REPORTED),
            # Deliberately kept on the NR side: ambiguous between "not
            # applicable" and "not available".
            ("N/A", A.NOT_REPORTED),
            # No token is removed from the recognizer, so a bare NONE on an
            # option-less field keeps its old meaning.
            ("NONE", A.NOT_REPORTED),
            ("NA", A.NOT_APPLICABLE),
            ("N.A.", A.NOT_APPLICABLE),
            ("Not applicable", A.NOT_APPLICABLE),
            ("NOT_APPLICABLE", A.NOT_APPLICABLE),
        ],
    )
    def test_bare_tokens(self, value, expected):
        assert A.classify(value) == expected

    @pytest.mark.parametrize("value", [0, 0.0, False, "0", "12.5", "Industry"])
    def test_substantive_values_are_reported(self, value):
        """0 and False are findings — "no withdrawals" is data."""
        assert A.classify(value) == A.REPORTED

    def test_token_set_is_a_superset_of_the_legacy_one(self):
        legacy = {"", "NR", "NA", "N/A", "NONE", "NOT REPORTED", "NOT_REPORTED"}
        assert legacy <= A.ABSENCE_TOKENS


class TestStamp:
    def test_na_without_grounding_downgrades_to_nr(self):
        """Claiming inapplicability is a claim about design, so it must quote it."""
        assert A.stamp({"value": "NA", "source_text": ""})["status"] == A.NOT_REPORTED
        assert A.stamp({"value": "NA", "source_text": "NR"})["status"] == A.NOT_REPORTED

    def test_grounded_na_survives(self):
        cell = {"value": "NA", "source_text": "A parallel-group trial with no crossover phase."}
        assert A.stamp(cell)["status"] == A.NOT_APPLICABLE

    def test_pipeline_verdict_is_never_overridden_by_the_value(self):
        """A failed cell must not be re-read as a genuine absence."""
        for status in (A.MISSING, A.ERROR, A.PARTIAL):
            cell = A.stamp({"value": "NR", "source_text": "NR", "status": status})
            assert cell["status"] == status

    def test_explicit_status_wins(self):
        cell = A.stamp({"value": "12"}, status=A.ERROR)
        assert cell["status"] == A.ERROR

    def test_stamp_does_not_mutate_its_input(self):
        original = {"value": "NR", "source_text": "NR"}
        A.stamp(original)
        assert "status" not in original

    def test_declared_option_reaches_reported_through_stamp(self):
        cell = A.stamp({"value": "None", "source_text": "No funding was received."},
                       ["Industry", "None"])
        assert cell["status"] == A.REPORTED


class TestFailureEnvelope:
    def test_failure_carries_no_nr_literal(self):
        """So that no downstream flatten can resurrect it as "paper is silent"."""
        env = A.failure_envelope(A.MISSING)
        assert env["value"] == ""
        assert env["source_text"] == ""
        assert env["status"] == A.MISSING

    def test_error_records_the_cause(self):
        env = A.failure_envelope(A.ERROR, "RuntimeError: boom")
        assert env["error"] == "RuntimeError: boom"


class TestDisplayLabel:
    @pytest.mark.parametrize(
        "status,expected",
        [
            (A.NOT_REPORTED, A.NR_LABEL),
            (A.NOT_APPLICABLE, A.NA_LABEL),
            (A.MISSING, A.FAILED_LABEL),
            (A.ERROR, A.FAILED_LABEL),
        ],
    )
    def test_labels(self, status, expected):
        assert A.display_label({"value": "x", "status": status}) == expected

    def test_reported_shows_its_own_value(self):
        assert A.display_label({"value": "None", "status": A.REPORTED}) is None

    def test_legacy_statusless_cell_shows_its_own_value(self):
        """Fix-forward: stored rows carry no status and must not be relabelled."""
        assert A.display_label({"value": "NR"}) is None

    def test_agentic_extracted_alias(self):
        assert A.normalize_status("extracted") == A.REPORTED
        assert A.display_label({"value": "x", "status": "extracted"}) is None

    def test_unknown_status_is_not_mistaken_for_a_label(self):
        assert A.normalize_status("wat") is None
        assert A.display_label({"value": "x", "status": "wat"}) is None


class TestCompareKey:
    def test_both_nr_agrees(self):
        a = {"value": "NR", "status": A.NOT_REPORTED}
        b = {"value": "not reported", "status": A.NOT_REPORTED}
        assert A.compare_key(a) == A.compare_key(b)

    def test_na_versus_nr_is_a_conflict(self):
        na = {"value": "NA", "status": A.NOT_APPLICABLE}
        nr = {"value": "NR", "status": A.NOT_REPORTED}
        assert A.compare_key(na) != A.compare_key(nr)

    def test_failure_is_incomparable(self):
        """An extraction failure is not evidence that two reviewers agree."""
        assert A.compare_key(A.failure_envelope(A.MISSING)) is None
        assert A.compare_key(A.failure_envelope(A.ERROR)) is None

    @pytest.mark.parametrize(
        "a,b",
        [
            ("3", 3),
            ("3.0", 3),
            ("yes", True),
            ("No", False),
            (" RCT ", "rct"),
            (["b", "a"], ["a", "b"]),
        ],
    )
    def test_existing_leniency_preserved(self, a, b):
        assert A.compare_key({"value": a}) == A.compare_key({"value": b})

    def test_bare_human_value_compares_against_an_envelope(self):
        """Human rows are bare scalars; AI rows are envelopes."""
        assert A.compare_key("None", ["Industry", "None"]) == A.compare_key(
            {"value": "None", "status": A.REPORTED}
        )


class TestSchemaExtras:
    @pytest.mark.parametrize(
        "options",
        [
            ["Yes", "No"],
            # A routed field needs BOTH tokens — NA and NR are different claims.
            ["Yes", "No", "NA"],
            ["Yes", "Not applicable"],
            # "None" is a substantive funding answer, so it cannot double as the
            # not-reported token; without NR the model would misuse it.
            ["Industry", "Public", "None"],
        ],
    )
    def test_nr_is_offered_when_no_explicit_nr_spelling_declared(self, options):
        assert A.schema_extras(options) == [A.NR_LABEL]

    @pytest.mark.parametrize(
        "options", [["a", "NR"], ["a", "N/A"], ["a", "Not reported"]]
    )
    def test_not_duplicated_when_author_declared_an_nr_spelling(self, options):
        assert A.schema_extras(options) == []
