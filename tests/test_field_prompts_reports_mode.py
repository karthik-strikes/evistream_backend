"""The /field-prompts response must report the extraction mode and composite key
that the extractor will ACTUALLY use.

Why this endpoint owns that
---------------------------
The form editor used to read a table field's mode from `forms.fields`, which is
only a mirror. `forms.schema_def` is what build_schema_classes compiles from,
and the two drifted: 42 live table fields carried `row_then_columns` in
schema_def and nothing in fields, so the editor rendered "Fast - 1 model call"
for fields running the 3-call keyed pipeline.

Serving the mode from schema_def here makes that class of misreport impossible:
the editor now reads the same column the extractor does. This test pins that,
including the `anchor_columns`-only shape that older forms still store.
"""

import os
import sys
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _schema_def(strategy, *, key_field="key_columns"):
    """A one-signature schema_def holding a single keyed table field."""
    out_field = {
        "name": "outcomes",
        "type": "List[Dict[str, Any]]",
        "description": "Outcome rows.",
        "subform_fields": [
            {"field_name": "outcome_name", "field_type": "text", "field_description": "Name"},
            {"field_name": "timepoint", "field_type": "text", "field_description": "When"},
            {"field_name": "mean", "field_type": "number", "field_description": "Mean"},
        ],
    }
    if strategy:
        out_field["extraction_strategy"] = strategy
    if key_field:
        out_field[key_field] = ["outcome_name", "timepoint"]
    return {"signatures": [{"class_name": "ExtractOutcomes", "output_fields": [out_field]}]}


@pytest.fixture
def call_endpoint():
    """Drive the real route with a stubbed DB row and an authenticated user."""
    from app.dependencies import get_current_user
    from app.main import app
    import app.api.v1.forms as forms_api

    def _call(schema_def, status_value="active"):
        row = {
            "id": str(uuid4()),
            "form_name": "Test form",
            "status": status_value,
            "schema_def": schema_def,
            "fields": [],
            "project_id": str(uuid4()),
        }
        sb = MagicMock()
        sb.table.return_value.select.return_value.eq.return_value.execute.return_value.data = [row]

        app.dependency_overrides[get_current_user] = lambda: uuid4()
        try:
            with patch.object(forms_api, "supabase", sb), \
                 patch.object(forms_api, "_check_can_edit_field_prompts", return_value=None):
                client = TestClient(app)
                resp = client.get(f"/api/v1/forms/{row['id']}/field-prompts")
            assert resp.status_code == 200, resp.text
            return resp.json()["field_prompts"]["outcomes"]
        finally:
            app.dependency_overrides.pop(get_current_user, None)

    return _call


def test_rigorous_mode_is_reported(call_endpoint):
    fp = call_endpoint(_schema_def("row_then_columns"))
    assert fp["extraction_strategy"] == "row_then_columns"
    assert fp["key_columns"] == ["outcome_name", "timepoint"]


def test_legacy_anchor_columns_spelling_is_still_reported(call_endpoint):
    """Older forms store the key under `anchor_columns` only. The endpoint reads
    through field_key_columns(), so both spellings surface identically."""
    fp = call_endpoint(_schema_def("row_then_columns", key_field="anchor_columns"))
    assert fp["key_columns"] == ["outcome_name", "timepoint"]


def test_field_with_no_stored_mode_reports_null_not_a_default(call_endpoint):
    """Absent must stay absent. Inventing 'single_call' here would recreate the
    original bug in a new place: the UI would confidently show a mode the
    backend never agreed to."""
    fp = call_endpoint(_schema_def(None, key_field=None))
    assert fp["extraction_strategy"] is None
    assert fp["key_columns"] is None
