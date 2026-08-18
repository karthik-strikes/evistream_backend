"""
ClinicalTrials.gov search + import endpoints.

Three read-only proxy routes (version/search/single-study) plus one write
route (import) that creates a `documents` row from a CT.gov trial record —
see backend/app/services/clinical_trials_service.py for the upstream client,
normalizer, and markdown synthesis.

Route order matters: literal paths (/version, /search) must be declared
before the /{nct_id} param route, or the NCT-regex guard on /{nct_id} would
shadow them.
"""

import logging
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from supabase import create_client

from app.config import settings
from app.dependencies import get_current_user
from app.services import clinical_trials_service as ct_service
from app.services.project_access import check_project_access
from app.services.storage_service import storage_service

logger = logging.getLogger(__name__)
router = APIRouter()

supabase = create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_KEY)


def _require_nct(nct_id: str) -> str:
    nct_id = nct_id.upper()
    if not ct_service.NCT_RE.match(nct_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid NCT ID. Expected format: NCT then 8 digits.",
        )
    return nct_id


@router.get("/version")
async def get_version(user_id: UUID = Depends(get_current_user)):
    """Upstream liveness probe + data-freshness check."""
    return await ct_service.fetch_version()


@router.get("/search")
async def search_trials(
    term: Optional[str] = None,
    cond: Optional[str] = Query(None),
    intr: Optional[str] = Query(None),
    spons: Optional[str] = Query(None),
    status_filter: Optional[str] = Query(None, alias="status"),  # comma-separated overallStatus
    phase: Optional[str] = Query(None),  # comma-separated
    pageSize: int = Query(25, ge=1, le=1000),
    pageToken: Optional[str] = None,
    user_id: UUID = Depends(get_current_user),
):
    """Proxy the upstream search endpoint. One search box on the frontend
    decides free-text vs NCT ID; free-text lands here as `term`."""
    params = {"format": "json", "pageSize": str(pageSize), "countTotal": "true"}
    if term:
        params["query.term"] = term
    if cond:
        params["query.cond"] = cond
    if intr:
        params["query.intr"] = intr
    if spons:
        params["query.spons"] = spons
    if status_filter:
        params["filter.overallStatus"] = status_filter
    if phase:
        agg_phase = ct_service.phase_agg_filter(phase)
        if agg_phase:
            params["aggFilters"] = agg_phase
    if pageToken:
        params["pageToken"] = pageToken

    try:
        return await ct_service.fetch_search(params)
    except HTTPException:
        raise
    except Exception:
        logger.exception("Error searching ClinicalTrials.gov")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Search failed.")


@router.get("/{nct_id}")
async def get_trial(nct_id: str, user_id: UUID = Depends(get_current_user)):
    """One study, normalized (flattened) — the primary lookup route."""
    nct_id = _require_nct(nct_id)
    try:
        return await ct_service.get_study_normalized(nct_id)
    except HTTPException:
        raise
    except Exception:
        logger.exception(f"Error fetching trial {nct_id}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to fetch trial.")


@router.get("/{nct_id}/raw")
async def get_trial_raw(nct_id: str, user_id: UUID = Depends(get_current_user)):
    """One study, untouched upstream JSON — escape hatch for consumers who
    need a field we don't surface in the normalized shape."""
    nct_id = _require_nct(nct_id)
    try:
        return await ct_service.fetch_study(nct_id)
    except HTTPException:
        raise
    except Exception:
        logger.exception(f"Error fetching raw trial {nct_id}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to fetch trial.")


class _ImportBody(BaseModel):
    project_id: UUID


@router.post("/{nct_id}/import", status_code=status.HTTP_201_CREATED)
async def import_trial(nct_id: str, body: _ImportBody, user_id: UUID = Depends(get_current_user)):
    """
    Import a CT.gov trial as a document. No PDF is uploaded — instead we
    synthesize a Markdown rendering of the normalized record and store it
    exactly like a parsed PDF's markdown (same storage_service call, same
    S3 key convention), so the resulting document is fully extractable
    through the normal pipeline with zero special-casing downstream.

    Dedups on NCT ID (enforced via a partial-unique index — see
    migrations/add_document_ctgov_source.sql). Manual search + import only:
    no persistent "connected source" state, no scheduled re-sync.
    """
    nct_id = _require_nct(nct_id)

    try:
        await check_project_access(body.project_id, user_id, "can_upload_docs")

        # Dedup by NCT ID first — mirrors the exact response shape of the
        # upload-dedup path (documents.py's content_hash check) so the
        # frontend can reuse its existing duplicate-toast handling.
        existing = (
            supabase.table("documents")
            .select("*")
            .eq("project_id", str(body.project_id))
            .eq("nct_id", nct_id)
            .execute()
        )
        if existing.data:
            # Override the route's default 201 — a duplicate is a 200, not a
            # creation (mirrors documents.py's upload-dedup response exactly).
            return JSONResponse(
                status_code=status.HTTP_200_OK,
                content={"duplicate": True, "document": existing.data[0]},
            )

        normalized = await ct_service.get_study_normalized(nct_id)
        document_content = ct_service.build_document_content(normalized)
        content_hash = ct_service.content_hash_for_import(nct_id)

        s3_markdown_path = storage_service.upload_markdown(document_content, str(body.project_id), content_hash)

        title = normalized["title"].get("brief") or normalized["title"].get("official") or nct_id
        phase_str = ", ".join(normalized.get("phase") or []) or None

        document_data = {
            "project_id": str(body.project_id),
            # varchar(255), unlike the text `title` below — clamp so a long
            # official title can't fail the insert with 22001.
            "filename": title[:255],
            "unique_filename": None,
            "content_hash": content_hash,
            "s3_pdf_path": None,
            "s3_markdown_path": s3_markdown_path,
            # A results-posted trial carries per-arm outcomes, participant flow
            # and adverse events — genuinely rich, often better than a PDF. A
            # registration-only record has planned outcomes but zero data, so
            # it's thin evidence and needs accepting first.
            "processing_status": (
                "completed" if normalized.get("status", {}).get("hasResults") else "metadata_only"
            ),
            "title": title,
            "labels": [],
            "source_type": "ctgov",
            "nct_id": nct_id,
            "trial_status": normalized["status"].get("overall"),
            "trial_phase": phase_str,
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
                action="Trial Imported",
                description=f"Imported ClinicalTrials.gov trial: {title} ({nct_id})",
                project_id=body.project_id,
                metadata={"nct_id": nct_id, "document_id": document["id"]},
            )
        except Exception:
            logger.debug("Activity logging failed for trial import (non-fatal)", exc_info=True)

        return document

    except HTTPException:
        raise
    except Exception:
        logger.exception(f"Error importing trial {nct_id}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to import trial.")
