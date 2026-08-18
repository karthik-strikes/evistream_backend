"""
Route-level tests for /api/v1/pubmed/*.

Uses FastAPI's TestClient (sync, drives async routes internally — no
pytest-asyncio needed) with `get_current_user` overridden via
`app.dependency_overrides`, and mocks the PubMed service + Supabase/storage
calls via unittest.mock — same convention as test_clinical_trials.py.
"""

import os
import sys
from unittest.mock import MagicMock, patch
from uuid import uuid4

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import pytest
from fastapi import HTTPException, status
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


def _normalized_stub(pmid="34878953"):
    return {
        "pmid": pmid,
        "sourceUrl": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
        "title": "Test Article",
        "authors": ["Doe J", "Smith A"],
        "journal": "Test Journal",
        "pubDate": "2022 Jun",
        "year": "2022",
        "doi": "10.1234/test",
        "pubTypes": ["Journal Article"],
        "abstractText": "This is the abstract.",
    }


class TestPmidValidation:
    def test_invalid_pmid_returns_400(self, client):
        resp = client.get("/api/v1/pubmed/not-a-pmid")
        assert resp.status_code == 400

    def test_invalid_pmid_on_raw_returns_400(self, client):
        resp = client.get("/api/v1/pubmed/not-a-pmid/raw")
        assert resp.status_code == 400

    def test_nine_digit_pmid_rejected(self, client):
        """PMID_RE caps at 8 digits — a 9-digit string should 400, not
        silently pass through to the upstream call."""
        resp = client.get("/api/v1/pubmed/123456789")
        assert resp.status_code == 400


class TestGetArticle:
    def test_upstream_404_propagates(self, client):
        with patch(
            "app.api.v1.pubmed.pubmed_service.get_article_normalized",
            side_effect=HTTPException(status_code=404, detail="PMID 99999999 not found."),
        ):
            resp = client.get("/api/v1/pubmed/99999999")
        assert resp.status_code == 404

    def test_upstream_timeout_maps_to_502(self, client):
        with patch(
            "app.api.v1.pubmed.pubmed_service.get_article_normalized",
            side_effect=HTTPException(status_code=502, detail="PubMed is unreachable."),
        ):
            resp = client.get("/api/v1/pubmed/34878953")
        assert resp.status_code == 502

    def test_success_returns_normalized_article(self, client):
        """fulltext_service.probe_full_text_availability must be mocked —
        otherwise this route would make a real Unpaywall/PMC network call
        for the stub's fake DOI on every test run."""
        stub = _normalized_stub()
        with patch("app.api.v1.pubmed.pubmed_service.get_article_normalized", return_value=stub), patch(
            "app.api.v1.pubmed.fulltext_service.probe_full_text_availability", return_value="none"
        ):
            resp = client.get("/api/v1/pubmed/34878953")
        assert resp.status_code == 200
        assert resp.json()["pmid"] == "34878953"
        assert resp.json()["abstractText"] == "This is the abstract."
        assert resp.json()["fullTextAvailability"] == "none"


class TestFullTextAvailabilityProbe:
    def test_pdf_available_surfaces_in_response(self, client):
        stub = _normalized_stub()
        with patch("app.api.v1.pubmed.pubmed_service.get_article_normalized", return_value=stub), patch(
            "app.api.v1.pubmed.fulltext_service.probe_full_text_availability", return_value="pdf"
        ) as mock_probe:
            resp = client.get("/api/v1/pubmed/34878953")
        assert resp.status_code == 200
        assert resp.json()["fullTextAvailability"] == "pdf"
        mock_probe.assert_called_once_with(stub["doi"], "34878953")

    def test_pmc_available_surfaces_in_response(self, client):
        stub = _normalized_stub()
        with patch("app.api.v1.pubmed.pubmed_service.get_article_normalized", return_value=stub), patch(
            "app.api.v1.pubmed.fulltext_service.probe_full_text_availability", return_value="pmc"
        ):
            resp = client.get("/api/v1/pubmed/34878953")
        assert resp.status_code == 200
        assert resp.json()["fullTextAvailability"] == "pmc"

    def test_probe_failure_propagates_as_500_not_silently_swallowed(self, client):
        """Unlike the import cascade (which degrades gracefully because it
        has a final abstract-only fallback), this read-only lookup route has
        no cascade to fall back to — an unexpected probe error should 500,
        not silently omit the field or crash with an unhandled exception."""
        stub = _normalized_stub()
        with patch("app.api.v1.pubmed.pubmed_service.get_article_normalized", return_value=stub), patch(
            "app.api.v1.pubmed.fulltext_service.probe_full_text_availability", side_effect=RuntimeError("boom")
        ):
            resp = client.get("/api/v1/pubmed/34878953")
        assert resp.status_code == 500


