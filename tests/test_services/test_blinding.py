"""Blinding rules pinned against the matrix in blinding_service._decide_visible_rows.

This is the server-side authority for what a reviewer can see. There was no
test coverage for it before this file — these pin the invariant that matters
to a clinician: an assigned reviewer_1 must never receive reviewer_2's raw
manual extraction (and vice versa) once blinding is turned on, regardless of
how many documents/projects are being resolved at once.

`_decide_visible_rows` is pure (no I/O) by design — the review_settings/
assignment lookups happen once per project/batch in the callers
(`filter_results_for_user`, `_apply_blinding_grouped` in results.py), not once
per row, so these tests need no DB, no event loop, no mocking.
"""

import os
import sys
from uuid import uuid4

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from app.services.blinding_service import _decide_visible_rows, _pick_assignment  # noqa: E402


USER = uuid4()
OTHER_USER = uuid4()


def _ai_row():
    return {"extraction_type": "ai", "extracted_by": None, "reviewer_role": None}


def _manual_row(role, author=OTHER_USER):
    return {"extraction_type": "manual", "extracted_by": str(author), "reviewer_role": role}


def _rows():
    """One AI row + one manual row per reviewer role, none authored by USER."""
    return [
        _ai_row(),
        _manual_row("reviewer_1"),
        _manual_row("reviewer_2"),
    ]


def _assignment(role):
    return {"reviewer_role": role, "reviewer_user_id": str(USER)}


def _types(rows):
    return sorted((r["extraction_type"], r.get("reviewer_role")) for r in rows)


# ═══════════════════════════════════════════════════════════════════════════
# blinding == "none" (default) — everyone sees everything, hide_ai is orthogonal
# ═══════════════════════════════════════════════════════════════════════════

class TestBlindingOff:
    def test_none_shows_everything_regardless_of_assignment(self):
        rows = _rows()
        for assignment in (None, _assignment("reviewer_1"), _assignment("adjudicator")):
            out = _decide_visible_rows(rows, USER, "member", {"blinding": "none"}, assignment)
            assert _types(out) == _types(rows)

    def test_none_with_hide_ai_still_hides_ai_only(self):
        rows = _rows()
        out = _decide_visible_rows(rows, USER, "member", {"blinding": "none", "hide_ai_results": True}, None)
        assert all(r["extraction_type"] != "ai" for r in out)
        assert len(out) == 2  # both manual rows remain — "none" means no reviewer blinding

    def test_missing_blinding_key_defaults_to_none(self):
        """review_settings == {} (a fresh project) must behave as blinding=none, not crash."""
        out = _decide_visible_rows(_rows(), USER, "member", {}, None)
        assert len(out) == 3


# ═══════════════════════════════════════════════════════════════════════════
# blinding == "full" / "partial" — unassigned caller
# ═══════════════════════════════════════════════════════════════════════════

class TestUnassignedCaller:
    @pytest.mark.parametrize("blinding", ["full", "partial"])
    @pytest.mark.parametrize("role", ["owner", "manager", "admin"])
    def test_unassigned_privileged_role_sees_everything(self, blinding, role):
        out = _decide_visible_rows(_rows(), USER, role, {"blinding": blinding}, None)
        assert _types(out) == _types(_rows())

    @pytest.mark.parametrize("blinding", ["full", "partial"])
    def test_unassigned_privileged_role_still_respects_hide_ai(self, blinding):
        out = _decide_visible_rows(
            _rows(), USER, "owner", {"blinding": blinding, "hide_ai_results": True}, None
        )
        assert all(r["extraction_type"] != "ai" for r in out)

    @pytest.mark.parametrize("blinding", ["full", "partial"])
    @pytest.mark.parametrize("role", ["member", "viewer"])
    def test_unassigned_ordinary_role_sees_ai_only(self, blinding, role):
        out = _decide_visible_rows(_rows(), USER, role, {"blinding": blinding}, None)
        assert _types(out) == [("ai", None)]

    @pytest.mark.parametrize("blinding", ["full", "partial"])
    def test_unassigned_missing_role_fails_closed(self, blinding):
        """A missing viewer_role must be treated as an ordinary member, never
        assumed to be owner-level — this is the fail-closed contract in the
        docstring. Regression target: don't let `None in (...)` accidentally
        become truthy for privileged roles."""
        out = _decide_visible_rows(_rows(), USER, None, {"blinding": blinding}, None)
        assert _types(out) == [("ai", None)]


