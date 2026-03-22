"""
Extraction job endpoints - Create and manage extraction jobs.
"""

import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status, Query, Request
from supabase import create_client
from uuid import UUID
from typing import List, Optional

from app.dependencies import get_current_user
from app.config import settings
from app.models.schemas import ExtractionCreate, ExtractionResponse
from app.models.enums import JobType, JobStatus
from app.rate_limits import RATE_LIMIT_EXTRACTION_CREATE, RATE_LIMIT_EXTRACTION_LIST
from app.services.project_access import check_project_access
from app.rate_limit import limiter
from app.services.activity_service import log_activity


logger = logging.getLogger(__name__)

router = APIRouter()


def _update_queue_position(job_id: str):
    pending = supabase.table("jobs").select("id", count="exact") \
        .eq("job_type", "extraction") \
        .eq("status", "pending") \
        .execute()
    queue_position = pending.count or 1
    supabase.table("jobs").update({
        "result_data": {
            "queue_position": queue_position,
            "queue_message": f"Position {queue_position} in queue" if queue_position > 0 else "Starting soon"
        }
    }).eq("id", job_id).execute()

# Initialize Supabase client
supabase = create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_KEY)


@router.post("", response_model=ExtractionResponse, status_code=status.HTTP_201_CREATED)
@limiter.limit(RATE_LIMIT_EXTRACTION_CREATE)
async def create_extraction_job(
    request: Request,
    extraction_data: ExtractionCreate,
    background_tasks: BackgroundTasks,
    user_id: UUID = Depends(get_current_user)
):
    """
    Create a new extraction job.

    - **project_id**: Project containing documents to extract from
    - **form_id**: Form to use for extraction (must have completed code generation)
    - **document_ids** (optional): Specific documents to extract from
    - **max_documents** (optional): Limit number of documents to process

    This will:
    1. Validate form has generated code
    2. Create extraction record in database
    3. Create background job
    4. Trigger extraction worker
    5. Return extraction with status "pending"
    """
    try:
        # Verify project access and extraction permission
        await check_project_access(extraction_data.project_id, user_id, "can_run_extractions")

        # Verify form exists, belongs to project, and has completed code generation
        form_result = supabase.table("forms")\
            .select("*")\
            .eq("id", str(extraction_data.form_id))\
            .eq("project_id", str(extraction_data.project_id))\
            .execute()

        if not form_result.data:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Form not found or doesn't belong to this project"
            )

        form = form_result.data[0]

        # Check form has completed code generation
        if form["status"] != "active":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Form code generation not complete. Current status: {form['status']}"
            )

        if not form.get("schema_name"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Form has no generated schema"
            )

        # Guard A: per-user active extraction job limit
        active = supabase.table("jobs").select("id", count="exact")\
            .eq("user_id", str(user_id))\
            .eq("job_type", "extraction")\
            .in_("status", ["pending", "processing"])\
            .execute()
        if (active.count or 0) >= settings.MAX_CONCURRENT_EXTRACTIONS_PER_USER:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=(
                    f"You already have {active.count} active extraction job(s). "
                    f"Max is {settings.MAX_CONCURRENT_EXTRACTIONS_PER_USER}. "
                    "Wait for an existing job to complete."
                )
            )

        # Guard B: per-job document cap
        doc_ids = extraction_data.document_ids
        effective_count = len(doc_ids) if doc_ids else 999
        if effective_count > settings.MAX_DOCUMENTS_PER_EXTRACTION_JOB:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Maximum {settings.MAX_DOCUMENTS_PER_EXTRACTION_JOB} documents per job. "
                    f"You submitted {effective_count}. Split into multiple jobs."
                )
            )

        # If specific documents requested, verify they exist and belong to project
        if extraction_data.document_ids:
            doc_ids_str = [str(d) for d in extraction_data.document_ids]
            docs_result = supabase.table("documents") \
                .select("id, processing_status") \
                .in_("id", doc_ids_str) \
                .eq("project_id", str(extraction_data.project_id)) \
                .execute()

            found_map = {d["id"]: d for d in (docs_result.data or [])}

            for doc_id in doc_ids_str:
                if doc_id not in found_map:
                    raise HTTPException(
                        status_code=status.HTTP_404_NOT_FOUND,
                        detail=f"Document {doc_id} not found or doesn't belong to this project"
                    )
                if found_map[doc_id]["processing_status"] != "completed":
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=f"Document {doc_id} has not been processed yet"
                    )

        # Create extraction record
        extraction_record = {
            "project_id": str(extraction_data.project_id),
            "form_id": str(extraction_data.form_id),
            "status": "pending"
        }

        result = supabase.table("extractions").insert(extraction_record).execute()

        if not result.data:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to create extraction record"
            )

        extraction = result.data[0]

        # Create background job
        job_data = {
            "user_id": str(user_id),
            "project_id": str(extraction_data.project_id),
            "job_type": JobType.EXTRACTION.value,
            "status": JobStatus.PENDING.value,
            "progress": 0,
            "input_data": {
                "extraction_id": extraction["id"],
                "form_id": str(extraction_data.form_id),
                "document_ids": [str(d) for d in extraction_data.document_ids] if extraction_data.document_ids else None,
                "max_documents": extraction_data.max_documents
            }
        }

        job_result = supabase.table("jobs").insert(job_data).execute()

        if not job_result.data:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to create job record"
            )

        job = job_result.data[0]
        job_id = UUID(job["id"])

        # Trigger background extraction task
        from app.workers.extraction_tasks import run_extraction

        celery_task = run_extraction.delay(
            extraction_id=extraction["id"],
            job_id=str(job_id),
            document_ids=[str(d) for d in extraction_data.document_ids] if extraction_data.document_ids else None,
            max_documents=extraction_data.max_documents
        )

        # Update queue position and celery_task_id in the background (non-blocking)
        background_tasks.add_task(_update_queue_position, str(job_id))

        async def _update_job_task_id(job_id: str, task_id: str):
            try:
                supabase.table("jobs").update({"celery_task_id": task_id}).eq("id", job_id).execute()
            except Exception as e:
                logger.error(f"Failed to update job {job_id} celery_task_id: {e}")

        background_tasks.add_task(_update_job_task_id, str(job_id), celery_task.id)

        background_tasks.add_task(
            log_activity,
            user_id=user_id,
            action_type="extraction",
            action="Extraction Started",
            description=f"Started extraction with form '{form.get('form_name', '')}'",
            project_id=extraction_data.project_id,
            metadata={"extraction_id": extraction["id"], "form_id": str(extraction_data.form_id), "form_name": form.get("form_name")},
        )

        return ExtractionResponse(
            id=UUID(extraction["id"]),
            project_id=UUID(extraction["project_id"]),
            form_id=UUID(extraction["form_id"]),
            status=extraction["status"],
            job_id=job_id,
            created_at=extraction["created_at"]
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to create extraction job")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred"
        )


