"""The two review-scope columns are written together, or not at all.

`projects.review_scope` is the prose extraction reads (see test_review_scope.py
for that half). `projects.review_scope_structured` is only the guided builder's
chips, kept because composing them into prose is one-way — unticked pairs vanish
from the text and "A versus B" hides which side was the comparator.

The failure this pins is the two columns disagreeing: a free-text save leaving
stale chips behind, so the next visit renders a builder that composes to
something other than what the model is actually being sent.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest  # noqa: E402

from app.api.v1.projects import _normalize_review_scope  # noqa: E402
from app.models.schemas import ReviewScopeStructured  # noqa: E402

TEXT = (
    "Populations of interest: Adults with chronic periodontitis and Smokers.\n"
    "Comparisons of interest: SRP + antibiotics versus SRP alone."
)


def _chips(**kw):
    return ReviewScopeStructured(**kw)


class TestWhatGetsStored:
    """One row per line of the table in _normalize_review_scope's docstring."""

    def test_blank_text_clears_both(self):
        assert _normalize_review_scope(None, None) == (None, None)
        assert _normalize_review_scope("   \n\t ", None) == (None, None)

    def test_blank_text_with_empty_chips_clears_both(self):
        # "Clear all" then save: the builder sends an empty draft, both go null.
        assert _normalize_review_scope("", _chips()) == (None, None)

    def test_free_text_save_wipes_stale_chips(self):
        # The whole point: no structured payload means "this is free text now".
        assert _normalize_review_scope(TEXT, None) == (TEXT, None)

    def test_empty_builder_stores_no_chips(self):
        scope, structured = _normalize_review_scope(TEXT, _chips())
        assert (scope, structured) == (TEXT, None)

    def test_pairs_off_alone_is_still_an_empty_builder(self):
        # pairs_off without any list is not a scope anyone can render.
        scope, structured = _normalize_review_scope(TEXT, _chips(pairs_off=["a⚔b"]))
        assert (scope, structured) == (TEXT, None)

    def test_text_plus_chips_stores_both(self):
        scope, structured = _normalize_review_scope(
            TEXT, _chips(populations=["Adults"], interventions=["SRP + antibiotics"])
        )
        assert scope == TEXT
        assert structured["populations"] == ["Adults"]
        assert structured["interventions"] == ["SRP + antibiotics"]
        assert structured["comparators"] == []

    def test_chips_without_text_is_rejected(self):
        # Storing chips whose prose is blank would show a full builder while the
        # model receives no scope at all — a 400 beats that silent divergence.
        with pytest.raises(ValueError):
            _normalize_review_scope(None, _chips(outcomes=["Probing pocket depth"]))
        with pytest.raises(ValueError):
            _normalize_review_scope("   ", _chips(populations=["Adults"]))

    def test_text_is_trimmed(self):
        assert _normalize_review_scope("  scope  ", None) == ("scope", None)


class TestChipCleaning:
    """ReviewScopeStructured normalizes entries before they reach the column."""

    def test_entries_are_trimmed_and_blanks_dropped(self):
        c = _chips(outcomes=["  Probing pocket depth  ", "", "   ", "CAL"])
        assert c.outcomes == ["Probing pocket depth", "CAL"]

    def test_duplicates_collapse_preserving_order(self):
        c = _chips(timepoints=["6 months", "3 months", "6 months"])
        assert c.timepoints == ["6 months", "3 months"]

    def test_overlong_entry_is_rejected(self):
        with pytest.raises(Exception):
            _chips(populations=["x" * 301])

    def test_unknown_keys_are_rejected(self):
        # A client bug should 422, not land silently in JSONB the builder
        # will later fail to render.
        with pytest.raises(Exception):
            ReviewScopeStructured(populations=["Adults"], interventionz=["typo"])

    def test_all_six_keys_survive_the_round_trip(self):
        c = _chips(
            populations=["P"], interventions=["I"], comparators=["C"],
            outcomes=["O"], timepoints=["T"], pairs_off=["I⚔C"],
        )
        assert set(c.model_dump()) == {
            "populations", "interventions", "comparators",
            "outcomes", "timepoints", "pairs_off",
        }


class TestSuggesterAgreesWithTheSaveModel:
    """The document suggester and the save endpoint must not drift apart.

    `utils/scope_suggestion` drops a chip that `ReviewScopeStructured` would
    reject, so the guided builder can never be handed entries the save call
    will refuse — the reviewer would see a full builder and a 422.
    """

    def test_entry_and_family_caps_are_the_same_numbers(self):
        from utils.scope_suggestion import MAX_ENTRY_CHARS, MAX_PER_FAMILY

        field = ReviewScopeStructured.model_fields["outcomes"]
        family_cap = next(
            m.max_length for m in field.metadata if getattr(m, "max_length", None)
        )
        assert family_cap == MAX_PER_FAMILY

        # An over-long entry does not truncate on save, it REJECTS the whole
        # payload — which is why the suggester drops such a chip instead of
        # handing it to the builder for the reviewer to trip over.
        with pytest.raises(Exception):
            ReviewScopeStructured(outcomes=["x" * (MAX_ENTRY_CHARS + 1)])

    def test_suggested_chips_round_trip_into_the_save_payload(self):
        from utils.scope_suggestion import validate_suggestion
        from core.generators.models import ScopeChip, ScopeSuggestion

        doc = "Adults with chronic periodontitis were compared with SRP alone at 6 months."
        out = validate_suggestion(
            ScopeSuggestion(chips=[
                ScopeChip(family="population", value="Adults with chronic periodontitis",
                          evidence="Adults with chronic periodontitis were compared"),
                ScopeChip(family="comparator", value="SRP alone",
                          evidence="were compared with SRP alone at 6 months"),
            ]),
            doc,
        )
        # Same singular -> plural mapping the frontend's FAMILY_KEY performs.
        keys = {"population": "populations", "comparator": "comparators"}
        lists = {}
        for chip in out["chips"]:
            lists.setdefault(keys[chip["family"]], []).append(chip["value"])

        stored = ReviewScopeStructured(**lists)
        assert stored.populations == ["Adults with chronic periodontitis"]
        assert stored.comparators == ["SRP alone"]