# ═══════════════════════════════════════════════════════════════════════════
# blinding == "full" / "partial" — assigned adjudicator
# ═══════════════════════════════════════════════════════════════════════════

class TestAdjudicator:
    @pytest.mark.parametrize("blinding", ["full", "partial"])
    def test_adjudicator_is_never_blinded(self, blinding):
        out = _decide_visible_rows(_rows(), USER, "member", {"blinding": blinding}, _assignment("adjudicator"))
        assert _types(out) == _types(_rows())

    @pytest.mark.parametrize("blinding", ["full", "partial"])
    def test_adjudicator_still_respects_hide_ai(self, blinding):
        out = _decide_visible_rows(
            _rows(), USER, "member", {"blinding": blinding, "hide_ai_results": True}, _assignment("adjudicator")
        )
        assert all(r["extraction_type"] != "ai" for r in out)


# ═══════════════════════════════════════════════════════════════════════════
# blinding == "full" / "partial" — assigned reviewer: the core invariant
# ═══════════════════════════════════════════════════════════════════════════

class TestAssignedReviewer:
    @pytest.mark.parametrize("blinding", ["full", "partial"])
    def test_reviewer_1_never_sees_reviewer_2_manual_row(self, blinding):
        out = _decide_visible_rows(_rows(), USER, "member", {"blinding": blinding}, _assignment("reviewer_1"))
        assert ("manual", "reviewer_2") not in _types(out)

    @pytest.mark.parametrize("blinding", ["full", "partial"])
    def test_reviewer_2_never_sees_reviewer_1_manual_row(self, blinding):
        out = _decide_visible_rows(_rows(), USER, "member", {"blinding": blinding}, _assignment("reviewer_2"))
        assert ("manual", "reviewer_1") not in _types(out)

    @pytest.mark.parametrize("blinding", ["full", "partial"])
    def test_reviewer_sees_ai_unless_hidden(self, blinding):
        visible = _decide_visible_rows(_rows(), USER, "member", {"blinding": blinding}, _assignment("reviewer_1"))
        hidden = _decide_visible_rows(
            _rows(), USER, "member", {"blinding": blinding, "hide_ai_results": True}, _assignment("reviewer_1")
        )
        assert ("ai", None) in _types(visible)
        assert ("ai", None) not in _types(hidden)

    @pytest.mark.parametrize("blinding", ["full", "partial"])
    def test_own_row_always_visible_even_if_role_tag_is_stale(self, blinding):
        """extracted_by is checked before the role match — a self-authored row
        must never be hidden from its own author, even if its stored
        reviewer_role tag doesn't match the caller's current assignment
        (handles role swaps / legacy untagged rows without re-tagging data)."""
        own_row_wrong_tag = _manual_row("reviewer_2", author=USER)
        out = _decide_visible_rows(
            [own_row_wrong_tag], USER, "member", {"blinding": blinding}, _assignment("reviewer_1")
        )
        assert out == [own_row_wrong_tag]


# ═══════════════════════════════════════════════════════════════════════════
# _pick_assignment — adjudicator precedence when a user holds two rows
# ═══════════════════════════════════════════════════════════════════════════

class TestPickAssignment:
    def test_no_rows_returns_none(self):
        assert _pick_assignment([]) is None

    def test_single_row_returned_as_is(self):
        row = {"reviewer_role": "reviewer_1"}
        assert _pick_assignment([row]) is row

    def test_adjudicator_wins_over_reviewer_row(self):
        """A user can legitimately hold both an adjudicator row and a reviewer
        row for the same document; the adjudicator row must take precedence
        regardless of list order — an adjudicator must never be blinded."""
        reviewer_row = {"reviewer_role": "reviewer_1"}
        adjudicator_row = {"reviewer_role": "adjudicator"}
        assert _pick_assignment([reviewer_row, adjudicator_row]) is adjudicator_row
        assert _pick_assignment([adjudicator_row, reviewer_row]) is adjudicator_row
