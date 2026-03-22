"""
Form management endpoints - Create extraction forms and trigger code generation.
"""

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status, Request, Query
from pydantic import BaseModel
from supabase import create_client
from uuid import UUID
from typing import List, Optional
import json

import logging
from app.dependencies import get_current_user
from app.config import settings
from app.models.schemas import FormCreate, FormUpdate, FormResponse
from app.models.enums import FormStatus, JobType, JobStatus
from app.rate_limits import (
    RATE_LIMIT_FORM_CREATE, RATE_LIMIT_FORM_LIST,
    RATE_LIMIT_FORM_MUTATE, RATE_LIMIT_FORM_REVIEW,
)
from app.services.project_access import check_project_access
from app.rate_limit import limiter
from app.services.activity_service import log_activity
from app.services.cache_service import cache_service

# Cache TTL constants
FORM_LIST_CACHE_TTL = 120   # 2 minutes for list queries
FORM_DETAIL_CACHE_TTL = 300  # 5 minutes for individual form

logger = logging.getLogger(__name__)


router = APIRouter()

# Initialize Supabase client
supabase = create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_KEY)


def _invalidate_form_cache(project_id: str = None, form_id: str = None):
    """Invalidate form-related caches after a mutation."""
    if project_id:
        cache_service.delete_pattern(f"forms:project:{project_id}:*")
    if form_id:
        cache_service.delete(f"forms:detail:{form_id}")


@router.post("", response_model=FormResponse, status_code=status.HTTP_201_CREATED)
@limiter.limit(RATE_LIMIT_FORM_CREATE)
async def create_form(
    request: Request,
    form_data: FormCreate,
    background_tasks: BackgroundTasks,
    user_id: UUID = Depends(get_current_user)
):
    """
    Create a new extraction form and trigger DSPy code generation.

    - **project_id**: Project to create form in
    - **form_name**: Name of the form
    - **form_description**: Description of what this form extracts
    - **fields**: List of field definitions for extraction
    - **enable_review**: Enable human review in code generation workflow

    This will:
    1. Create form record in database
    2. Trigger background code generation job
    3. Return form with status "pending"
    """
    try:
        # Verify project access and create forms permission
        await check_project_access(form_data.project_id, user_id, "can_create_forms")

        # Check for duplicate name in same project
        existing = supabase.table("forms")\
            .select("id")\
            .eq("project_id", str(form_data.project_id))\
            .eq("form_name", form_data.form_name)\
            .execute()
        if existing.data:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f'A form named "{form_data.form_name}" already exists in this project'
            )

        # Convert FieldDefinition objects to dictionaries
        fields_dict = [field.model_dump() for field in form_data.fields]

        # Create form record
        form_record = {
            "project_id": str(form_data.project_id),
            "form_name": form_data.form_name,
            "form_description": form_data.form_description,
            "fields": json.dumps(fields_dict),
            "status": FormStatus.GENERATING.value,
            "schema_name": None,
            "task_dir": None,
            "statistics": None,
            "error": None
        }

        result = supabase.table("forms").insert(form_record).execute()

        if not result.data:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to create form record"
            )

        form = result.data[0]

        # Invalidate list cache for this project
        _invalidate_form_cache(project_id=str(form_data.project_id))

        # Create background job for code generation
        from app.workers.generation_tasks import generate_form_code

        job_data = {
            "user_id": str(user_id),
            "project_id": str(form_data.project_id),
            "job_type": JobType.FORM_GENERATION.value,
            "status": JobStatus.PENDING.value,
            "progress": 0,
            "input_data": {
                "form_id": form["id"],
                "form_name": form_data.form_name,
                "enable_review": form_data.enable_review
            }
        }
        job_result = supabase.table("jobs").insert(job_data).execute()

        if job_result.data:
            job = job_result.data[0]
            job_id = job["id"]

            # Trigger Celery task for code generation
            celery_task = generate_form_code.delay(
                form_id=form["id"],
                job_id=str(job_id),
                enable_review=form_data.enable_review
            )

            # Update job with Celery task ID in the background (non-blocking)
            async def _update_job_task_id(job_id: str, task_id: str):
                try:
                    supabase.table("jobs").update({"celery_task_id": task_id}).eq("id", job_id).execute()
                except Exception as e:
                    logger.error(f"Failed to update job {job_id} celery_task_id: {e}")

            background_tasks.add_task(_update_job_task_id, str(job_id), celery_task.id)

        background_tasks.add_task(
            log_activity,
            user_id=user_id,
            action_type="form_create",
            action="Form Created",
            description=f"Created form: {form_data.form_name}",
            project_id=form_data.project_id,
            metadata={"form_id": form["id"], "form_name": form_data.form_name},
        )

        # Parse JSON strings back to dicts/lists
        form["fields"] = json.loads(form["fields"])
        if isinstance(form.get("statistics"), str):
            form["statistics"] = json.loads(form["statistics"])

        return FormResponse(**form)

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to create form")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred"
        )