class TestSearchRouteOrdering:
    def test_search_resolves_to_search_handler_not_400(self, client):
        """Proves /search (a literal path) is matched before /{pmid} — if
        route order were wrong, this would 400 from the PMID-regex guard."""
        with patch(
            "app.api.v1.pubmed.pubmed_service.fetch_search",
            return_value={"total": 0, "results": []},
        ):
            resp = client.get("/api/v1/pubmed/search?term=lung+cancer")
        assert resp.status_code == 200
        assert resp.json() == {"total": 0, "results": []}


class TestImportArticle:
    def test_first_import_succeeds_and_inserts(self, client):
        """No open-access PDF, no PMC copy -> today's abstract-only path,
        unchanged. Both fulltext_service calls must be mocked — otherwise
        the route would make a real Unpaywall network call for the stub's
        fake DOI, which is exactly the kind of non-deterministic live call
        this test suite avoids (see test_pubmed_service.py's docstring)."""
        stub = _normalized_stub()
        mock_supabase = MagicMock()
        mock_supabase.table.return_value.select.return_value.eq.return_value.eq.return_value.execute.return_value.data = []
        inserted_row = {"id": str(uuid4()), "pmid": "34878953", "filename": "Test Article"}
        mock_supabase.table.return_value.insert.return_value.execute.return_value.data = [inserted_row]

        with patch("app.api.v1.pubmed.supabase", mock_supabase), patch(
            "app.api.v1.pubmed.check_project_access"
        ), patch(
            "app.api.v1.pubmed.pubmed_service.get_article_normalized", return_value=stub
        ), patch(
            "app.api.v1.pubmed.storage_service.upload_markdown", return_value="markdown/proj/hash.md"
        ), patch(
            "app.api.v1.pubmed.fulltext_service.fetch_open_access_pdf", return_value=None
        ), patch(
            "app.api.v1.pubmed.fulltext_service.fetch_pmc_fulltext", return_value=None
        ), patch(
            "app.api.v1.pubmed.pubmed_service.has_abstract", return_value=True
        ), patch(
            "app.services.activity_service.log_activity"
        ):
            resp = client.post("/api/v1/pubmed/34878953/import", json={"project_id": FAKE_PROJECT_ID})

        assert resp.status_code == 201
        body = resp.json()
        assert body["full_text_source"] == "abstract"
        assert {k: v for k, v in body.items() if k != "full_text_source"} == inserted_row
        mock_supabase.table.return_value.insert.assert_called_once()
        inserted_payload = mock_supabase.table.return_value.insert.call_args[0][0]
        assert inserted_payload["pmid"] == "34878953"
        assert inserted_payload["source_type"] == "pubmed"
        assert inserted_payload["s3_pdf_path"] is None
        # An abstract is thin evidence: readable, but held out of extraction
        # runs until a reviewer accepts it (see enums.ProcessingStatus).
        assert inserted_payload["processing_status"] == "metadata_only"
        assert inserted_payload["doi"] == "10.1234/test"