@router.get("", response_model=List[ExtractionResponse])
async def list_extractions(
    project_id: Optional[UUID] = Query(None),
    limit: int = Query(default=50, le=500),
    offset: int = Query(default=0, ge=0),
    user_id: UUID = Depends(get_current_user)
):
    """
    List extraction jobs.

    - **project_id** (optional): Filter by project

    Returns extractions sorted by creation date (newest first).
    """
    try:
        if project_id:
            # Verify project access and view results permission
            await check_project_access(project_id, user_id, "can_view_results")

            # Get extractions for specific project
            result = supabase.table("extractions")\
                .select("*")\
                .eq("project_id", str(project_id))\
                .order("created_at", desc=True)\
                .range(offset, offset + limit - 1)\
                .execute()
        else:
            # Get all extractions from user's owned + member projects
            owned_result = supabase.table("projects")\
                .select("id")\
                .eq("user_id", str(user_id))\
                .execute()
            member_result = supabase.table("project_members")\
                .select("project_id")\
                .eq("user_id", str(user_id))\
                .eq("can_view_results", True)\
                .execute()
            owned_ids = [p["id"] for p in (owned_result.data or [])]
            member_ids = [r["project_id"] for r in (member_result.data or [])]
            project_ids = list(set(owned_ids + member_ids))

            if not project_ids:
                return []

            result = supabase.table("extractions")\
                .select("*")\
                .in_("project_id", project_ids)\
                .order("created_at", desc=True)\
                .range(offset, offset + limit - 1)\
                .execute()

        extractions = result.data or []

        # Fetch all related jobs in one query, keyed by extraction_id
        extraction_to_job: dict = {}
        if extractions:
            project_ids_in_result = list({e["project_id"] for e in extractions})
            jobs_result = supabase.table("jobs")\
                .select("id, input_data")\
                .eq("job_type", JobType.EXTRACTION.value)\
                .in_("project_id", project_ids_in_result)\
                .order("created_at", desc=True)\
                .execute()
            for job in (jobs_result.data or []):
                eid = (job.get("input_data") or {}).get("extraction_id")
                if eid and eid not in extraction_to_job:
                    extraction_to_job[eid] = job["id"]

        response_list = []
        for extraction in extractions:
            raw_job_id = extraction_to_job.get(extraction["id"])
            job_id = UUID(raw_job_id) if raw_job_id else None
            response_list.append(ExtractionResponse(
                id=UUID(extraction["id"]),
                project_id=UUID(extraction["project_id"]),
                form_id=UUID(extraction["form_id"]),
                status=extraction["status"],
                job_id=job_id,
                created_at=extraction["created_at"]
            ))

        return response_list

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to list extractions")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred"
        )