@router.get("", response_model=List[FormResponse])
@limiter.limit(RATE_LIMIT_FORM_LIST)
async def list_forms(
    request: Request,
    project_id: Optional[UUID] = None,
    search: Optional[str] = Query(default=None, max_length=255),
    limit: int = Query(default=50, le=500),
    offset: int = Query(default=0, ge=0),
    user_id: UUID = Depends(get_current_user)
):
    """
    List extraction forms.

    - **project_id** (optional): Filter by project
    - **search** (optional): Filter by form name (case-insensitive, partial match)

    If project_id is provided, only returns forms from that project.
    Otherwise, returns all forms from all user's projects.
    """
    try:
        if project_id:
            # Verify project access and view permission
            await check_project_access(project_id, user_id, "can_view_docs")

            # Check cache (only for non-search queries)
            if not search:
                cache_key = f"forms:project:{project_id}:list:{limit}:{offset}"
                cached = cache_service.get(cache_key)
                if cached is not None:
                    return [FormResponse(**f) for f in cached]

            # Get forms for specific project
            query = supabase.table("forms")\
                .select("*")\
                .eq("project_id", str(project_id))

            if search:
                query = query.ilike("form_name", f"%{search}%")

            result = query\
                .order("created_at", desc=True)\
                .range(offset, offset + limit - 1)\
                .execute()
        else:
            # Get all forms from user's owned + member projects
            owned_result = supabase.table("projects")\
                .select("id")\
                .eq("user_id", str(user_id))\
                .execute()
            member_result = supabase.table("project_members")\
                .select("project_id")\
                .eq("user_id", str(user_id))\
                .eq("can_view_docs", True)\
                .execute()
            owned_ids = [p["id"] for p in (owned_result.data or [])]
            member_ids = [r["project_id"] for r in (member_result.data or [])]
            project_ids = list(set(owned_ids + member_ids))

            if not project_ids:
                return []

            query = supabase.table("forms")\
                .select("*")\
                .in_("project_id", project_ids)

            if search:
                query = query.ilike("form_name", f"%{search}%")

            result = query\
                .order("created_at", desc=True)\
                .range(offset, offset + limit - 1)\
                .execute()

        forms = result.data or []

        # Parse JSON strings to dicts/lists
        for form in forms:
            if isinstance(form.get("fields"), str):
                form["fields"] = json.loads(form["fields"])
            if isinstance(form.get("statistics"), str):
                form["statistics"] = json.loads(form["statistics"])

        # Cache the result (only for project-scoped, non-search queries)
        if project_id and not search:
            cache_key = f"forms:project:{project_id}:list:{limit}:{offset}"
            cache_service.set(cache_key, forms, ttl=FORM_LIST_CACHE_TTL)

        return [FormResponse(**form) for form in forms]

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to list forms")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred"
        )


