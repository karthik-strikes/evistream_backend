"""Absence-aware reviewer comparison, coverage math, exports and the retry gate.

The behaviours pinned here are the ones a clinician sees: whether two reviewers
are shown as agreeing, whether a paper is flagged as under-extracted, and what
lands in a downloaded file.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from utils import absence as A  # noqa: E402


def _cell(value, status=None, source="a quote"):
    cell = {"value": value, "source_text": source}
    if status:
        cell["status"] = status
    return cell


# ═══════════════════════════════════════════════════════════════════════════
# Reviewer agreement
# ═══════════════════════════════════════════════════════════════════════════

class TestAdjudicationComparison:
    @staticmethod
    def _agree(a, b):
        from app.services.adjudication_service import _align_multiselect, _canon, _unwrap_for_compare

        a_n, b_n = _unwrap_for_compare(a), _unwrap_for_compare(b)
        a_n, b_n = _align_multiselect(a_n, b_n)
        return _canon(a_n) == _canon(b_n)

    def test_both_nr_agrees(self):
        """Two reviewers who both correctly record a reporting gap agree."""
        assert self._agree(_cell("NR", A.NOT_REPORTED), _cell("NR", A.NOT_REPORTED))

    def test_nr_spellings_agree(self):
        assert self._agree("NR", "not reported")
        assert self._agree("NR", "N/A")

    def test_na_versus_nr_is_a_conflict(self):
        """Different claims about the study: design-excluded vs never-stated."""
        assert not self._agree(_cell("NA", A.NOT_APPLICABLE), _cell("NR", A.NOT_REPORTED))
        assert not self._agree("Not applicable", "NR")

    def test_na_spellings_agree_with_each_other(self):
        assert self._agree("NA", "Not applicable")

    def test_failure_never_agrees_with_a_genuine_nr(self):
        """The bug this closes: a failed AI cell used to compare equal to a
        reviewer's real NR, so the reviewer signed off on a pipeline fault."""
        failed = A.failure_envelope(A.MISSING)
        assert not self._agree(failed, _cell("NR", A.NOT_REPORTED))
        assert not self._agree(failed, "NR")

    def test_failure_does_not_even_agree_with_itself(self):
        assert not self._agree(
            A.failure_envelope(A.ERROR), A.failure_envelope(A.ERROR)
        )

    def test_substantive_values_still_compare_leniently(self):
        assert self._agree(_cell("3"), _cell(3))
        assert self._agree(_cell("yes"), _cell(True))
        assert self._agree(_cell("RCT"), _cell(" rct "))

    def test_declared_none_is_a_value_not_an_absence(self):
        """funding "None" must not agree with a reviewer who said NR."""
        assert not self._agree(_cell("None", A.REPORTED), _cell("NR", A.NOT_REPORTED))

    def test_legacy_statusless_rows_are_untouched(self):
        """Fix-forward: stored rows carry no status and must compare as before."""
        assert self._agree({"value": "12"}, {"value": "12"})
        assert self._agree({"value": "NR"}, {"value": "NR"})


# ═══════════════════════════════════════════════════════════════════════════
# Coverage
# ═══════════════════════════════════════════════════════════════════════════

class TestCoverageMath:
    @staticmethod
    def _mod():
        from app.api.v1 import extractions

        return extractions

    def test_not_applicable_is_not_a_gap(self):
        assert self._mod()._field_is_empty(_cell("NA", A.NOT_APPLICABLE)) is False

    def test_failure_and_nr_are_gaps(self):
        m = self._mod()
        assert m._field_is_empty(A.failure_envelope(A.MISSING)) is True
        assert m._field_is_empty(_cell("NR", A.NOT_REPORTED)) is True

    def test_reported_none_is_not_a_gap(self):
        """An unfunded study reported its funding."""
        assert self._mod()._field_is_empty(_cell("None", A.REPORTED)) is False

    def test_partial_is_not_a_gap(self):
        assert self._mod()._field_is_empty(_cell([{"a": 1}], A.PARTIAL)) is False

    def test_routed_form_is_not_flagged_as_under_extracted(self):
        """A RoB 2 paper whose signalling questions are inapplicable by design
        must not be reported as a badly extracted paper."""
        m = self._mod()
        data = {
            "d1_answer": _cell("Yes", A.REPORTED),
            "d2_3": _cell("Not applicable", A.NOT_APPLICABLE),
            "d2_4": _cell("Not applicable", A.NOT_APPLICABLE),
            "d2_5": _cell("Not applicable", A.NOT_APPLICABLE),
        }
        assert m._flagged_more_than_half_empty(data) is False

    def test_genuinely_empty_paper_is_still_flagged(self):
        m = self._mod()
        data = {
            "a": _cell("Yes", A.REPORTED),
            "b": _cell("NR", A.NOT_REPORTED),
            "c": A.failure_envelope(A.ERROR),
        }
        assert m._flagged_more_than_half_empty(data) is True

    def test_all_inapplicable_is_not_flagged(self):
        m = self._mod()
        data = {"a": _cell("NA", A.NOT_APPLICABLE)}
        assert m._flagged_more_than_half_empty(data) is False

    def test_no_fields_at_all_is_still_flagged(self):
        assert self._mod()._flagged_more_than_half_empty({}) is True


