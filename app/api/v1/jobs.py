"""
Job management endpoints - View and manage background jobs.
"""

import logging
from fastapi import APIRouter, Depends, HTTPException, status, Query
from supabase import create_client
from uuid import UUID
from typing import List, Optional

from app.dependencies import get_current_user
from app.config import settings
from app.models.schemas import JobResponse
from app.models.enums import JobStatus

logger = logging.getLogger(__name__)

router = APIRouter()

# Initialize Supabase client
supabase = create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_KEY)


@router.get("", response_model=List[JobResponse])
async def list_jobs(
    project_id: Optional[UUID] = Query(None),
    limit: int = Query(default=50, le=500),
    offset: int = Query(default=0, ge=0),
    user_id: UUID = Depends(get_current_user)
):
    """List background jobs."""
    try:
        query = supabase.table("jobs").select("*")

        if project_id:
            # Verify project belongs to user
            project_result = supabase.table("projects")\
                .select("id")\
                .eq("id", str(project_id))\
                .eq("user_id", str(user_id))\
                .execute()

            if not project_result.data:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Project not found"
                )

            query = query.eq("project_id", str(project_id))

        else:
            # Get all jobs from user's projects
            projects_result = supabase.table("projects")\
                .select("id")\
                .eq("user_id", str(user_id))\
                .execute()

            project_ids = [p["id"] for p in (projects_result.data or [])]

            if not project_ids:
                return []

            query = query.in_("project_id", project_ids)

        result = query.order("created_at", desc=True).range(offset, offset + limit - 1).execute()

        jobs = result.data or []
        return [JobResponse(**job) for job in jobs]

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Error listing jobs")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred"
        )


@router.get("/{job_id}", response_model=JobResponse)
async def get_job(
    job_id: UUID,
    user_id: UUID = Depends(get_current_user)
):
    """Get a specific job by ID."""
    try:
        result = supabase.table("jobs")\
            .select("*")\
            .eq("id", str(job_id))\
            .execute()

        if not result.data:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Job not found"
            )

        job = result.data[0]

        # Verify ownership via project
        if job.get("project_id"):
            project_result = supabase.table("projects")\
                .select("id")\
                .eq("id", job["project_id"])\
                .eq("user_id", str(user_id))\
                .execute()

            if not project_result.data:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Job not found"
                )

        return JobResponse(**job)

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Error getting job")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred"
        )


@router.get("/{job_id}/status", response_model=JobResponse)
async def get_job_status(
    job_id: UUID,
    user_id: UUID = Depends(get_current_user)
):
    """Get job status (alias for get job)."""
    return await get_job(job_id, user_id)


@router.post("/{job_id}/cancel", status_code=status.HTTP_200_OK)
async def cancel_job(
    job_id: UUID,
    user_id: UUID = Depends(get_current_user)
):
    """Cancel a running job."""
    try:
        result = supabase.table("jobs")\
            .select("*")\
            .eq("id", str(job_id))\
            .execute()

        if not result.data:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Job not found"
            )

        job = result.data[0]

        # Verify ownership
        if job.get("project_id"):
            project_result = supabase.table("projects")\
                .select("id")\
                .eq("id", job["project_id"])\
                .eq("user_id", str(user_id))\
                .execute()

            if not project_result.data:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Job not found"
                )

        # Check if already terminal
        if job["status"] in [JobStatus.COMPLETED.value, JobStatus.FAILED.value, JobStatus.CANCELLED.value]:
            return {
                "message": f"Job already {job['status']}",
                "status": job["status"]
            }

        # Revoke Celery task if exists
        revoke_failed = False
        if job.get("celery_task_id"):
            try:
                from celery import current_app
                current_app.control.revoke(job["celery_task_id"], terminate=True)
            except Exception as e:
                logger.error(f"Failed to revoke task {job['celery_task_id']}: {e}")
                revoke_failed = True

        # Update job status only if still pending or processing
        supabase.table("jobs").update({
            "status": JobStatus.CANCELLED.value,
            "error_message": "Cancelled by user"
        }).eq("id", str(job_id)).in_("status", ["pending", "processing"]).execute()

        if revoke_failed:
            return {
                "message": "Job marked as cancelled but task revocation failed — task may still be running",
                "status": "cancelled"
            }

        return {
            "message": "Job cancelled successfully",
            "status": "cancelled"
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Error cancelling job")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred"
        )
