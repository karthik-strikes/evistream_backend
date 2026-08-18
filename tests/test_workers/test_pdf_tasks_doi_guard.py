"""
Unit test for the DOI/title-clobber guard in process_pdf_document
(app/workers/pdf_tasks.py) added alongside the PubMed "full pipeline"
full-text feature.

Context: a PubMed import that found an open-access PDF via Unpaywall already
carries an authoritative doi/title from PubMed's own metadata (that DOI is
literally how the PDF was found). Before this guard, process_pdf_document's
best-effort DOI extraction (doi_service.extract_doi) unconditionally
overwrote doi/doi_source — including to None/"none" when the PDF-text-scrape
cascade found nothing — which would have silently wiped a correct,
already-known DOI. Manual uploads (no source_type / source_type != "pubmed")
must keep the original unconditional-overwrite behavior.

Runs process_pdf_document directly (bind=True lets Celery bind `self`
automatically even outside a worker/broker) with every external dependency
(supabase, storage_service, pdf_processing_service, extract_doi, the
fire-and-forget notify/activity/clean-pdf calls) mocked — no real network,
S3, or Celery broker involved.
"""

import os
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from app.services.doi_service import DoiResult  # noqa: E402
from app.workers.pdf_tasks import process_pdf_document  # noqa: E402

DOCUMENT_ID = "11111111-1111-1111-1111-111111111111"
JOB_ID = "22222222-2222-2222-2222-222222222222"


def _make_mock_supabase(document: dict):
    """Route table("documents") and table("jobs") to separate mocks so their
    .update(...) calls can be inspected independently — a plain MagicMock
    would otherwise return the SAME mock for both table names, conflating
    documents.update() calls (which is what these tests assert on) with
    jobs.update() calls."""
    documents_table = MagicMock()
    documents_table.select.return_value.eq.return_value.execute.return_value.data = [document]
    jobs_table = MagicMock()

    mock_supabase = MagicMock()
    mock_supabase.table.side_effect = lambda name: documents_table if name == "documents" else jobs_table
    return mock_supabase, documents_table, jobs_table


def _run_task(document: dict, scraped_doi_result: DoiResult):
    mock_supabase, documents_table, jobs_table = _make_mock_supabase(document)

    parse_result = {
        "success": True,
        "markdown_content": "# Title\n\nBody text.",
        "blocks_json": None,
        "blocks_error": None,
        "parse_quality_score": 0.9,
        "checkpoint_id": "cp-1",
        "request_id": "req-1",
        "page_count": 3,
        "metadata": {},
    }

    with patch("app.workers.pdf_tasks.supabase", mock_supabase), patch(
        "app.workers.pdf_tasks.storage_service.download_to_temp"
    ), patch(
        "app.workers.pdf_tasks.storage_service.upload_markdown", return_value="markdown/proj/hash.md"
    ), patch(
        "app.workers.pdf_tasks.pdf_processing_service.process_pdf_to_markdown", return_value=parse_result
    ), patch(
        "app.workers.pdf_tasks.extract_doi", return_value=scraped_doi_result
    ), patch(
        "app.workers.pdf_tasks.sync_notify"
    ), patch(
        "app.workers.pdf_tasks.sync_log_activity"
    ), patch(
        "app.workers.pdf_tasks.clean_pdf_document.delay"
    ):
        process_pdf_document(DOCUMENT_ID, JOB_ID)

    return documents_table


