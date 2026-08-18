"""
EndNote (.enlx) library import endpoints.

Two-step flow so the user reviews before anything is created:
  1. POST /api/v1/endnote/preview  — parse the uploaded library and return the
     references it contains (metadata + which have a PDF). Creates NOTHING.
  2. POST /api/v1/endnote/import   — commit: stash the library in S3 and hand off
     to the `import_endnote_library` Celery task, optionally restricted to a
     selected subset of reference ids (ref_ids). Returns a job_id; progress
     streams over ws_jobs:{job_id}.

References WITH a PDF go through the same Datalab pipeline a manual upload uses;
references WITHOUT a PDF land as `needs_pdf` so a reviewer can attach one later
via POST /documents/{id}/attach-pdf. Mirrors pubmed.py / clinical_trials.py.
"""

import json
import logging
import os
import tempfile
import uuid
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from supabase import create_client

from app.config import settings
from app.dependencies import get_current_user
from app.models.enums import JobStatus, JobType
from app.services import endnote_service
from app.services.project_access import check_project_access
from app.services.storage_service import storage_service

logger = logging.getLogger(__name__)
router = APIRouter()

supabase = create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_KEY)

_MAX_ENLX_BYTES = 500 * 1024 * 1024  # libraries with many attached PDFs get large


def _validate_enlx_upload(filename: str, data: bytes) -> None:
    if not (filename or "").lower().endswith(".enlx"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Expected an EndNote compressed library (.enlx).",
        )
    if len(data) > _MAX_ENLX_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="EndNote library exceeds the maximum allowed size.",
        )
    if data[:2] != b"PK":  # .enlx is a ZIP archive — local file signature "PK"
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="File is not a valid .enlx archive.",
        )


@router.post("/preview")
async def preview_endnote(
    project_id: UUID = Form(...),
    file: UploadFile = File(...),
    user_id: UUID = Depends(get_current_user),
):
    """Parse an uploaded .enlx and return its references — WITHOUT importing
    anything — so the user can review/select before committing."""
    filename = file.filename or "library.enlx"
    data = await file.read()
    _validate_enlx_upload(filename, data)
    await check_project_access(project_id, user_id, "can_upload_docs")

    tmp = tempfile.NamedTemporaryFile(prefix="enlx_preview_", suffix=".enlx", delete=False)
    try:
        tmp.write(data)
        tmp.close()
        records = endnote_service.parse_enlx(tmp.name)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception:
        logger.exception("Failed to parse .enlx for preview")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to read the EndNote library.",
        )
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass

    with_pdf = sum(1 for r in records if r.has_pdf)
    return {
        "filename": filename,
        "total": len(records),
        "with_pdf": with_pdf,
        "needs_pdf": len(records) - with_pdf,
        "records": [
            {
                "ref_id": r.ref_id,
                "title": r.title,
                "authors": "; ".join(r.authors) if r.authors else None,
                "year": r.year,
                "journal": r.journal,
                "doi": r.doi,
                "pmid": r.pmid,
                "pmcid": r.pmcid,
                # For the frontend's availability probe (POST /api/v1/citations/
                # availability) on references with no embedded PDF — the raw
                # URL(s) mined at parse time, tried as a last resort there too.
                "urls": r.urls,
                "has_pdf": r.has_pdf,
            }
            for r in records
        ],
    }


def _parse_ref_ids(ref_ids: Optional[str]) -> Optional[List[int]]:
    if not ref_ids:
        return None
    try:
        parsed = json.loads(ref_ids)
        if not isinstance(parsed, list):
            raise ValueError
        return [int(x) for x in parsed]
    except (ValueError, TypeError):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid ref_ids.")


@router.post("/import", status_code=status.HTTP_202_ACCEPTED)
async def import_endnote(
    project_id: UUID = Form(...),
    file: UploadFile = File(...),
    ref_ids: Optional[str] = Form(None),
    user_id: UUID = Depends(get_current_user),
):
    """Commit the import. `ref_ids` (optional JSON array) restricts the import to
    the references the user selected in the preview; omit to import all. Returns
    a job_id; follow progress over ws_jobs:{job_id}."""
    filename = file.filename or "library.enlx"
    data = await file.read()
    _validate_enlx_upload(filename, data)
    await check_project_access(project_id, user_id, "can_upload_docs")
    selected_ids = _parse_ref_ids(ref_ids)

    project_id_str = str(project_id)
    token = uuid.uuid4().hex
    try:
        s3_key = storage_service.upload_import_file(data, project_id_str, token, ext="enlx")
    except Exception:
        logger.exception("Failed to stash uploaded .enlx in S3")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to store the uploaded library.",
        )

    job_data = {
        "user_id": str(user_id),
        "project_id": project_id_str,
        "job_type": JobType.IMPORT.value,
        "status": JobStatus.PENDING.value,
        "progress": 0,
        "input_data": {"s3_key": s3_key, "filename": filename, "kind": "endnote", "ref_ids": selected_ids},
    }
    job_result = supabase.table("jobs").insert(job_data).execute()
    if not job_result.data:
        storage_service.delete_object(s3_key)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create import job.",
        )
    job_id = job_result.data[0]["id"]

    from app.workers.import_tasks import import_endnote_library

    task = import_endnote_library.delay(
        job_id=str(job_id),
        project_id=project_id_str,
        s3_key=s3_key,
        user_id=str(user_id),
        ref_ids=selected_ids,
    )
    supabase.table("jobs").update({"celery_task_id": task.id}).eq("id", job_id).execute()

    return {"job_id": job_id, "status": "processing"}
