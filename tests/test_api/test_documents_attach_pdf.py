"""
Route-level tests for POST /api/v1/documents/{id}/attach-pdf — the manual
full-text fallback added for PubMed imports where no free open-access copy
was found automatically (see ImportedTrialDrawer's "Attach PDF" prompt and
app/api/v1/pubmed.py's Unpaywall/PMC cascade).

Same TestClient + dependency_overrides + unittest.mock convention as
test_pubmed.py; nothing here touches real S3, Supabase, or Celery.
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
FAKE_DOCUMENT_ID = str(uuid4())

REAL_PDF_BYTES = b"%PDF-1.4\n%fake pdf content for tests\n"
NOT_A_PDF_BYTES = b"this is definitely not a pdf"


@pytest.fixture
def client():
    app.dependency_overrides[get_current_user] = lambda: FAKE_USER_ID
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.pop(get_current_user, None)


def _document_row(**overrides):
    row = {
        "id": FAKE_DOCUMENT_ID,
        "project_id": FAKE_PROJECT_ID,
        "content_hash": "abc123hash",
        "filename": "PMID 34878953",
        "s3_pdf_path": None,
        "processing_status": "completed",
        "source_type": "pubmed",
    }
    row.update(overrides)
    return row


class TestAttachPdf:
    def test_valid_pdf_starts_processing(self, client):
        mock_supabase = MagicMock()
        doc = _document_row()
        mock_supabase.table.return_value.select.return_value.eq.return_value.execute.return_value.data = [doc]
        job_row = {"id": str(uuid4())}
        mock_supabase.table.return_value.insert.return_value.execute.return_value.data = [job_row]

        mock_celery_task = MagicMock()
        mock_celery_task.id = "celery-task-abc"

        with patch("app.api.v1.documents.supabase", mock_supabase), patch(
            "app.api.v1.documents.check_project_access"
        ), patch(
            "app.api.v1.documents.storage_service.upload_pdf", return_value="pdfs/proj/abc123hash.pdf"
        ) as mock_upload_pdf, patch(
            "app.workers.pdf_tasks.process_pdf_document.delay", return_value=mock_celery_task
        ) as mock_delay:
            resp = client.post(
                f"/api/v1/documents/{FAKE_DOCUMENT_ID}/attach-pdf",
                files={"file": ("paper.pdf", REAL_PDF_BYTES, "application/pdf")},
            )

        assert resp.status_code == 200
        assert resp.json()["status"] == "processing"
        mock_upload_pdf.assert_called_once_with(REAL_PDF_BYTES, FAKE_PROJECT_ID, "abc123hash")
        mock_delay.assert_called_once_with(document_id=FAKE_DOCUMENT_ID, job_id=job_row["id"])

        update_payload = mock_supabase.table.return_value.update.call_args_list[0][0][0]
        assert update_payload["s3_pdf_path"] == "pdfs/proj/abc123hash.pdf"
        assert update_payload["processing_status"] == "pending"

    def test_non_pdf_bytes_rejected(self, client):
        mock_supabase = MagicMock()
        doc = _document_row()
        mock_supabase.table.return_value.select.return_value.eq.return_value.execute.return_value.data = [doc]

        with patch("app.api.v1.documents.supabase", mock_supabase), patch(
            "app.api.v1.documents.check_project_access"
        ):
            resp = client.post(
                f"/api/v1/documents/{FAKE_DOCUMENT_ID}/attach-pdf",
                files={"file": ("paper.pdf", NOT_A_PDF_BYTES, "application/pdf")},
            )

        assert resp.status_code == 400
        mock_supabase.table.return_value.insert.assert_not_called()

    def test_wrong_extension_rejected(self, client):
        mock_supabase = MagicMock()
        doc = _document_row()
        mock_supabase.table.return_value.select.return_value.eq.return_value.execute.return_value.data = [doc]

        with patch("app.api.v1.documents.supabase", mock_supabase), patch(
            "app.api.v1.documents.check_project_access"
        ):
            resp = client.post(
                f"/api/v1/documents/{FAKE_DOCUMENT_ID}/attach-pdf",
                files={"file": ("paper.txt", REAL_PDF_BYTES, "text/plain")},
            )

        assert resp.status_code == 400

    def test_document_not_found_returns_404(self, client):
        mock_supabase = MagicMock()
        mock_supabase.table.return_value.select.return_value.eq.return_value.execute.return_value.data = []

        with patch("app.api.v1.documents.supabase", mock_supabase):
            resp = client.post(
                f"/api/v1/documents/{FAKE_DOCUMENT_ID}/attach-pdf",
                files={"file": ("paper.pdf", REAL_PDF_BYTES, "application/pdf")},
            )

        assert resp.status_code == 404

    def test_no_project_access_returns_403(self, client):
        mock_supabase = MagicMock()
        doc = _document_row()
        mock_supabase.table.return_value.select.return_value.eq.return_value.execute.return_value.data = [doc]

        with patch("app.api.v1.documents.supabase", mock_supabase), patch(
            "app.api.v1.documents.check_project_access",
            side_effect=HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No access"),
        ):
            resp = client.post(
                f"/api/v1/documents/{FAKE_DOCUMENT_ID}/attach-pdf",
                files={"file": ("paper.pdf", REAL_PDF_BYTES, "application/pdf")},
            )

        assert resp.status_code == 403

    def test_oversized_file_rejected(self, client):
        mock_supabase = MagicMock()
        doc = _document_row()
        mock_supabase.table.return_value.select.return_value.eq.return_value.execute.return_value.data = [doc]

        with patch("app.api.v1.documents.supabase", mock_supabase), patch(
            "app.api.v1.documents.check_project_access"
        ), patch("app.api.v1.documents.MAX_FILE_SIZE", 10):
            resp = client.post(
                f"/api/v1/documents/{FAKE_DOCUMENT_ID}/attach-pdf",
                files={"file": ("paper.pdf", REAL_PDF_BYTES, "application/pdf")},
            )

        assert resp.status_code == 413