# ═══════════════════════════════════════════════════════════════════════════
# Exports
# ═══════════════════════════════════════════════════════════════════════════

class TestExportRendering:
    """Exercises utils.absence.export_cell, which app.api.v1.results binds as
    _export_cell (that module cannot be imported here — it opens an AWS client
    at import time and this box has no live credentials)."""

    @staticmethod
    def _cellout(cell):
        return A.export_cell(cell)

    def test_status_drives_the_label_not_the_raw_text(self):
        assert self._cellout(_cell("NR", A.NOT_REPORTED))["value"] == A.NR_LABEL
        assert self._cellout(_cell("NA", A.NOT_APPLICABLE))["value"] == A.NA_LABEL

    def test_failure_is_not_blank(self):
        """A blank column reads as "the paper does not report this"."""
        out = self._cellout(A.failure_envelope(A.MISSING))
        assert out["value"] == A.FAILED_LABEL
        assert out["value"] != ""

    def test_reported_value_is_never_overwritten(self):
        assert self._cellout(_cell("None", A.REPORTED))["value"] == "None"

    def test_internal_keys_are_dropped(self):
        out = self._cellout(A.failure_envelope(A.ERROR, "boom"))
        assert "status" not in out and "error" not in out

    def test_table_cells_are_rendered_too(self):
        """_apply_export_prefs used to be top-level only, so per-cell statuses
        leaked into exports untouched."""
        table = {
            "value": [
                {
                    "arm": _cell("Ibuprofen", A.REPORTED),
                    "sd": _cell("NR", A.NOT_REPORTED),
                    "route": A.failure_envelope(A.ERROR, "boom"),
                }
            ],
            "source_text": "Table 2",
            "status": A.REPORTED,
        }
        row = self._cellout(table)["value"][0]
        assert row["arm"]["value"] == "Ibuprofen"
        assert row["sd"]["value"] == A.NR_LABEL
        assert row["route"]["value"] == A.FAILED_LABEL
        assert "status" not in row["sd"]


# ═══════════════════════════════════════════════════════════════════════════
# Retry gate — must not change (token spend)
# ═══════════════════════════════════════════════════════════════════════════

class TestRetryGateUnchanged:
    @staticmethod
    def _classify(v):
        from utils.extraction_assertions import _classify_value

        return _classify_value(v)

    def test_failure_still_retryable(self):
        assert self._classify(A.failure_envelope(A.MISSING)) == "empty"
        assert self._classify(A.failure_envelope(A.ERROR)) == "empty"

    def test_deliberate_nr_is_an_answer_not_a_retry(self):
        assert self._classify(_cell("NR", A.NOT_REPORTED)) == "nr"

    def test_not_applicable_is_an_answer_not_a_retry(self):
        assert self._classify(_cell("NA", A.NOT_APPLICABLE)) == "nr"

    def test_substantive_unchanged(self):
        assert self._classify(_cell("12.5", A.REPORTED)) == "substantive"


# ═══════════════════════════════════════════════════════════════════════════
# Required-field validation
# ═══════════════════════════════════════════════════════════════════════════

RULE = {
    "rule_type": "required",
    "rule_config": {},
    "field_name": "f",
    "id": "r1",
    "severity": "error",
    "message": "required",
}


class TestRequiredRule:
    """Previously dead for every AI extraction: an envelope dict is neither
    None nor a str, so the old check could never fire."""

    @staticmethod
    def _violation(value):
        from app.services.data_cleaning_service import _check_rule

        return _check_rule(RULE, value, {})

    @pytest.mark.parametrize(
        "value",
        [
            A.failure_envelope(A.MISSING),
            A.failure_envelope(A.ERROR),
            None,
            "",
        ],
    )
    def test_fires_when_nothing_was_captured(self, value):
        assert self._violation(value) is not None

    @pytest.mark.parametrize(
        "value",
        [
            _cell("12", A.REPORTED),
            _cell("None", A.REPORTED),
            _cell("NA", A.NOT_APPLICABLE),
            # A bare "NR" string always satisfied this rule; an NR envelope now
            # does too. NR is an answer about the paper, not a blank.
            _cell("NR", A.NOT_REPORTED),
            "12",
        ],
    )
    def test_satisfied_by_an_actual_answer(self, value):
        assert self._violation(value) is None