@router.get("/{form_id}", response_model=FormResponse)
@limiter.limit(RATE_LIMIT_FORM_LIST)
async def get_form(
    request: Request,
    form_id: UUID,
    user_id: UUID = Depends(get_current_user)
):
    """
    Get a specific form by ID.

    Returns 404 if form doesn't exist or doesn't belong to user's project.
    """
    try:
        # Check cache first
        cache_key = f"forms:detail:{form_id}"
        cached = cache_service.get(cache_key)
        if cached is not None:
            # Still verify access on cached results
            await check_project_access(UUID(cached["project_id"]), user_id, "can_view_docs")
            return FormResponse(**cached)

        # Get form
        result = supabase.table("forms")\
            .select("*")\
            .eq("id", str(form_id))\
            .execute()

        if not result.data:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Form not found"
            )

        form = result.data[0]

        # Verify project access and view permission
        await check_project_access(UUID(form["project_id"]), user_id, "can_view_docs")

        # Parse JSON strings to dicts/lists
        if isinstance(form.get("fields"), str):
            form["fields"] = json.loads(form["fields"])
        if isinstance(form.get("statistics"), str):
            form["statistics"] = json.loads(form["statistics"])

        # Cache the result
        cache_service.set(cache_key, form, ttl=FORM_DETAIL_CACHE_TTL)

        return FormResponse(**form)

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to get form %s", form_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred"
        )


@router.put("/{form_id}", response_model=FormResponse)
@limiter.limit(RATE_LIMIT_FORM_MUTATE)
async def update_form(
    request: Request,
    form_id: UUID,
    form_data: FormUpdate,
    background_tasks: BackgroundTasks,
    user_id: UUID = Depends(get_current_user)
):
    """
    Update a form's metadata (name, description, fields).

    Status transitions:
    - If fields are updated on an ACTIVE form, status changes to DRAFT
    - Schema name is invalidated when fields change
    - Code regeneration is required after field updates

    Note: Updating fields will NOT regenerate code automatically.
    Use POST /forms/{form_id}/regenerate to trigger code regeneration.
    """
    try:
        # Get form
        result = supabase.table("forms")\
            .select("*")\
            .eq("id", str(form_id))\
            .execute()

        if not result.data:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Form not found"
            )

        form = result.data[0]

        # Verify project access and create forms permission
        await check_project_access(UUID(form["project_id"]), user_id, "can_create_forms")

        # Check for duplicate name (exclude self)
        if form_data.form_name is not None:
            existing = supabase.table("forms")\
                .select("id")\
                .eq("project_id", form["project_id"])\
                .eq("form_name", form_data.form_name)\
                .neq("id", str(form_id))\
                .execute()
            if existing.data:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f'A form named "{form_data.form_name}" already exists in this project'
                )

        # Build update data
        update_data = {}
        if form_data.form_name is not None:
            update_data["form_name"] = form_data.form_name
        if form_data.form_description is not None:
            update_data["form_description"] = form_data.form_description

        fields_changed = False
        if form_data.fields is not None:
            update_data["fields"] = json.dumps([field.model_dump() for field in form_data.fields])
            fields_changed = True
        if form_data.enable_review is not None:
            update_data["enable_review"] = form_data.enable_review

        if not update_data:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No fields to update"
            )

        # If fields changed and form is ACTIVE, invalidate schema but don't move to DRAFT
        # (frontend will immediately trigger regeneration via the /regenerate endpoint)
        if fields_changed and form["status"] == FormStatus.ACTIVE.value:
            update_data["schema_name"] = None  # Invalidate schema until regeneration completes

        # Update form
        result = supabase.table("forms")\
            .update(update_data)\
            .eq("id", str(form_id))\
            .execute()

        updated_form = result.data[0]

        # Invalidate caches
        _invalidate_form_cache(project_id=form["project_id"], form_id=str(form_id))

        # Log activity
        changed_fields = list(update_data.keys())
        background_tasks.add_task(
            log_activity,
            user_id=user_id,
            action_type="form_update",
            action="Form Updated",
            description=f"Updated form: {updated_form.get('form_name', str(form_id))}",
            project_id=UUID(form["project_id"]),
            metadata={"form_id": str(form_id), "changed_fields": changed_fields},
        )

        # Parse JSON strings to dicts/lists
        if isinstance(updated_form.get("fields"), str):
            updated_form["fields"] = json.loads(updated_form["fields"])
        if isinstance(updated_form.get("statistics"), str):
            updated_form["statistics"] = json.loads(updated_form["statistics"])

        return FormResponse(**updated_form)

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to update form %s", form_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred"
        )


