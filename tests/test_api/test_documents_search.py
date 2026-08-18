"""
Route-level tests for GET /api/v1/documents search — specifically the
filename-OR-label matching behavior.

Regression coverage for a real bug: the route used to push a DB-level
`.ilike("filename", ...)` filter before falling back to an in-memory
filename-or-label check — so a document that matched by LABEL but not
filename was excluded before the label check ever ran. Fixed by doing all
search matching in Python and pushing the DB `.range()` pagination past
"no search" only.
"""

import os
import sys
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch
from uuid import uuid4

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import pytest
from fastapi.testclient import TestClient

from app.dependencies import get_current_user
from app.main import app

FAKE_USER_ID = uuid4()
FAKE_PROJECT_ID = str(uuid4())


@pytest.fixture
def client():
    app.dependency_overrides[get_current_user] = lambda: FAKE_USER_ID
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.pop(get_current_user, None)


def _doc_row(filename="paper.pdf", labels=None, **overrides):
    row = {
        "id": str(uuid4()),
        "project_id": FAKE_PROJECT_ID,
        "ref_id": 1,
        "filename": filename,
        "unique_filename": None,
        "s3_pdf_path": None,
        "s3_markdown_path": None,
        "processing_status": "completed",
        "processing_error": None,
        "labels": labels or [],
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    row.update(overrides)
    return row


class TestDocumentSearchMatchesLabelsNotJustFilename:
    def test_label_only_match_is_returned(self, client):
        """The exact bug: a document whose filename does NOT contain the
        search term, but whose labels DO, must still be returned."""
        mock_supabase = MagicMock()
        docs = [
            _doc_row(filename="unrelated_name.pdf", labels=["ibuprofen", "rct"]),
            _doc_row(filename="something_else.pdf", labels=["placebo"]),
        ]
        mock_supabase.table.return_value.select.return_value.eq.return_value.order.return_value.execute.return_value.data = docs

        with patch("app.api.v1.documents.supabase", mock_supabase), patch(
            "app.api.v1.documents.check_project_access"
        ):
            resp = client.get(f"/api/v1/documents?project_id={FAKE_PROJECT_ID}&search=ibuprofen")

        assert resp.status_code == 200
        body = resp.json()
        assert len(body) == 1
        assert body[0]["filename"] == "unrelated_name.pdf"

    def test_filename_match_still_works(self, client):
        mock_supabase = MagicMock()
        docs = [
            _doc_row(filename="ibuprofen_trial.pdf", labels=[]),
            _doc_row(filename="unrelated.pdf", labels=[]),
        ]
        mock_supabase.table.return_value.select.return_value.eq.return_value.order.return_value.execute.return_value.data = docs

        with patch("app.api.v1.documents.supabase", mock_supabase), patch(
            "app.api.v1.documents.check_project_access"
        ):
            resp = client.get(f"/api/v1/documents?project_id={FAKE_PROJECT_ID}&search=ibuprofen")

        assert resp.status_code == 200
        body = resp.json()
        assert len(body) == 1
        assert body[0]["filename"] == "ibuprofen_trial.pdf"

    def test_search_is_case_insensitive_on_labels(self, client):
        mock_supabase = MagicMock()
        docs = [_doc_row(filename="x.pdf", labels=["RCT"])]
        mock_supabase.table.return_value.select.return_value.eq.return_value.order.return_value.execute.return_value.data = docs

        with patch("app.api.v1.documents.supabase", mock_supabase), patch(
            "app.api.v1.documents.check_project_access"
        ):
            resp = client.get(f"/api/v1/documents?project_id={FAKE_PROJECT_ID}&search=rct")

        assert resp.status_code == 200
        assert len(resp.json()) == 1

    def test_no_match_returns_empty(self, client):
        mock_supabase = MagicMock()
        docs = [_doc_row(filename="x.pdf", labels=["placebo"])]
        mock_supabase.table.return_value.select.return_value.eq.return_value.order.return_value.execute.return_value.data = docs

        with patch("app.api.v1.documents.supabase", mock_supabase), patch(
            "app.api.v1.documents.check_project_access"
        ):
            resp = client.get(f"/api/v1/documents?project_id={FAKE_PROJECT_ID}&search=nonexistent")

        assert resp.status_code == 200
        assert resp.json() == []

    def test_no_search_term_returns_all_unfiltered(self, client):
        mock_supabase = MagicMock()
        docs = [_doc_row(filename="a.pdf"), _doc_row(filename="b.pdf")]
        # No-search path applies .range() at the DB level.
        mock_supabase.table.return_value.select.return_value.eq.return_value.order.return_value.range.return_value.execute.return_value.data = docs

        with patch("app.api.v1.documents.supabase", mock_supabase), patch(
            "app.api.v1.documents.check_project_access"
        ):
            resp = client.get(f"/api/v1/documents?project_id={FAKE_PROJECT_ID}")

        assert resp.status_code == 200
        assert len(resp.json()) == 2

    def test_search_pagination_applied_after_filtering(self, client):
        """limit/offset must slice the FILTERED set, not the raw fetch —
        otherwise a label match past the first `limit` unfiltered rows
        would be silently dropped."""
        mock_supabase = MagicMock()
        # 3 documents all match "rct", but limit=1 should return only 1.
        docs = [_doc_row(filename=f"doc{i}.pdf", labels=["rct"]) for i in range(3)]
        mock_supabase.table.return_value.select.return_value.eq.return_value.order.return_value.execute.return_value.data = docs

        with patch("app.api.v1.documents.supabase", mock_supabase), patch(
            "app.api.v1.documents.check_project_access"
        ):
            resp = client.get(f"/api/v1/documents?project_id={FAKE_PROJECT_ID}&search=rct&limit=1&offset=1")

        assert resp.status_code == 200
        body = resp.json()
        assert len(body) == 1
        assert body[0]["filename"] == "doc1.pdf"
