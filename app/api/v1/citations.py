"""
RIS / DOI citation import endpoints.

Two steps so the user reviews before anything is created:
  1. POST /api/v1/citations/preview — parse an uploaded .ris file and return the
     citations it contains. Creates NOTHING. (DOI-paste mode is handled entirely
     client-side and skips straight to import.)
  2. POST /api/v1/citations/import — commit the selected citations: hand them to
     the `import_citations` Celery task, which fetches each one's open-access
     full text (Unpaywall PDF → PMC) via fulltext_service; the rest land as
     `needs_pdf`. Returns a job_id; progress streams over ws_jobs:{job_id}.
"""

import asyncio
import logging
from typing import Any, Dict, List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from pydantic import BaseModel
from supabase import create_client

from app.config import settings
from app.dependencies import get_current_user
from app.models.enums import JobStatus, JobType
from app.services import fulltext_service, ris_service
from app.services.project_access import check_project_access

logger = logging.getLogger(__name__)
router = APIRouter()

supabase = create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_KEY)

_MAX_RIS_BYTES = 25 * 1024 * 1024  # RIS is plain text — generous ceiling
_MAX_PROBE = 300                   # cap on how many citations we probe per request


@router.post("/preview")
async def preview_ris(
    project_id: UUID = Form(...),
    file: UploadFile = File(...),
    user_id: UUID = Depends(get_current_user),
):
    """Parse an uploaded .ris file and return its citations — WITHOUT importing."""
    filename = file.filename or "references.ris"
    if not filename.lower().endswith((".ris", ".txt")):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Expected a RIS file (.ris or .txt).")
    await check_project_access(project_id, user_id, "can_upload_docs")

    data = await file.read()
    if len(data) > _MAX_RIS_BYTES:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="RIS file is too large.")

    text = data.decode("utf-8", errors="replace")
    records = ris_service.parse_ris(text)
    if not records:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No citations found in the RIS file.")

    return {
        "filename": filename,
        "total": len(records),
        "with_doi": sum(1 for r in records if r.get("doi")),
        "records": [
            {
                "id": i,
                "title": r.get("title"),
                "authors": "; ".join(r.get("authors") or []) or None,
                "year": r.get("year"),
                "journal": r.get("journal"),
                "doi": r.get("doi"),
                "pmid": r.get("pmid"),
                "pmcid": r.get("pmcid"),
                # Round-tripped to the frontend so both the preview's
                # availability probe AND the eventual /import call (which
                # sends these same record dicts back — see
                # import_tasks._import_one_citation) actually get the
                # PMCID/raw-URL fallback tier. Dropping them here would
                # silently defeat that tier for every file-upload import.
                "urls": r.get("urls"),
            }
            for i, r in enumerate(records)
        ],
    }


class _AvailItem(BaseModel):
    id: int
    doi: Optional[str] = None
    pmid: Optional[str] = None
    pmcid: Optional[str] = None
    urls: Optional[List[str]] = None


class _AvailBody(BaseModel):
    items: List[_AvailItem]


@router.post("/availability")
async def check_availability(body: _AvailBody, user_id: UUID = Depends(get_current_user)):
    """Best-effort pre-import probe: for each reference, is a free full-text copy
    likely available? Returns per-item status — 'pdf' (open-access PDF via
    Unpaywall/PMC/a direct URL), 'pmc' (PubMed Central full text), or 'none'.
    Cheap: no full download, reuses fulltext_service.probe_full_text_availability.
    Bounded concurrency so we stay polite to Unpaywall/NCBI.

    Generic across both importers, not RIS-specific despite the URL prefix —
    the EndNote preview (endnote.py) calls this same endpoint with doi/pmid/
    pmcid/urls mined from the `.enlx` `url` field."""
    items = body.items[:_MAX_PROBE]
    sem = asyncio.Semaphore(8)

    async def _one(it: _AvailItem):
        if not it.doi and not it.pmid and not it.pmcid and not it.urls:
            return {"id": it.id, "status": "none"}
        async with sem:
            try:
                st = await fulltext_service.probe_full_text_availability(it.doi, it.pmid, it.pmcid, it.urls)
            except Exception:
                logger.debug("availability probe failed for %s", it.doi or it.pmid, exc_info=True)
                st = "none"
        return {"id": it.id, "status": st or "none"}

    results = await asyncio.gather(*[_one(it) for it in items])
    return {"results": results, "capped": len(body.items) > _MAX_PROBE}


class _ImportBody(BaseModel):
    project_id: UUID
    records: List[Dict[str, Any]]


@router.post("/import", status_code=status.HTTP_202_ACCEPTED)
async def import_citations_endpoint(body: _ImportBody, user_id: UUID = Depends(get_current_user)):
    """Commit the selected citations for open-access fetch + import. Returns a
    job_id; follow progress over ws_jobs:{job_id}."""
    await check_project_access(body.project_id, user_id, "can_upload_docs")
    if not body.records:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No citations to import.")

    project_id_str = str(body.project_id)
    job_result = supabase.table("jobs").insert({
        "user_id": str(user_id),
        "project_id": project_id_str,
        "job_type": JobType.IMPORT.value,
        "status": JobStatus.PENDING.value,
        "progress": 0,
        "input_data": {"kind": "ris", "count": len(body.records)},
    }).execute()
    if not job_result.data:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to create import job.")
    job_id = job_result.data[0]["id"]

    from app.workers.import_tasks import import_citations

    task = import_citations.delay(
        job_id=str(job_id),
        project_id=project_id_str,
        records=body.records,
        user_id=str(user_id),
    )
    supabase.table("jobs").update({"celery_task_id": task.id}).eq("id", job_id).execute()

    return {"job_id": job_id, "status": "processing"}
