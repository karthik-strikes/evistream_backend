"""
Tests for `utils/value_compare.py` — the single answer to "did two sources agree?".

These pin the four semantic merges made when the codebase's four independent
agreement implementations were unified, because each one changes a number that
someone looks at:

  D1  an empty value with no status is INCOMPARABLE, not "NR"
  D2  ambiguous absence tokens ("None", "") are not folded in the comparison path
  D3  a failure is never a rating — and what that means inside a table row
  D4  multi-select alignment is pairwise, and must not swallow an absence token

Plus the two definitional fixes: `_partial` is not a field, and the AI-key
normalizer keeps plain keys while dropping grounding-metadata keys.
"""

import pytest

from utils import absence
from utils import value_compare as vc


def _cell(value, status=None, **extra):
    """A `{value, source_text, status}` envelope as stored in extracted_data."""
    cell = {"value": value}
    if status is not None:
        cell["status"] = status
    cell.update(extra)
    return cell


# ═══════════════════════════════════════════════════════════════════════════
# The tri-state verdict
# ═══════════════════════════════════════════════════════════════════════════

class TestTriState:
    def test_identical_values_agree(self):
        assert vc.agreement("RCT", "RCT") == vc.AGREE
        assert vc.agreement(_cell("12"), _cell("12")) == vc.AGREE

    def test_both_explicit_nr_agree(self):
        """An asserted reporting gap is a finding about the paper, so two
        reviewers who both recorded it agree."""
        a = _cell("NR", absence.NOT_REPORTED)
        b = _cell("NR", absence.NOT_REPORTED)
        assert vc.agreement(a, b) == vc.AGREE

    def test_nr_spellings_agree_with_each_other(self):
        assert vc.agreement("NR", "not reported") == vc.AGREE
        assert vc.agreement("NR", "N/R") == vc.AGREE

    def test_na_never_equals_nr(self):
        """Different claims about the study: 'the paper is silent' vs 'this
        cannot apply here'."""
        assert vc.agreement("NA", "NR") == vc.DISAGREE
        assert vc.agreement(
            _cell("NA", absence.NOT_APPLICABLE), _cell("NR", absence.NOT_REPORTED)
        ) == vc.DISAGREE

    def test_na_spellings_agree_with_each_other(self):
        assert vc.agreement("NA", "Not applicable") == vc.AGREE

    # ── D3: a failure is not a rating ──
    def test_failure_vs_nr_is_incomparable(self):
        failed = _cell("", absence.ERROR)
        assert vc.agreement(failed, _cell("NR", absence.NOT_REPORTED)) == vc.INCOMPARABLE

    def test_failure_vs_itself_is_incomparable(self):
        """Two failed extractions are not two reviewers agreeing. The trap this
        guards is that `absence.compare_key` returns None for both and
        `None == None` is True."""
        a = _cell("", absence.ERROR)
        b = _cell(None, absence.MISSING)
        assert vc.agreement(a, b) == vc.INCOMPARABLE

    def test_failure_vs_real_value_is_incomparable(self):
        assert vc.agreement(_cell(None, absence.MISSING), "12") == vc.INCOMPARABLE

    # ── D1: unfilled is not a claim ──
    def test_empty_with_no_status_is_incomparable(self):
        """`absence.compare_key("")` returns "NR", so without this rule two blank
        cells would score as agreement — nobody recording anything read as both
        reviewers asserting a reporting gap."""
        assert vc.agreement("", "") == vc.INCOMPARABLE
        assert vc.agreement(None, None) == vc.INCOMPARABLE
        assert vc.agreement([], []) == vc.INCOMPARABLE
        assert vc.agreement(_cell(""), _cell("")) == vc.INCOMPARABLE

    def test_empty_vs_explicit_nr_is_incomparable(self):
        assert vc.agreement("", _cell("NR", absence.NOT_REPORTED)) == vc.INCOMPARABLE

    def test_explicit_nr_with_empty_value_is_still_a_claim(self):
        """The status is what makes it a claim, not the value text. This is the
        line between D1 and a genuine reporting gap."""
        a = _cell("", absence.NOT_REPORTED)
        b = _cell("NR", absence.NOT_REPORTED)
        assert vc.agreement(a, b) == vc.AGREE

    def test_zero_and_false_are_findings_not_blanks(self):
        assert vc.agreement(0, 0) == vc.AGREE
        assert vc.agreement(False, False) == vc.AGREE
        assert vc.agreement(0, "") == vc.INCOMPARABLE

    # ── D2: ambiguous tokens are not folded ──
    def test_bare_none_does_not_agree_with_nr(self):
        """A study that reported no funding is not a study that failed to report
        its funding. `absence.classify("None")` folds to not_reported; the
        comparison path deliberately uses `canonical_absence_label`, which does
        not."""
        assert vc.agreement("None", "NR") == vc.DISAGREE

    def test_declared_none_option_stays_reported(self):
        assert vc.agreement(
            _cell("None", absence.REPORTED), _cell("NR", absence.NOT_REPORTED)
        ) == vc.DISAGREE

    # ── leniency that was already there and must stay ──
    def test_numeric_string_equals_number(self):
        assert vc.agreement("3", 3) == vc.AGREE
        assert vc.agreement("3.0", 3) == vc.AGREE

    def test_boolean_synonyms(self):
        assert vc.agreement("yes", True) == vc.AGREE
        assert vc.agreement("Y", "true") == vc.AGREE
        assert vc.agreement("no", False) == vc.AGREE
        assert vc.agreement("yes", "no") == vc.DISAGREE

    def test_case_and_whitespace_insensitive(self):
        assert vc.agreement("RCT", " rct ") == vc.AGREE

    def test_different_cited_quotes_still_agree(self):
        """source_text is metadata about a value, never the value itself."""
        a = _cell("142", absence.REPORTED, source_text="A total of 142 patients…")
        b = _cell("142", absence.REPORTED, source_text="142 participants were enrolled")
        assert vc.agreement(a, b) == vc.AGREE

    def test_values_agree_folds_incomparable_to_false(self):
        assert vc.values_agree("RCT", "RCT") is True
        assert vc.values_agree(_cell("", absence.ERROR), _cell("", absence.ERROR)) is False
        assert vc.values_agree("", "") is False