@router.get("/{extraction_id}", response_model=ExtractionResponse)
async def get_extraction(
    extraction_id: UUID,
    user_id: UUID = Depends(get_current_user)
):
    """
    Get extraction job status.

    Returns extraction details including current status and job information.
    """
    try:
        # Get extraction
        result = supabase.table("extractions")\
            .select("*")\
            .eq("id", str(extraction_id))\
            .execute()

        if not result.data:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Extraction not found"
            )

        extraction = result.data[0]

        # Verify project access and view results permission
        await check_project_access(UUID(extraction["project_id"]), user_id, "can_view_results")

        # Find associated job
        job_result = supabase.table("jobs")\
            .select("id")\
            .eq("job_type", JobType.EXTRACTION.value)\
            .contains("input_data", {"extraction_id": str(extraction_id)})\
            .order("created_at", desc=True)\
            .limit(1)\
            .execute()

        job_id = UUID(job_result.data[0]["id"]) if job_result.data else None

        return ExtractionResponse(
            id=UUID(extraction["id"]),
            project_id=UUID(extraction["project_id"]),
            form_id=UUID(extraction["form_id"]),
            status=extraction["status"],
            job_id=job_id,
            created_at=extraction["created_at"]
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to get extraction %s", extraction_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred"
        )


@router.delete("/{extraction_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_extraction(
    extraction_id: UUID,
    background_tasks: BackgroundTasks,
    user_id: UUID = Depends(get_current_user)
):
    """
    Delete an extraction and its associated results.

    This will:
    - Delete associated extraction_results
    - Cancel associated job if still running
    - Delete the extraction record
    """
    try:
        # Get extraction
        result = supabase.table("extractions")\
            .select("*")\
            .eq("id", str(extraction_id))\
            .execute()

        if not result.data:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Extraction not found"
            )

        extraction = result.data[0]

        # Verify project access and extraction permission
        await check_project_access(UUID(extraction["project_id"]), user_id, "can_run_extractions")

        # Cancel associated job if still running
        job_result = supabase.table("jobs")\
            .select("*")\
            .eq("job_type", JobType.EXTRACTION.value)\
            .contains("input_data", {"extraction_id": str(extraction_id)})\
            .execute()

        for job in (job_result.data or []):
            if job["status"] in [JobStatus.PENDING.value, JobStatus.PROCESSING.value]:
                if job.get("celery_task_id"):
                    try:
                        from celery import current_app
                        current_app.control.revoke(job["celery_task_id"], terminate=True)
                    except Exception as e:
                        logger.error(f"Failed to revoke task {job['celery_task_id']}: {e}")
                supabase.table("jobs").update({
                    "status": JobStatus.CANCELLED.value,
                    "error_message": "Extraction deleted"
                }).eq("id", job["id"]).execute()

        # Delete associated extraction results
        supabase.table("extraction_results")\
            .delete()\
            .eq("extraction_id", str(extraction_id))\
            .execute()

        # Delete the extraction record
        supabase.table("extractions")\
            .delete()\
            .eq("id", str(extraction_id))\
            .execute()

        background_tasks.add_task(
            log_activity,
            user_id=user_id,
            action_type="extraction",
            action="Extraction Deleted",
            description=f"Deleted extraction {extraction_id}",
            project_id=UUID(extraction["project_id"]),
            metadata={"extraction_id": str(extraction_id)},
        )

        return None  # 204 No Content

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to delete extraction %s", extraction_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred"
        )