@router.delete("/{form_id}", status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit(RATE_LIMIT_FORM_MUTATE)
async def delete_form(
    request: Request,
    form_id: UUID,
    background_tasks: BackgroundTasks,
    user_id: UUID = Depends(get_current_user)
):
    """
    Delete a form.

    This will:
    - Delete the form record from database
    - Delete generated code directory (if exists)
    - CASCADE delete any extraction results

    Only forms from user's projects can be deleted.
    """
    try:
        # Get form
        result = supabase.table("forms")\
            .select("*")\
            .eq("id", str(form_id))\
            .execute()

        if not result.data:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Form not found"
            )

        form = result.data[0]

        # Verify project access and create forms permission
        await check_project_access(UUID(form["project_id"]), user_id, "can_create_forms")

        # Cancel any active Celery tasks for this form
        jobs_result = supabase.table("jobs")\
            .select("id, celery_task_id, status")\
            .eq("project_id", form["project_id"])\
            .in_("status", ["pending", "processing"])\
            .execute()

        if jobs_result.data:
            from app.workers.celery_app import celery_app as _celery_app
            for job in jobs_result.data:
                # Check if this job belongs to this form
                input_data = job.get("input_data") or {}
                if isinstance(input_data, str):
                    try:
                        import json as _json
                        input_data = _json.loads(input_data)
                    except Exception:
                        input_data = {}
                if str(input_data.get("form_id", "")) != str(form_id):
                    continue
                # Revoke the Celery task
                celery_task_id = job.get("celery_task_id")
                if celery_task_id:
                    try:
                        _celery_app.control.revoke(celery_task_id, terminate=True, signal="SIGTERM")
                    except Exception as e:
                        logger.error(f"Failed to revoke task {celery_task_id}: {e}")
                # Mark job as cancelled
                supabase.table("jobs").update({
                    "status": "cancelled",
                    "error_message": "Form deleted by user"
                }).eq("id", job["id"]).execute()

        # Delete generated code directory if exists
        if form.get("task_dir"):
            import shutil
            from pathlib import Path
            task_dir = Path(form["task_dir"])
            if task_dir.exists():
                shutil.rmtree(task_dir)

        # Delete form record from database
        supabase.table("forms")\
            .delete()\
            .eq("id", str(form_id))\
            .execute()

        # Invalidate caches
        _invalidate_form_cache(project_id=form["project_id"], form_id=str(form_id))

        background_tasks.add_task(
            log_activity,
            user_id=user_id,
            action_type="form_delete",
            action="Form Deleted",
            description=f"Deleted form: {form.get('form_name', str(form_id))}",
            project_id=UUID(form["project_id"]),
            metadata={"form_id": str(form_id), "form_name": form.get("form_name")},
        )

        return None  # 204 No Content

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to delete form %s", form_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred"
        )


@router.post("/{form_id}/regenerate")
@limiter.limit(RATE_LIMIT_FORM_MUTATE)
async def regenerate_form_code(
    request: Request,
    form_id: UUID,
    background_tasks: BackgroundTasks,
    enable_review: bool = False,
    user_id: UUID = Depends(get_current_user)
):
    """
    Regenerate DSPy code for a form.

    Useful when:
    - Initial code generation failed
    - Fields were updated
    - You want to try code generation with different settings

    - **enable_review**: Enable human review in workflow

    Returns form data with job_id for WebSocket log streaming.
    """
    try:
        # Get form
        result = supabase.table("forms")\
            .select("*")\
            .eq("id", str(form_id))\
            .execute()

        if not result.data:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Form not found"
            )

        form = result.data[0]

        # Verify project access and create forms permission
        await check_project_access(UUID(form["project_id"]), user_id, "can_create_forms")

        # Guard: don't start a second generation if one is already running
        if form["status"] in (FormStatus.GENERATING.value, FormStatus.REGENERATING.value):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Code generation is already in progress for this form"
            )

        # Reset form status to generating
        supabase.table("forms").update({
            "status": FormStatus.REGENERATING.value,
            "error": None
        }).eq("id", str(form_id)).execute()

        # Invalidate caches
        _invalidate_form_cache(project_id=form["project_id"], form_id=str(form_id))

        # Create new job for code generation
        from app.workers.generation_tasks import generate_form_code

        job_data = {
            "user_id": str(user_id),
            "project_id": form["project_id"],
            "job_type": JobType.FORM_GENERATION.value,
            "status": JobStatus.PENDING.value,
            "progress": 0,
            "input_data": {
                "form_id": form["id"],
                "form_name": form["form_name"],
                "enable_review": enable_review
            }
        }
        job_id = None
        job_result = supabase.table("jobs").insert(job_data).execute()

        if job_result.data:
            job = job_result.data[0]
            job_id = job["id"]

            # Trigger Celery task
            celery_task = generate_form_code.delay(
                form_id=form["id"],
                job_id=str(job_id),
                enable_review=enable_review
            )

            # Update job with Celery task ID
            supabase.table("jobs").update({
                "celery_task_id": celery_task.id
            }).eq("id", str(job_id)).execute()

        # Get updated form
        result = supabase.table("forms")\
            .select("*")\
            .eq("id", str(form_id))\
            .execute()

        updated_form = result.data[0]

        # Parse JSON strings to dicts/lists
        if isinstance(updated_form.get("fields"), str):
            updated_form["fields"] = json.loads(updated_form["fields"])
        if isinstance(updated_form.get("statistics"), str):
            updated_form["statistics"] = json.loads(updated_form["statistics"])

        background_tasks.add_task(
            log_activity,
            user_id=user_id,
            action_type="code_generation",
            action="Code Regenerated",
            description=f"Regenerated code for form: {form.get('form_name', str(form_id))}",
            project_id=UUID(form["project_id"]),
            metadata={"form_id": str(form_id), "form_name": form.get("form_name")},
        )

        # Return form with job_id for WebSocket streaming
        response_data = FormResponse(**updated_form).model_dump()
        if job_id is not None:
            response_data["job_id"] = str(job_id)

        return response_data

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to regenerate form code for form %s", form_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred"
        )