# ═══════════════════════════════════════════════════════════════════════════
# Table fields
# ═══════════════════════════════════════════════════════════════════════════

class TestNestedTables:
    @staticmethod
    def _rows(*pairs):
        return [{"drug": d, "n": n} for d, n in pairs]

    def test_row_order_is_not_a_conflict(self):
        a = self._rows(("aspirin", "40"), ("placebo", "38"))
        b = self._rows(("placebo", "38"), ("aspirin", "40"))
        assert vc.agreement(a, b) == vc.AGREE

    def test_row_metadata_is_not_part_of_row_identity(self):
        """`page`, `confidence` and `source_text` differing on an otherwise
        identical row is not a disagreement. `absence._value_key` strips only
        four such keys; this module strips all of them."""
        a = [{"drug": "aspirin", "n": "40", "page": 4, "confidence": 0.9}]
        b = [{"drug": "aspirin", "n": "40", "page": 7, "confidence": 0.4}]
        assert vc.agreement(a, b) == vc.AGREE

    def test_differing_cell_is_a_conflict(self):
        a = self._rows(("aspirin", "40"))
        b = self._rows(("aspirin", "38"))
        assert vc.agreement(a, b) == vc.DISAGREE

    def test_failed_cell_inside_a_row_is_a_conflict_not_a_dropped_field(self):
        """Deliberate asymmetry with the top-level rule, and the reason is
        information: a table whose 20th cell faulted still holds 19 real values,
        so calling the whole field "not a rating" discards them. A conflict puts
        it in front of an adjudicator who can see which cell failed. This is also
        the pre-existing behaviour of the adjudication path, whose sentinel makes
        the row never-equal."""
        a = [{"drug": "aspirin", "n": _cell(None, absence.ERROR)}]
        b = [{"drug": "aspirin", "n": _cell("40", absence.REPORTED)}]
        assert vc.agreement(a, b) == vc.DISAGREE

    def test_nested_na_vs_nr_is_a_conflict(self):
        a = [{"drug": "aspirin", "ae": _cell("NA", absence.NOT_APPLICABLE)}]
        b = [{"drug": "aspirin", "ae": _cell("NR", absence.NOT_REPORTED)}]
        assert vc.agreement(a, b) == vc.DISAGREE

    def test_empty_table_is_incomparable(self):
        assert vc.agreement([], []) == vc.INCOMPARABLE

    def test_explicitly_empty_table_agrees(self):
        """An extractor that reports "this table is not present" wrote a status."""
        a = _cell("NR", absence.NOT_REPORTED)
        b = _cell([], absence.NOT_REPORTED)
        assert vc.agreement(a, b) == vc.AGREE


# ═══════════════════════════════════════════════════════════════════════════
# D4 — multi-select alignment
# ═══════════════════════════════════════════════════════════════════════════

class TestMultiselect:
    def test_list_agrees_with_comma_joined_string(self):
        assert vc.agreement(["Age ≥ 18", "T2DM"], "Age ≥ 18, T2DM") == vc.AGREE

    def test_alignment_is_order_insensitive(self):
        assert vc.agreement(["a", "b"], "b, a") == vc.AGREE

    def test_differing_members_disagree(self):
        assert vc.agreement(["a", "b"], "a, c") == vc.DISAGREE

    def test_absence_token_is_never_split(self):
        """`(["a","b"], "NR")` must stay a list-vs-gap disagreement. Splitting
        would give the same NR claim a different grouping token depending on what
        it happened to be compared against."""
        assert vc.agreement(["a", "b"], "NR") == vc.DISAGREE
        _, kb = vc.compare_pair(["a", "b"], "NR")
        assert kb == vc.agreement_token("NR"), "NR tokenized differently after alignment"

    def test_alignment_needs_both_sides(self):
        """A single-value tokenizer cannot see it — which is why `compare_pair`
        is the sanctioned way to get tokens for grouping."""
        assert vc.agreement_token(["a", "b"]) != vc.agreement_token("a, b")
        ka, kb = vc.compare_pair(["a", "b"], "a, b")
        assert ka == kb


