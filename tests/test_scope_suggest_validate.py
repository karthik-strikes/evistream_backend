"""What a model proposes for the review scope is not what the reviewer sees.

The scope goes verbatim into every signature docstring in the project
(`runtime_builders.apply_review_scope`), so the failure being pinned here is a
chip the model invented arriving in the builder looking exactly like a chip it
read. `utils/scope_suggestion.validate_suggestion` is the gate.

The one asymmetry worth knowing: an unquotable chip is KEPT and flagged, not
dropped. PDF table extraction mangles whitespace often enough that dropping
would lose correct chips, and a flagged chip only costs the reviewer a click.
"""

import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest  # noqa: E402

from core.generators.models import ScopeChip, ScopeSuggestion  # noqa: E402
from utils.scope_suggestion import (  # noqa: E402
    MAX_ENTRY_CHARS,
    MAX_PER_FAMILY,
    validate_suggestion,
)

DOC = """
--- protocol.pdf ---
Types of participants: adults with chronic periodontitis, including smokers with
chronic periodontitis. We will exclude patients with uncontrolled diabetes.

Types of interventions: scaling and root planing (SRP) plus systemic
antibiotics, compared with SRP alone.

Primary outcome: probing pocket depth (PPD) at 3 and 6 months.
Secondary outcomes: clinical attachment level and bleeding on probing.
"""


def _sug(chips, **kw):
    return ScopeSuggestion(chips=chips, **kw)


def _chip(family, value, evidence, confidence="high"):
    return ScopeChip(family=family, value=value, evidence=evidence, confidence=confidence)


class TestGrounding:
    def test_quoted_chip_keeps_its_confidence(self):
        out = validate_suggestion(
            _sug([_chip("population", "Adults with chronic periodontitis",
                        "adults with chronic periodontitis, including smokers")]),
            DOC,
        )
        assert out["chips"] == [{
            "family": "population",
            "value": "Adults with chronic periodontitis",
            "evidence": "adults with chronic periodontitis, including smokers",
            "confidence": "high",
            "unverified": False,
        }]

    def test_invented_chip_is_kept_but_flagged_and_demoted(self):
        """Not dropped — see the module docstring. Flagged is the contract."""
        out = validate_suggestion(
            _sug([_chip("outcome", "Quality of life",
                        "quality of life measured with the OHIP-14 questionnaire")]),
            DOC,
        )
        assert len(out["chips"]) == 1
        assert out["chips"][0]["unverified"] is True
        assert out["chips"][0]["confidence"] == "low"
        assert out["dropped"] == []

    def test_quote_survives_mangled_whitespace_and_punctuation(self):
        """A quote copied out of a PDF rarely matches byte-for-byte."""
        out = validate_suggestion(
            _sug([_chip("outcome", "Probing pocket depth",
                        "Probing   pocket\ndepth (PPD)  at 3 and 6 months")]),
            DOC,
        )
        assert out["chips"][0]["unverified"] is False

    def test_too_short_a_quote_proves_nothing(self):
        """'6 months' is in every protocol ever written."""
        out = validate_suggestion(
            _sug([_chip("timepoint", "6 months", "6 months")]), DOC
        )
        assert out["chips"][0]["unverified"] is True

    def test_long_quote_matching_only_at_its_head_still_counts(self):
        """Spanning a page break or an interleaved table cell is not lying."""
        out = validate_suggestion(
            _sug([_chip("intervention", "SRP plus systemic antibiotics",
                        "scaling and root planing (SRP) plus systemic antibiotics, "
                        "administered for seven days starting on the day of debridement")]),
            DOC,
        )
        assert out["chips"][0]["unverified"] is False


class TestWhatGetsDropped:
    def test_family_outside_the_five_is_dropped(self):
        """The Literal blocks this at the model layer; the check is belt and braces
        for a hand-built or future non-structured caller."""
        out = validate_suggestion(
            SimpleNamespace(
                chips=[SimpleNamespace(family="study_design", value="RCTs only",
                                       evidence="randomised controlled trials", confidence="high")],
                not_used=[], needs_review=[], notes="",
            ),
            DOC,
        )
        assert out["chips"] == []
        assert "not a scope family" in out["dropped"][0]

    def test_overlong_value_is_dropped_because_save_would_reject_it(self):
        out = validate_suggestion(
            _sug([_chip("population", "x" * (MAX_ENTRY_CHARS + 1), "adults with chronic periodontitis")]),
            DOC,
        )
        assert out["chips"] == []
        assert f"longer than {MAX_ENTRY_CHARS}" in out["dropped"][0]

    def test_family_cap_matches_the_save_model(self):
        chips = [
            _chip("outcome", f"Outcome {i}", "secondary outcomes: clinical attachment level")
            for i in range(MAX_PER_FAMILY + 5)
        ]
        out = validate_suggestion(_sug(chips), DOC)
        assert len(out["chips"]) == MAX_PER_FAMILY
        assert len(out["dropped"]) == 5

    def test_duplicates_collapse_case_insensitively_without_complaint(self):
        out = validate_suggestion(
            _sug([
                _chip("comparator", "SRP alone", "compared with SRP alone"),
                _chip("comparator", "srp ALONE", "compared with SRP alone"),
            ]),
            DOC,
        )
        assert [c["value"] for c in out["chips"]] == ["SRP alone"]
        assert out["dropped"] == []

    def test_blank_value_vanishes_quietly(self):
        out = validate_suggestion(_sug([_chip("outcome", "   ", "secondary outcomes")]), DOC)
        assert out["chips"] == []


class TestValueCleanup:
    @pytest.mark.parametrize("raw,clean", [
        ("- Probing pocket depth", "Probing pocket depth"),
        ("1. Probing pocket depth", "Probing pocket depth"),
        ("(2) Probing pocket depth", "Probing pocket depth"),
        ("Probing pocket depth.", "Probing pocket depth"),
        ('"Probing pocket depth"', "Probing pocket depth"),
        ("Probing   pocket\n depth", "Probing pocket depth"),
    ])
    def test_copied_markup_is_stripped(self, raw, clean):
        out = validate_suggestion(
            _sug([_chip("outcome", raw, "primary outcome: probing pocket depth (PPD)")]), DOC
        )
        assert out["chips"][0]["value"] == clean


class TestPassthroughFields:
    def test_exclusions_and_ambiguities_reach_the_reviewer(self):
        out = validate_suggestion(
            _sug(
                [],
                not_used=["Excluded: uncontrolled diabetes", "Excluded: uncontrolled diabetes"],
                needs_review=["'SRP alone' could be a comparator or an intervention"],
                notes="Scope taken from  the Methods  section of protocol.pdf.",
            ),
            DOC,
        )
        assert out["not_used"] == ["Excluded: uncontrolled diabetes"]
        assert out["needs_review"] == ["'SRP alone' could be a comparator or an intervention"]
        assert out["notes"] == "Scope taken from the Methods section of protocol.pdf."

    def test_model_cannot_mute_comparisons(self):
        """pairs_off is a human judgment about what the review is for. The
        suggestion model is never asked for one and cannot smuggle one in."""
        assert "pairs_off" not in ScopeSuggestion.model_fields
        out = validate_suggestion(_sug([]), DOC)
        assert "pairs_off" not in out