class TestPubmedImportDoiPreserved:
    def test_scrape_finding_nothing_does_not_wipe_existing_doi(self):
        """The exact bug this guard prevents: a PubMed-sourced doc's real
        DOI must survive even when the PDF-text-scrape cascade finds none
        (doi=None, source="none") — pre-fix this unconditionally overwrote
        doi to None."""
        document = {
            "id": DOCUMENT_ID,
            "project_id": "proj-1",
            "content_hash": "hash123",
            "s3_pdf_path": "pdfs/proj-1/hash123.pdf",
            "source_type": "pubmed",
            "doi": "10.1234/authoritative-pubmed-doi",
            "title": "Authoritative PubMed Title",
            "filename": "Test Article",
        }
        documents_table = _run_task(document, DoiResult(doi=None, source="none", title=None))

        final_update = documents_table.update.call_args_list[-1][0][0]
        # The guard leaves "doi"/"doi_source" out of the update payload
        # entirely rather than re-writing the existing value — either way,
        # the row's real doi is left untouched (not overwritten to None).
        assert "doi" not in final_update
        assert "doi_source" not in final_update
        assert "title" not in final_update

    def test_scrape_finding_different_doi_does_not_override_pubmed_doi(self):
        document = {
            "id": DOCUMENT_ID,
            "project_id": "proj-1",
            "content_hash": "hash123",
            "s3_pdf_path": "pdfs/proj-1/hash123.pdf",
            "source_type": "pubmed",
            "doi": "10.1234/authoritative-pubmed-doi",
            "title": "Authoritative PubMed Title",
            "filename": "Test Article",
        }
        documents_table = _run_task(
            document, DoiResult(doi="10.5555/some-other-scraped-doi", source="text", title="Scraped Title")
        )

        final_update = documents_table.update.call_args_list[-1][0][0]
        # Guard omits doi/doi_source from the payload rather than writing the
        # scraped (wrong) value — the row keeps its existing authoritative doi.
        assert "doi" not in final_update
        assert "doi_source" not in final_update
        assert "title" not in final_update

    def test_backfills_doi_when_pubmed_had_none(self):
        """Edge case kept for safety even though it shouldn't occur via the
        Unpaywall-PDF path in practice (we only fetch a PDF when a DOI is
        already known) — a scraped DOI beats none."""
        document = {
            "id": DOCUMENT_ID,
            "project_id": "proj-1",
            "content_hash": "hash123",
            "s3_pdf_path": "pdfs/proj-1/hash123.pdf",
            "source_type": "pubmed",
            "doi": None,
            "title": "Authoritative PubMed Title",
            "filename": "Test Article",
        }
        documents_table = _run_task(
            document, DoiResult(doi="10.5555/backfilled-doi", source="text", title="Scraped Title")
        )

        final_update = documents_table.update.call_args_list[-1][0][0]
        assert final_update["doi"] == "10.5555/backfilled-doi"
        assert final_update["doi_source"] == "text"
        assert "title" not in final_update


class TestManualUploadUnaffected:
    def test_normal_upload_still_gets_unconditional_doi_title_overwrite(self):
        """source_type is absent (or 'upload') for a manually-uploaded
        document — the guard must not change this path's behavior at all."""
        document = {
            "id": DOCUMENT_ID,
            "project_id": "proj-1",
            "content_hash": "hash123",
            "s3_pdf_path": "pdfs/proj-1/hash123.pdf",
            "filename": "manual-upload.pdf",
        }
        documents_table = _run_task(
            document, DoiResult(doi="10.9999/scraped-from-pdf", source="metadata", title="Scraped PDF Title")
        )

        final_update = documents_table.update.call_args_list[-1][0][0]
        assert final_update["doi"] == "10.9999/scraped-from-pdf"
        assert final_update["doi_source"] == "metadata"
        assert final_update["title"] == "Scraped PDF Title"

    def test_normal_upload_doi_cleared_to_none_when_scrape_finds_nothing(self):
        """Pre-existing (unchanged) behavior for manual uploads — a failed
        scrape does record doi=None/source='none', unlike the pubmed-guarded
        path above."""
        document = {
            "id": DOCUMENT_ID,
            "project_id": "proj-1",
            "content_hash": "hash123",
            "s3_pdf_path": "pdfs/proj-1/hash123.pdf",
            "filename": "manual-upload.pdf",
        }
        documents_table = _run_task(document, DoiResult(doi=None, source="none", title=None))

        final_update = documents_table.update.call_args_list[-1][0][0]
        assert final_update["doi"] is None
        assert final_update["doi_source"] == "none"