@router.post("/{form_id}/approve-decomposition")
@limiter.limit(RATE_LIMIT_FORM_REVIEW)
async def approve_decomposition(
    request: Request,
    form_id: UUID,
    background_tasks: BackgroundTasks,
    user_id: UUID = Depends(get_current_user)
):
    """
    Approve the decomposition and continue code generation.

    This endpoint is called when a form is in AWAITING_REVIEW status
    and the user approves the decomposition plan.

    Returns the updated form with continued generation status.
    """
    try:
        # Get form
        result = supabase.table("forms")\
            .select("*")\
            .eq("id", str(form_id))\
            .execute()

        if not result.data:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Form not found"
            )

        form = result.data[0]

        # Verify project access and create forms permission
        await check_project_access(UUID(form["project_id"]), user_id, "can_create_forms")

        # Check if form is in awaiting_review status
        if form["status"] != FormStatus.AWAITING_REVIEW.value:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Form is not awaiting review (current status: {form['status']})"
            )

        # Extract workflow metadata from form
        metadata_str = form.get("metadata")
        if not metadata_str:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No workflow metadata found in form"
            )

        try:
            metadata = json.loads(metadata_str) if isinstance(metadata_str, str) else metadata_str
            thread_id = metadata.get("thread_id")
            task_name = metadata.get("task_name")

            if not thread_id:
                raise ValueError("Missing thread_id in metadata")
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid metadata: {str(e)}"
            )

        # Update form status to generating
        supabase.table("forms").update({
            "status": FormStatus.GENERATING.value
        }).eq("id", str(form_id)).execute()

        # Invalidate caches
        _invalidate_form_cache(project_id=form["project_id"], form_id=str(form_id))

        # Resume workflow in background using Celery
        from app.workers.generation_tasks import resume_after_approval

        # Create job for tracking
        job_data = {
            "user_id": str(user_id),
            "project_id": form["project_id"],
            "job_type": JobType.FORM_GENERATION.value,
            "status": JobStatus.PROCESSING.value,
            "progress": 50,
            "input_data": {
                "form_id": str(form_id),
                "form_name": form["form_name"],
                "thread_id": thread_id,
                "action": "approve"
            }
        }
        job_result = supabase.table("jobs").insert(job_data).execute()
        job_id = job_result.data[0]["id"]

        # Trigger Celery task to resume workflow
        celery_task = resume_after_approval.delay(
            form_id=str(form_id),
            job_id=str(job_id),
            thread_id=thread_id,
            task_name=task_name
        )

        # Update job with Celery task ID
        supabase.table("jobs").update({
            "celery_task_id": celery_task.id
        }).eq("id", str(job_id)).execute()

        background_tasks.add_task(
            log_activity,
            user_id=user_id,
            action_type="code_generation",
            action="Form Approved",
            description=f"Approved decomposition for form: {form.get('form_name', str(form_id))}",
            project_id=UUID(form["project_id"]),
            metadata={"form_id": str(form_id), "form_name": form.get("form_name")},
        )

        return {
            "message": "Decomposition approved, continuing generation",
            "form_id": str(form_id),
            "job_id": str(job_id),
            "status": "generating"
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to approve decomposition for form %s", form_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred"
        )


