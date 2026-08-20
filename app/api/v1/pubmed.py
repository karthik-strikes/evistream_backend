"""
PubMed search + import endpoints.

Mirrors backend/app/api/v1/clinical_trials.py exactly: three read-only
proxy routes (search/article/raw) plus one write route (import) that
creates a `documents` row from a PubMed article — see
backend/app/services/pubmed_service.py for the upstream client, normalizer,
and document-content synthesis.

Route order matters: /search (literal) must be declared before /{pmid}
(param route), or the PMID-regex guard on /{pmid} would shadow it.
"""

import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from supabase import create_client

from app.config import settings
from app.dependencies import get_current_user
from app.services import fulltext_service, pubmed_service
from app.services.project_access import check_project_access
from app.services.storage_service import storage_service
from utils.study_label import first_surname, year_of

logger = logging.getLogger(__name__)
router = APIRouter()

supabase = create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_KEY)


def _require_pmid(pmid: str) -> str:
    pmid = pmid.strip()
    if not pubmed_service.PMID_RE.match(pmid):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid PMID. Expected 1-8 digits.",
        )
    return pmid


@router.get("/search")
async def search_articles(term: str, user_id: UUID = Depends(get_current_user)):
    """Proxy the upstream esearch+esummary lookup. One search box on the
    frontend decides free-text vs PMID; free-text lands here as `term`."""
    try:
        return await pubmed_service.fetch_search(term)
    except HTTPException:
        raise
    except Exception:
        logger.exception("Error searching PubMed")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Search failed.")


@router.get("/{pmid}")
async def get_article(pmid: str, user_id: UUID = Depends(get_current_user)):
    """One article, normalized (citation metadata + abstract) — the primary
    lookup route. Also probes (live, uncached — see
    fulltext_service.probe_full_text_availability) whether a free full-text
    copy is likely available and in what form, so the frontend can show
    "PDF available" / "Full text via PMC" / "Abstract only" BEFORE a user
    commits to importing, not just after."""
    pmid = _require_pmid(pmid)
    try:
        normalized = await pubmed_service.get_article_normalized(pmid)
        availability = await fulltext_service.probe_full_text_availability(normalized.get("doi"), pmid)
        return {**normalized, "fullTextAvailability": availability}
    except HTTPException:
        raise
    except Exception:
        logger.exception(f"Error fetching article {pmid}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to fetch article.")


@router.get("/{pmid}/raw")
async def get_article_raw(pmid: str, user_id: UUID = Depends(get_current_user)):
    """One article, untouched upstream data — escape hatch for consumers
    who need a field we don't surface in the normalized shape."""
    pmid = _require_pmid(pmid)
    try:
        return await pubmed_service.get_article_full(pmid)
    except HTTPException:
        raise
    except Exception:
        logger.exception(f"Error fetching raw article {pmid}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to fetch article.")


class _ImportBody(BaseModel):
    project_id: UUID