class TestImportArticleFullTextCascade:
    """The Unpaywall PDF -> PMC full text -> abstract cascade added for the
    "full pipeline" PubMed full-text feature (see app/services/fulltext_service.py)."""

    def test_open_access_pdf_routes_through_real_pdf_pipeline(self, client):
        stub = _normalized_stub()
        mock_supabase = MagicMock()
        mock_supabase.table.return_value.select.return_value.eq.return_value.eq.return_value.execute.return_value.data = []
        inserted_row = {"id": str(uuid4()), "pmid": "34878953", "filename": "Test Article"}
        mock_supabase.table.return_value.insert.return_value.execute.return_value.data = [inserted_row]
        job_row = {"id": str(uuid4())}

        # This branch calls .insert() twice through the same
        # mock_supabase.table(...).insert(...) chain — once for `documents`,
        # once for `jobs` — so route by payload shape rather than call order.
        def insert_side_effect(payload):
            result = MagicMock()
            if "job_type" in payload:
                result.execute.return_value.data = [job_row]
            else:
                result.execute.return_value.data = [inserted_row]
            return result

        mock_supabase.table.return_value.insert.side_effect = insert_side_effect

        mock_celery_task = MagicMock()
        mock_celery_task.id = "celery-task-123"

        with patch("app.api.v1.pubmed.supabase", mock_supabase), patch(
            "app.api.v1.pubmed.check_project_access"
        ), patch(
            "app.api.v1.pubmed.pubmed_service.get_article_normalized", return_value=stub
        ), patch(
            "app.api.v1.pubmed.fulltext_service.fetch_open_access_pdf", return_value=b"%PDF-fake-bytes"
        ), patch(
            "app.api.v1.pubmed.storage_service.upload_pdf", return_value="pdfs/proj/hash.pdf"
        ) as mock_upload_pdf, patch(
            "app.workers.pdf_tasks.process_pdf_document.delay", return_value=mock_celery_task
        ) as mock_delay, patch(
            "app.services.activity_service.log_activity"
        ):
            resp = client.post("/api/v1/pubmed/34878953/import", json={"project_id": FAKE_PROJECT_ID})

        assert resp.status_code == 201
        assert resp.json()["full_text_source"] == "unpaywall_pdf"
        mock_upload_pdf.assert_called_once()
        # The document insert (first call) must carry a real s3_pdf_path and
        # a pending status — process_pdf_document owns completion from here.
        doc_payload = mock_supabase.table.return_value.insert.call_args_list[0][0][0]
        assert doc_payload["s3_pdf_path"] == "pdfs/proj/hash.pdf"
        assert doc_payload["s3_markdown_path"] is None
        assert doc_payload["processing_status"] == "pending"
        assert doc_payload["doi"] == "10.1234/test"
        # A jobs row was created and the real Celery task was enqueued —
        # exactly the same pipeline a manual upload uses.
        job_payload = mock_supabase.table.return_value.insert.call_args_list[1][0][0]
        assert job_payload["job_type"] == "pdf_processing"
        assert job_payload["status"] == "pending"
        mock_delay.assert_called_once_with(document_id=inserted_row["id"], job_id=job_row["id"])

    def test_no_doi_still_attempts_a_pmid_based_pdf_lookup(self, client):
        """A missing DOI used to end the PDF hunt outright. It no longer does:
        the resolver can still reach Europe PMC from the PMID alone, which for
        PMC-deposited papers is often the only copy that actually downloads."""
        stub = _normalized_stub()
        stub["doi"] = None
        mock_supabase = MagicMock()
        mock_supabase.table.return_value.select.return_value.eq.return_value.eq.return_value.execute.return_value.data = []
        inserted_row = {"id": str(uuid4()), "pmid": "34878953", "filename": "Test Article"}
        mock_supabase.table.return_value.insert.return_value.execute.return_value.data = [inserted_row]

        with patch("app.api.v1.pubmed.supabase", mock_supabase), patch(
            "app.api.v1.pubmed.check_project_access"
        ), patch(
            "app.api.v1.pubmed.pubmed_service.get_article_normalized", return_value=stub
        ), patch(
            "app.api.v1.pubmed.storage_service.upload_markdown", return_value="markdown/proj/hash.md"
        ), patch(
            "app.api.v1.pubmed.fulltext_service.fetch_open_access_pdf", return_value=None
        ) as mock_fetch_pdf, patch(
            "app.api.v1.pubmed.fulltext_service.fetch_pmc_fulltext", return_value=None
        ), patch(
            "app.api.v1.pubmed.pubmed_service.has_abstract", return_value=True
        ), patch(
            "app.services.activity_service.log_activity"
        ):
            resp = client.post("/api/v1/pubmed/34878953/import", json={"project_id": FAKE_PROJECT_ID})

        assert resp.status_code == 201
        assert resp.json()["full_text_source"] == "abstract"
        mock_fetch_pdf.assert_called_once_with(None, "34878953")

    def test_no_abstract_and_no_full_text_lands_needs_pdf_not_metadata_only(self, client):
        """An editorial or letter with no abstract has nothing to read at all.
        `metadata_only` would be wrong — that status can be accepted into an
        extraction run, so a reviewer could green-light empty evidence."""
        stub = _normalized_stub()
        mock_supabase = MagicMock()
        mock_supabase.table.return_value.select.return_value.eq.return_value.eq.return_value.execute.return_value.data = []
        inserted_row = {"id": str(uuid4()), "pmid": "34878953", "filename": "Test Article"}
        mock_supabase.table.return_value.insert.return_value.execute.return_value.data = [inserted_row]

        with patch("app.api.v1.pubmed.supabase", mock_supabase), patch(
            "app.api.v1.pubmed.check_project_access"
        ), patch(
            "app.api.v1.pubmed.pubmed_service.get_article_normalized", return_value=stub
        ), patch(
            "app.api.v1.pubmed.storage_service.upload_markdown", return_value="markdown/proj/hash.md"
        ), patch(
            "app.api.v1.pubmed.fulltext_service.fetch_open_access_pdf", return_value=None
        ), patch(
            "app.api.v1.pubmed.fulltext_service.fetch_pmc_fulltext", return_value=None
        ), patch(
            "app.api.v1.pubmed.pubmed_service.has_abstract", return_value=False
        ), patch(
            "app.services.activity_service.log_activity"
        ):
            resp = client.post("/api/v1/pubmed/34878953/import", json={"project_id": FAKE_PROJECT_ID})

        assert resp.status_code == 201
        assert resp.json()["full_text_source"] == "none"
        doc_payload = mock_supabase.table.return_value.insert.call_args[0][0]
        assert doc_payload["processing_status"] == "needs_pdf"

    def test_pmc_fulltext_used_when_no_open_access_pdf(self, client):
        stub = _normalized_stub()
        mock_supabase = MagicMock()
        mock_supabase.table.return_value.select.return_value.eq.return_value.eq.return_value.execute.return_value.data = []
        inserted_row = {"id": str(uuid4()), "pmid": "34878953", "filename": "Test Article"}
        mock_supabase.table.return_value.insert.return_value.execute.return_value.data = [inserted_row]
        pmc_sections = [{"section": "Introduction", "text": "Some PMC full text."}]

        with patch("app.api.v1.pubmed.supabase", mock_supabase), patch(
            "app.api.v1.pubmed.check_project_access"
        ), patch(
            "app.api.v1.pubmed.pubmed_service.get_article_normalized", return_value=stub
        ), patch(
            "app.api.v1.pubmed.fulltext_service.fetch_open_access_pdf", return_value=None
        ), patch(
            "app.api.v1.pubmed.fulltext_service.fetch_pmc_fulltext", return_value=pmc_sections
        ), patch(
            "app.api.v1.pubmed.storage_service.upload_markdown", return_value="markdown/proj/hash.md"
        ) as mock_upload_markdown, patch(
            "app.services.activity_service.log_activity"
        ):
            resp = client.post("/api/v1/pubmed/34878953/import", json={"project_id": FAKE_PROJECT_ID})

        assert resp.status_code == 201
        assert resp.json()["full_text_source"] == "pmc"
        doc_payload = mock_supabase.table.return_value.insert.call_args[0][0]
        assert doc_payload["s3_pdf_path"] is None
        assert doc_payload["processing_status"] == "completed"
        # The stored JSON must actually contain the PMC text, not just a
        # side-channel flag.
        stored_content = mock_upload_markdown.call_args[0][0]
        assert "Some PMC full text." in stored_content

    def test_duplicate_pmid_returns_duplicate_without_second_insert(self, client):
        stub = _normalized_stub()
        mock_supabase = MagicMock()
        existing_row = {"id": str(uuid4()), "pmid": "34878953", "filename": "Already Imported"}
        mock_supabase.table.return_value.select.return_value.eq.return_value.eq.return_value.execute.return_value.data = [
            existing_row
        ]

        with patch("app.api.v1.pubmed.supabase", mock_supabase), patch(
            "app.api.v1.pubmed.check_project_access"
        ), patch(
            "app.api.v1.pubmed.pubmed_service.get_article_normalized", return_value=stub
        ):
            resp = client.post("/api/v1/pubmed/34878953/import", json={"project_id": FAKE_PROJECT_ID})

        assert resp.status_code == 200
        body = resp.json()
        assert body["duplicate"] is True
        assert body["document"] == existing_row
        mock_supabase.table.return_value.insert.assert_not_called()

    def test_invalid_pmid_returns_400(self, client):
        resp = client.post("/api/v1/pubmed/not-a-pmid/import", json={"project_id": FAKE_PROJECT_ID})
        assert resp.status_code == 400

    def test_no_project_access_returns_403(self, client):
        with patch(
            "app.api.v1.pubmed.check_project_access",
            side_effect=HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No access"),
        ):
            resp = client.post("/api/v1/pubmed/34878953/import", json={"project_id": FAKE_PROJECT_ID})
        assert resp.status_code == 403
