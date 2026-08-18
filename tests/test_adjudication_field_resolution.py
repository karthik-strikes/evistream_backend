"""
`field_resolutions` validation on the adjudication save request.

`resolution_source` records *how* an adjudicator settled a field, and it is the
provenance of `final_value` for `data_cleaning_service` and every downstream
export. It was previously an unvalidated `str` inside a `Dict[str, Any]`, so a
mislabeled value was stored silently — the consensus screen had been sending two
labels (`ai`, `suggestion`) that were absent from the declared vocabulary the
whole time.

Two things must both hold, and they pull in opposite directions:
  * an unknown `resolution_source` must be rejected;
  * typing the payload must not narrow what the JSONB column can store — Pydantic
    v2 defaults to `extra="ignore"`, so declaring the model without
    `extra="allow"` would turn validation into silent data loss.
"""

import os
import sys

import pytest
from pydantic import ValidationError

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.models.schemas import (  # noqa: E402
    AdjudicationResolveRequest,
    FieldResolution,
)

_BASE = {
    "project_id": "11111111-1111-1111-1111-111111111111",
    "form_id": "22222222-2222-2222-2222-222222222222",
    "document_id": "33333333-3333-3333-3333-333333333333",
}


def _request(resolutions, **kw):
    return AdjudicationResolveRequest(**_BASE, field_resolutions=resolutions, **kw)


class TestResolutionSourceVocabulary:
    @pytest.mark.parametrize("source", [
        "agreed", "ai", "reviewer_1", "reviewer_2", "majority", "suggestion",
        "custom", "not_reported", "not_applicable",
    ])
    def test_every_declared_source_is_accepted(self, source):
        req = _request({"age": {"final_value": "58", "resolution_source": source}})
        assert req.field_resolutions["age"].resolution_source == source

    @pytest.mark.parametrize("source", ["ai", "suggestion"])
    def test_the_labels_the_frontend_was_already_sending(self, source):
        """These were outside the old declared union. Wiring the model in before
        widening it would have 422'd every live dual-reviewer save."""
        _request({"age": {"final_value": "58", "resolution_source": source}})

    def test_unknown_source_is_rejected(self):
        with pytest.raises(ValidationError):
            _request({"age": {"final_value": "58", "resolution_source": "vibes"}})

    def test_default_is_agreed(self):
        req = _request({"age": {"final_value": "58"}})
        assert req.field_resolutions["age"].resolution_source == "agreed"


class TestNoDataLoss:
    def test_undeclared_keys_survive(self):
        """The column is JSONB and used to store the payload verbatim. extra=allow
        keeps that true."""
        req = _request({"age": {
            "final_value": "58",
            "resolution_source": "custom",
            "some_future_key": {"nested": [1, 2, 3]},
        }})
        dumped = req.field_resolutions["age"].model_dump(mode="json")
        assert dumped["some_future_key"] == {"nested": [1, 2, 3]}

    def test_falsy_final_values_are_preserved(self):
        """0 and False are findings, not absences — a resolver that coerced them
        away would silently change the recorded answer."""
        for value in (0, False, "", []):
            req = _request({"n": {"final_value": value, "resolution_source": "custom"}})
            assert req.field_resolutions["n"].model_dump(mode="json")["final_value"] == value

    def test_table_rows_round_trip(self):
        rows = [{"drug": "aspirin", "n": "40"}, {"drug": "placebo", "n": "38"}]
        req = _request({"arms": {"final_value": rows, "resolution_source": "reviewer_1"}})
        assert req.field_resolutions["arms"].model_dump(mode="json")["final_value"] == rows

    def test_dump_shape_is_uniform(self):
        """Every declared key is present after the dump even when the client omits
        it, so stored rows stop varying in shape by client version."""
        req = _request({"age": {"final_value": "58", "resolution_source": "ai"}})
        dumped = req.field_resolutions["age"].model_dump(mode="json")
        assert set(dumped) >= {
            "reviewer_1_value", "reviewer_2_value", "agreed", "final_value",
            "resolution_source", "adjudicator_note",
        }


class TestStatus:
    def test_completed_and_in_progress_accepted(self):
        for s in ("in_progress", "completed"):
            assert _request({}, status=s).status == s

    def test_default_is_in_progress(self):
        assert _request({}).status == "in_progress"

    def test_unknown_status_is_rejected(self):
        """The DB has CHECK (status IN ('in_progress','completed')), so an invalid
        value used to surface as an opaque 500 from the generic handler."""
        with pytest.raises(ValidationError):
            _request({}, status="done")


class TestFieldResolutionDirectly:
    def test_agreed_defaults_false(self):
        assert FieldResolution().agreed is False

    def test_reviewer_values_default_to_none(self):
        r = FieldResolution()
        assert r.reviewer_1_value is None and r.reviewer_2_value is None