# ═══════════════════════════════════════════════════════════════════════════
# The invariant that keeps the callers unified
# ═══════════════════════════════════════════════════════════════════════════

class TestTokenVerdictConsistency:
    CELLS = [
        "RCT", " rct ", "12", 12, 12.0, "yes", True, "no", False,
        "NR", "not reported", "NA", "Not applicable", "None", "", None, [],
        ["a", "b"], "a, b", "b, a",
        _cell("12", absence.REPORTED), _cell("NR", absence.NOT_REPORTED),
        _cell("NA", absence.NOT_APPLICABLE), _cell("", absence.ERROR),
        _cell(None, absence.MISSING), _cell("", absence.NOT_REPORTED),
        [{"drug": "aspirin", "n": "40"}], [{"drug": "aspirin", "n": "38"}],
    ]

    @pytest.mark.parametrize("i", range(len(CELLS)))
    def test_verdict_matches_token_equality(self, i):
        """`agreement(a,b) == AGREE` **iff** `compare_pair(a,b)` yields two equal
        non-None tokens, for every pair in the matrix. This is what makes the
        boolean verdict and the kappa category impossible to drift apart — the
        exact failure mode of the four-way split this module replaced."""
        a = self.CELLS[i]
        for b in self.CELLS:
            ka, kb = vc.compare_pair(a, b)
            verdict = vc.agreement(a, b)
            if ka is None or kb is None:
                assert verdict == vc.INCOMPARABLE
            else:
                assert verdict == (vc.AGREE if ka == kb else vc.DISAGREE)

    @pytest.mark.parametrize("i", range(len(CELLS)))
    def test_agreement_is_symmetric(self, i):
        a = self.CELLS[i]
        for b in self.CELLS:
            assert vc.agreement(a, b) == vc.agreement(b, a)

    @pytest.mark.parametrize("i", range(len(CELLS)))
    def test_comparable_cells_agree_with_themselves(self, i):
        """Reflexive except for failures and blanks, which are incomparable by
        design — that asymmetry is rules 1 and 2, not a bug."""
        a = self.CELLS[i]
        expected = vc.INCOMPARABLE if vc.agreement_token(a) is None else vc.AGREE
        assert vc.agreement(a, a) == expected


# ═══════════════════════════════════════════════════════════════════════════
# What counts as a field
# ═══════════════════════════════════════════════════════════════════════════

class TestComparableFields:
    def test_union_across_sources(self):
        assert vc.comparable_fields({"a": 1}, {"b": 2}, {"a": 3}) == {"a", "b"}

    def test_partial_flag_is_not_a_field(self):
        """`_partial` is a control flag stored inside extracted_data
        (api/v1/results.py:476). Counted as a field it inflates the denominator,
        always reads as disputed, and renders as a literal row in the review UI."""
        assert vc.comparable_fields({"age": 1, "_partial": True}, {"age": 2}) == {"age"}

    def test_tolerates_none_and_empty_sources(self):
        assert vc.comparable_fields(None, {}, {"a": 1}) == {"a"}


class TestNormalizeAiKeys:
    def test_value_suffix_is_stripped(self):
        assert vc.normalize_ai_keys({"age.value": 58}) == {"age": 58}

    def test_plain_key_without_a_value_sibling_survives(self):
        """The denominator fix: api/v1/results.py used to drop every plain key as
        soon as any `.value` key existed, so the dashboard counted fewer fields
        than the review screen did."""
        out = vc.normalize_ai_keys({"age.value": 58, "design": "RCT"})
        assert out == {"age": 58, "design": "RCT"}

    def test_plain_key_with_a_value_sibling_is_dropped(self):
        out = vc.normalize_ai_keys({"age.value": 58, "age": {"value": 58}})
        assert out == {"age": 58}

    def test_grounding_metadata_keys_are_dropped(self):
        """consensus/page.tsx kept these (nothing has an `age.source_text.value`
        sibling), so they surfaced in the review screen as pseudo-fields."""
        out = vc.normalize_ai_keys({
            "age.value": 58,
            "age.source_text": "mean age was 58",
            "age.source_location": {"page": 3},
            "age.confidence": 0.9,
            "age.reasoning": "stated in Table 1",
        })
        assert out == {"age": 58}

    def test_manual_row_passes_through_unchanged(self):
        manual = {"age": {"value": 58, "status": "reported"}, "design": "RCT"}
        assert vc.normalize_ai_keys(manual) == manual

    def test_empty_payload(self):
        assert vc.normalize_ai_keys({}) == {}
        assert vc.normalize_ai_keys(None) == {}