class RejectDecompositionRequest(BaseModel):
    feedback: str


@router.post("/{form_id}/reject-decomposition")
@limiter.limit(RATE_LIMIT_FORM_REVIEW)
async def reject_decomposition(
    request: Request,
    form_id: UUID,
    body: RejectDecompositionRequest,
    background_tasks: BackgroundTasks,
    user_id: UUID = Depends(get_current_user)
):
    """
    Reject the decomposition and provide feedback for regeneration.

    This endpoint is called when a form is in AWAITING_REVIEW status
    and the user wants changes to the decomposition plan.

    Args:
        feedback: User feedback explaining what needs to change

    Returns the updated form with regeneration status.
    """
    try:
        # Get form
        result = supabase.table("forms")\
            .select("*")\
            .eq("id", str(form_id))\
            .execute()

        if not result.data:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Form not found"
            )

        form = result.data[0]

        # Verify project access and create forms permission
        await check_project_access(UUID(form["project_id"]), user_id, "can_create_forms")

        # Check if form is in awaiting_review status
        if form["status"] != FormStatus.AWAITING_REVIEW.value:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Form is not awaiting review (current status: {form['status']})"
            )

        # Extract workflow metadata from form
        metadata_str = form.get("metadata")
        if not metadata_str:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No workflow metadata found in form"
            )

        try:
            metadata = json.loads(metadata_str) if isinstance(metadata_str, str) else metadata_str
            thread_id = metadata.get("thread_id")
            task_name = metadata.get("task_name")

            if not thread_id:
                raise ValueError("Missing thread_id in metadata")
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid metadata: {str(e)}"
            )

        # Update form status to regenerating
        supabase.table("forms").update({
            "status": FormStatus.REGENERATING.value,
            "error": None
        }).eq("id", str(form_id)).execute()

        # Invalidate caches
        _invalidate_form_cache(project_id=form["project_id"], form_id=str(form_id))

        # Resume workflow with feedback in background using Celery
        from app.workers.generation_tasks import resume_after_rejection

        # Create job for tracking
        job_data = {
            "user_id": str(user_id),
            "project_id": form["project_id"],
            "job_type": JobType.FORM_GENERATION.value,
            "status": JobStatus.PROCESSING.value,
            "progress": 25,
            "input_data": {
                "form_id": str(form_id),
                "form_name": form["form_name"],
                "thread_id": thread_id,
                "action": "reject",
                "feedback": body.feedback
            }
        }
        job_result = supabase.table("jobs").insert(job_data).execute()
        job_id = job_result.data[0]["id"]

        # Trigger Celery task to resume workflow with feedback
        celery_task = resume_after_rejection.delay(
            form_id=str(form_id),
            job_id=str(job_id),
            thread_id=thread_id,
            task_name=task_name,
            feedback=body.feedback
        )

        # Update job with Celery task ID
        supabase.table("jobs").update({
            "celery_task_id": celery_task.id
        }).eq("id", str(job_id)).execute()

        background_tasks.add_task(
            log_activity,
            user_id=user_id,
            action_type="code_generation",
            action="Decomposition Rejected",
            description=f"Rejected decomposition for form: {form.get('form_name', str(form_id))}",
            project_id=UUID(form["project_id"]),
            metadata={"form_id": str(form_id), "form_name": form.get("form_name"), "feedback": body.feedback},
        )

        return {
            "message": "Feedback received, regenerating decomposition",
            "form_id": str(form_id),
            "job_id": str(job_id),
            "feedback": body.feedback,
            "status": "regenerating"
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to reject decomposition for form %s", form_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred"
        )
