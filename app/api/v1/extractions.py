"""
Extraction job endpoints - Create and manage extraction jobs.
"""

from fastapi import APIRouter, Depends, HTTPException, status, Query, Request
from supabase import create_client
from uuid import UUID
from typing import List, Optional

from app.dependencies import get_current_user
from app.config import settings
from app.models.schemas import ExtractionCreate, ExtractionResponse
from app.models.enums import JobType, JobStatus
from app.rate_limits import RATE_LIMIT_EXTRACTION_CREATE, RATE_LIMIT_EXTRACTION_LIST


router = APIRouter()

# Initialize Supabase client
supabase = create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)


@router.post("", response_model=ExtractionResponse, status_code=status.HTTP_201_CREATED)
async def create_extraction_job(
    request: Request,
    extraction_data: ExtractionCreate,
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
        # Verify project exists and belongs to user
        project_result = supabase.table("projects")\
            .select("id")\
            .eq("id", str(extraction_data.project_id))\
            .eq("user_id", str(user_id))\
            .execute()

        if not project_result.data:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Project not found"
            )

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

        # If specific documents requested, verify they exist and belong to project
        if extraction_data.document_ids:
            for doc_id in extraction_data.document_ids:
                doc_result = supabase.table("documents")\
                    .select("id, processing_status")\
                    .eq("id", str(doc_id))\
                    .eq("project_id", str(extraction_data.project_id))\
                    .execute()

                if not doc_result.data:
                    raise HTTPException(
                        status_code=status.HTTP_404_NOT_FOUND,
                        detail=f"Document {doc_id} not found or doesn't belong to this project"
                    )

                # Check document has been processed
                if doc_result.data[0]["processing_status"] != "completed":
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

        # Update job with Celery task ID
        supabase.table("jobs").update({
            "celery_task_id": celery_task.id
        }).eq("id", str(job_id)).execute()

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
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error creating extraction: {str(e)}"
        )


@router.get("", response_model=List[ExtractionResponse])
async def list_extractions(
    project_id: Optional[UUID] = Query(None),
    user_id: UUID = Depends(get_current_user)
):
    """
    List extraction jobs.

    - **project_id** (optional): Filter by project

    Returns extractions sorted by creation date (newest first).
    """
    try:
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

            # Get extractions for specific project
            result = supabase.table("extractions")\
                .select("*")\
                .eq("project_id", str(project_id))\
                .order("created_at", desc=True)\
                .execute()
        else:
            # Get all extractions from user's projects
            projects_result = supabase.table("projects")\
                .select("id")\
                .eq("user_id", str(user_id))\
                .execute()

            project_ids = [p["id"] for p in (projects_result.data or [])]

            if not project_ids:
                return []

            result = supabase.table("extractions")\
                .select("*")\
                .in_("project_id", project_ids)\
                .order("created_at", desc=True)\
                .execute()

        extractions = result.data or []

        # Get job IDs for each extraction
        response_list = []
        for extraction in extractions:
            # Find associated job
            job_result = supabase.table("jobs")\
                .select("id")\
                .eq("job_type", JobType.EXTRACTION.value)\
                .contains("input_data", {"extraction_id": extraction["id"]})\
                .order("created_at", desc=True)\
                .limit(1)\
                .execute()

            job_id = UUID(job_result.data[0]["id"]) if job_result.data else None

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
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error listing extractions: {str(e)}"
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

        # Verify extraction's project belongs to user
        project_result = supabase.table("projects")\
            .select("id")\
            .eq("id", extraction["project_id"])\
            .eq("user_id", str(user_id))\
            .execute()

        if not project_result.data:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Extraction not found"
            )

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
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error getting extraction: {str(e)}"
        )


@router.delete("/{extraction_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_extraction(
    extraction_id: UUID,
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

        # Verify extraction's project belongs to user
        project_result = supabase.table("projects")\
            .select("id")\
            .eq("id", extraction["project_id"])\
            .eq("user_id", str(user_id))\
            .execute()

        if not project_result.data:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Extraction not found"
            )

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
                    except Exception:
                        pass
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

        return None  # 204 No Content

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error deleting extraction: {str(e)}"
        )


@router.post("/{extraction_id}/cancel", status_code=status.HTTP_200_OK)
async def cancel_extraction(
    extraction_id: UUID,
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

        # Verify extraction's project belongs to user
        project_result = supabase.table("projects")\
            .select("id")\
            .eq("id", extraction["project_id"])\
            .eq("user_id", str(user_id))\
            .execute()

        if not project_result.data:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Extraction not found"
            )

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
                from celery import current_app
                current_app.control.revoke(job["celery_task_id"], terminate=True)

            # Update job status
            supabase.table("jobs").update({
                "status": JobStatus.FAILED.value,
                "error_message": "Cancelled by user"
            }).eq("id", job["id"]).execute()

        # Update extraction status
        supabase.table("extractions").update({
            "status": "cancelled"
        }).eq("id", str(extraction_id)).execute()

        return {
            "message": "Extraction cancelled successfully",
            "status": "cancelled"
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error cancelling extraction: {str(e)}"
        )