@router.post("/{pmid}/import", status_code=status.HTTP_201_CREATED)
async def import_article(pmid: str, body: _ImportBody, user_id: UUID = Depends(get_current_user)):
    """
    Import a PubMed article as a document. Cascades toward the richest free
    copy available: (1) an open-access PDF via Unpaywall, routed through the
    exact same Datalab/Celery pipeline a manual upload uses; (2) PubMed
    Central structured full text, embedded as JSON; (3) citation + abstract
    only (today's original behavior) — in which case the frontend offers a
    manual "attach PDF" fallback (POST /documents/{id}/attach-pdf) so a user
    with licensed access can still get full extraction. See
    fulltext_service.py for the Unpaywall/PMC lookups.

    Dedups on PMID (enforced via a partial-unique index — see
    migrations/add_document_pmid_unique_index.sql).
    """
    pmid = _require_pmid(pmid)

    try:
        await check_project_access(body.project_id, user_id, "can_upload_docs")

        # Dedup by PMID first — mirrors the exact response shape of the
        # upload-dedup path (documents.py's content_hash check) and the
        # CT.gov import's nct_id dedup, so the frontend can reuse its
        # existing duplicate-toast handling.
        existing = (
            supabase.table("documents")
            .select("*")
            .eq("project_id", str(body.project_id))
            .eq("pmid", pmid)
            .execute()
        )
        if existing.data:
            return JSONResponse(
                status_code=status.HTTP_200_OK,
                content={"duplicate": True, "document": existing.data[0]},
            )

        normalized = await pubmed_service.get_article_normalized(pmid)
        content_hash = pubmed_service.content_hash_for_import(pmid)
        title = normalized.get("title") or f"PMID {pmid}"
        # `documents.filename` is varchar(255) while `title` is text — a >255-char
        # paper title fails the insert with 22001 (observed on an EndNote import,
        # Aug 11 2026). Clamp the varchar column only; `title` keeps the full string.
        filename = title[:255]
        doi = normalized.get("doi")
        project_id = str(body.project_id)

        # Cascade toward the richest available copy of the paper:
        #   1. Unpaywall PDF (by DOI) -> the exact same Datalab/Celery
        #      pipeline a manual upload uses (real PDF viewer, grounded quotes).
        #   2. PMC structured full text -> embedded as JSON, no PDF viewer.
        #   3. Abstract only (today's behavior) -> user can attach a PDF later
        #      via POST /documents/{id}/attach-pdf.
        pdf_bytes = await fulltext_service.fetch_open_access_pdf(doi, pmid)

        if pdf_bytes:
            s3_pdf_path = storage_service.upload_pdf(pdf_bytes, project_id, content_hash)

            document_data = {
                "project_id": project_id,
                "filename": filename,
                "unique_filename": None,
                "content_hash": content_hash,
                "s3_pdf_path": s3_pdf_path,
                "s3_markdown_path": None,
                "processing_status": "pending",
                "title": title,
                "labels": [],
                "source_type": "pubmed",
                "pmid": pmid,
                "doi": doi,
                # esummary already returned the author list ("Raslan N") and
                # pubdate — the two halves of the "Raslan 2021" study label.
                "first_author": first_surname(normalized.get("authors")),
                "pub_year": year_of(normalized.get("year") or normalized.get("pubDate")),
            }
            result = supabase.table("documents").insert(document_data).execute()
            if not result.data:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to create document record."
                )
            document = result.data[0]
            document_id = document["id"]

            from app.models.enums import JobType, JobStatus

            job_data = {
                "user_id": str(user_id),
                "project_id": project_id,
                "job_type": JobType.PDF_PROCESSING.value,
                "status": JobStatus.PENDING.value,
                "progress": 0,
                "input_data": {"document_id": document_id, "filename": title},
            }
            job_result = supabase.table("jobs").insert(job_data).execute()
            if job_result.data:
                job_id = job_result.data[0]["id"]
                from app.workers.pdf_tasks import process_pdf_document

                celery_task = process_pdf_document.delay(document_id=document_id, job_id=str(job_id))
                supabase.table("jobs").update({"celery_task_id": celery_task.id}).eq("id", job_id).execute()
            else:
                logger.warning(f"Failed to create processing job for PubMed PDF import {pmid}; document stuck pending")

            full_text_source = "unpaywall_pdf"
        else:
            pmc_sections = await fulltext_service.fetch_pmc_fulltext(pmid)
            if pmc_sections:
                normalized["fullText"] = pmc_sections
                full_text_source = "pmc"
            elif await pubmed_service.has_abstract(pmid):
                full_text_source = "abstract"
            else:
                # Citation metadata only — an editorial or letter with no
                # abstract. There is nothing here for a reviewer to read or
                # for extraction to score, so this must NOT land as
                # `metadata_only`: that status is acceptable-but-thin and can
                # be accepted into a run. `needs_pdf` keeps it out until a PDF
                # is attached, mirroring the RIS/EndNote importer's last tier
                # (import_tasks.py:_import_one_citation).
                full_text_source = "none"

            document_content = pubmed_service.build_document_content(normalized)
            s3_markdown_path = storage_service.upload_markdown(document_content, project_id, content_hash)

            document_data = {
                "project_id": project_id,
                "filename": filename,
                "unique_filename": None,
                "content_hash": content_hash,
                "s3_pdf_path": None,
                "s3_markdown_path": s3_markdown_path,
                # PMC gave us the real article body → full text. An abstract
                # alone is thin evidence and must be accepted by a reviewer
                # before it enters extraction. Neither → nothing readable at
                # all, so it waits for an attached PDF.
                "processing_status": (
                    "completed" if full_text_source == "pmc"
                    else "metadata_only" if full_text_source == "abstract"
                    else "needs_pdf"
                ),
                "title": title,
                "labels": [],
                "source_type": "pubmed",
                "pmid": pmid,
                "doi": doi,
                # esummary already returned the author list ("Raslan N") and
                # pubdate — the two halves of the "Raslan 2021" study label.
                "first_author": first_surname(normalized.get("authors")),
                "pub_year": year_of(normalized.get("year") or normalized.get("pubDate")),
            }
            result = supabase.table("documents").insert(document_data).execute()
            if not result.data:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to create document record."
                )
            document = result.data[0]

        try:
            from app.services.activity_service import log_activity

            await log_activity(
                user_id=user_id,
                action_type="import",
                action="Article Imported",
                description=f"Imported PubMed article: {title} (PMID {pmid}, full_text={full_text_source})",
                project_id=body.project_id,
                metadata={"pmid": pmid, "document_id": document["id"], "full_text_source": full_text_source},
            )
        except Exception:
            logger.debug("Activity logging failed for article import (non-fatal)", exc_info=True)

        return {**document, "full_text_source": full_text_source}

    except HTTPException:
        raise
    except Exception:
        logger.exception(f"Error importing article {pmid}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to import article.")