@router.post("/{extraction_id}/cancel", status_code=status.HTTP_200_OK)
async def cancel_extraction(
    extraction_id: UUID,
    background_tasks: BackgroundTasks,
    user_id: UUID = Depends(get_current_user)
):
    """
    Cancel an extraction job.

    Attempts to cancel the background Celery task if it's still running.
    Updates extraction status to "cancelled".
    """
    try:
        # Get extraction
        result = supabase.table("extractions")\
            .select("*")\
            .eq("id", str(extraction_id))\
            .execute()

        if not result.data:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Extraction not found"
            )

        extraction = result.data[0]

        # Verify project access and extraction permission
        await check_project_access(UUID(extraction["project_id"]), user_id, "can_run_extractions")

        # Check if extraction is already completed or failed
        if extraction["status"] in ["completed", "failed", "cancelled"]:
            return {
                "message": f"Extraction already {extraction['status']}",
                "status": extraction["status"]
            }

        # Find associated job
        job_result = supabase.table("jobs")\
            .select("*")\
            .eq("job_type", JobType.EXTRACTION.value)\
            .contains("input_data", {"extraction_id": str(extraction_id)})\
            .order("created_at", desc=True)\
            .limit(1)\
            .execute()

        if job_result.data:
            job = job_result.data[0]

            # Try to revoke Celery task if it exists
            if job.get("celery_task_id"):
                try:
                    from celery import current_app
                    current_app.control.revoke(job["celery_task_id"], terminate=True)
                except Exception as e:
                    logger.error(f"Failed to revoke task {job['celery_task_id']}: {e}")

            # Update job status only if still pending or processing
            supabase.table("jobs").update({
                "status": JobStatus.CANCELLED.value,
                "error_message": "Cancelled by user"
            }).eq("id", job["id"]).in_("status", ["pending", "processing"]).execute()

        # Update extraction status only if still pending or processing
        supabase.table("extractions").update({
            "status": "cancelled"
        }).eq("id", str(extraction_id)).in_("status", ["pending", "processing"]).execute()

        background_tasks.add_task(
            log_activity,
            user_id=user_id,
            action_type="extraction",
            action="Extraction Cancelled",
            description=f"Cancelled extraction {extraction_id}",
            project_id=UUID(extraction["project_id"]),
            metadata={"extraction_id": str(extraction_id)},
        )

        return {
            "message": "Extraction cancelled successfully",
            "status": "cancelled"
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to cancel extraction %s", extraction_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred"
        )


@router.post("/{extraction_id}/retry-failed", status_code=status.HTTP_200_OK)
async def retry_failed_extraction(
    extraction_id: UUID,
    background_tasks: BackgroundTasks,
    user_id: UUID = Depends(get_current_user)
):
    """
    Retry only the documents that failed in the most recent extraction run.

    Creates a new job for just the failed documents and updates the extraction
    status back to pending. Clears failed_document_ids from the previous job
    so the retry button disappears.
    """
    try:
        # Get extraction
        result = supabase.table("extractions")\
            .select("*")\
            .eq("id", str(extraction_id))\
            .execute()

        if not result.data:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Extraction not found"
            )

        extraction = result.data[0]

        # Verify project access and extraction permission
        await check_project_access(UUID(extraction["project_id"]), user_id, "can_run_extractions")

        # Find the most recent job for this extraction
        job_result = supabase.table("jobs")\
            .select("*")\
            .eq("job_type", JobType.EXTRACTION.value)\
            .contains("input_data", {"extraction_id": str(extraction_id)})\
            .order("created_at", desc=True)\
            .limit(1)\
            .execute()

        if not job_result.data:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="No job found for this extraction"
            )

        job = job_result.data[0]
        result_data = job.get("result_data") or {}
        failed_document_ids = result_data.get("failed_document_ids") or []

        if not failed_document_ids:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No failed documents to retry"
            )

        # Clear failed_document_ids from the current job so the retry button disappears
        updated_result_data = dict(result_data)
        updated_result_data["failed_document_ids"] = []
        supabase.table("jobs").update({
            "result_data": updated_result_data
        }).eq("id", job["id"]).execute()

        # Create new job for the retry
        new_job_data = {
            "user_id": str(user_id),
            "project_id": extraction["project_id"],
            "job_type": JobType.EXTRACTION.value,
            "status": JobStatus.PENDING.value,
            "progress": 0,
            "input_data": {
                "extraction_id": str(extraction_id),
                "form_id": str(extraction["form_id"]),
                "document_ids": failed_document_ids,
                "max_documents": None
            }
        }

        new_job_result = supabase.table("jobs").insert(new_job_data).execute()

        if not new_job_result.data:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to create retry job"
            )

        new_job = new_job_result.data[0]
        new_job_id = new_job["id"]

        # Update extraction status back to pending
        supabase.table("extractions").update({
            "status": "pending"
        }).eq("id", str(extraction_id)).execute()

        # Trigger the extraction task
        from app.workers.extraction_tasks import run_extraction

        celery_task = run_extraction.delay(
            extraction_id=str(extraction_id),
            job_id=new_job_id,
            document_ids=failed_document_ids,
            max_documents=None
        )

        # Update job with Celery task ID
        supabase.table("jobs").update({
            "celery_task_id": celery_task.id
        }).eq("id", new_job_id).execute()

        background_tasks.add_task(
            log_activity,
            user_id=user_id,
            action_type="extraction",
            action="Retry Failed Papers",
            description=f"Retrying {len(failed_document_ids)} failed papers for extraction {extraction_id}",
            project_id=UUID(extraction["project_id"]),
            metadata={"extraction_id": str(extraction_id), "retrying_count": len(failed_document_ids)},
        )

        return {
            "job_id": new_job_id,
            "retrying_count": len(failed_document_ids)
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to retry failed extraction %s", extraction_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred"
        )
