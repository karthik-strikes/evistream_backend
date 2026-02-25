"""
Form management endpoints - Create extraction forms and trigger code generation.
"""

from fastapi import APIRouter, Depends, HTTPException, status, Request
from supabase import create_client
from uuid import UUID
from typing import List, Optional
import json

from app.dependencies import get_current_user, get_optional_user
from app.config import settings
from app.models.schemas import FormCreate, FormUpdate, FormResponse
from app.models.enums import FormStatus, JobType, JobStatus
from app.rate_limits import RATE_LIMIT_FORM_CREATE, RATE_LIMIT_FORM_LIST


router = APIRouter()

# Initialize Supabase client
supabase = create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)


@router.post("", response_model=FormResponse, status_code=status.HTTP_201_CREATED)
async def create_form(
    request: Request,
    form_data: FormCreate,
    user_id: UUID = Depends(get_optional_user)
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
        # Use placeholder user ID if not authenticated (dev mode)
        effective_user_id = user_id or UUID("00000000-0000-0000-0000-000000000001")

        # Verify project exists and belongs to user
        project_result = supabase.table("projects")\
            .select("id")\
            .eq("id", str(form_data.project_id))\
            .eq("user_id", str(effective_user_id))\
            .execute()

        if not project_result.data:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Project not found"
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

        # Create background job for code generation
        from app.workers.generation_tasks import generate_form_code

        job_data = {
            "user_id": str(effective_user_id),
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

            # Update job with Celery task ID
            supabase.table("jobs").update({
                "celery_task_id": celery_task.id
            }).eq("id", str(job_id)).execute()

        # Parse JSON strings back to dicts/lists
        form["fields"] = json.loads(form["fields"])
        if isinstance(form.get("statistics"), str):
            form["statistics"] = json.loads(form["statistics"])

        return FormResponse(**form)

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error creating form: {str(e)}"
        )


@router.get("", response_model=List[FormResponse])
async def list_forms(
    project_id: Optional[UUID] = None,
    user_id: UUID = Depends(get_current_user)
):
    """
    List extraction forms.

    - **project_id** (optional): Filter by project

    If project_id is provided, only returns forms from that project.
    Otherwise, returns all forms from all user's projects.
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

            # Get forms for specific project
            result = supabase.table("forms")\
                .select("*")\
                .eq("project_id", str(project_id))\
                .order("created_at", desc=True)\
                .execute()
        else:
            # Get all forms from user's projects
            projects_result = supabase.table("projects")\
                .select("id")\
                .eq("user_id", str(user_id))\
                .execute()

            project_ids = [p["id"] for p in (projects_result.data or [])]

            if not project_ids:
                return []

            result = supabase.table("forms")\
                .select("*")\
                .in_("project_id", project_ids)\
                .order("created_at", desc=True)\
                .execute()

        forms = result.data or []

        # Parse JSON strings to dicts/lists
        for form in forms:
            if isinstance(form.get("fields"), str):
                form["fields"] = json.loads(form["fields"])
            if isinstance(form.get("statistics"), str):
                form["statistics"] = json.loads(form["statistics"])

        return [FormResponse(**form) for form in forms]

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error listing forms: {str(e)}"
        )


@router.get("/{form_id}", response_model=FormResponse)
async def get_form(
    form_id: UUID,
    user_id: UUID = Depends(get_current_user)
):
    """
    Get a specific form by ID.

    Returns 404 if form doesn't exist or doesn't belong to user's project.
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

        # Verify form's project belongs to user
        project_result = supabase.table("projects")\
            .select("id")\
            .eq("id", form["project_id"])\
            .eq("user_id", str(user_id))\
            .execute()

        if not project_result.data:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Form not found"
            )

        # Parse JSON strings to dicts/lists
        if isinstance(form.get("fields"), str):
            form["fields"] = json.loads(form["fields"])
        if isinstance(form.get("statistics"), str):
            form["statistics"] = json.loads(form["statistics"])

        return FormResponse(**form)

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error getting form: {str(e)}"
        )


@router.put("/{form_id}", response_model=FormResponse)
async def update_form(
    form_id: UUID,
    form_data: FormUpdate,
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

        # Verify form's project belongs to user
        project_result = supabase.table("projects")\
            .select("id")\
            .eq("id", form["project_id"])\
            .eq("user_id", str(user_id))\
            .execute()

        if not project_result.data:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Form not found"
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

        if not update_data:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No fields to update"
            )

        # If fields changed and form is ACTIVE, mark as DRAFT (needs regeneration)
        if fields_changed and form["status"] == FormStatus.ACTIVE.value:
            update_data["status"] = FormStatus.DRAFT.value
            update_data["schema_name"] = None  # Invalidate schema
            # Note: task_dir is kept for potential regeneration

        # Update form
        result = supabase.table("forms")\
            .update(update_data)\
            .eq("id", str(form_id))\
            .execute()

        updated_form = result.data[0]

        # Parse JSON strings to dicts/lists
        if isinstance(updated_form.get("fields"), str):
            updated_form["fields"] = json.loads(updated_form["fields"])
        if isinstance(updated_form.get("statistics"), str):
            updated_form["statistics"] = json.loads(updated_form["statistics"])

        return FormResponse(**updated_form)

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error updating form: {str(e)}"
        )


@router.delete("/{form_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_form(
    form_id: UUID,
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

        # Verify form's project belongs to user
        project_result = supabase.table("projects")\
            .select("id")\
            .eq("id", form["project_id"])\
            .eq("user_id", str(user_id))\
            .execute()

        if not project_result.data:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Form not found"
            )

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

        return None  # 204 No Content

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error deleting form: {str(e)}"
        )


@router.post("/{form_id}/regenerate")
async def regenerate_form_code(
    form_id: UUID,
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

        # Verify form's project belongs to user
        project_result = supabase.table("projects")\
            .select("id")\
            .eq("id", form["project_id"])\
            .eq("user_id", str(user_id))\
            .execute()

        if not project_result.data:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Form not found"
            )

        # Reset form status to generating
        supabase.table("forms").update({
            "status": FormStatus.REGENERATING.value,
            "error": None
        }).eq("id", str(form_id)).execute()

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

        # Return form with job_id for WebSocket streaming
        response_data = FormResponse(**updated_form).model_dump()
        response_data["job_id"] = str(job_id)

        return response_data

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error regenerating form code: {str(e)}"
        )


@router.post("/{form_id}/approve-decomposition")
async def approve_decomposition(
    form_id: UUID,
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

        # Verify form's project belongs to user
        project_result = supabase.table("projects")\
            .select("id")\
            .eq("id", form["project_id"])\
            .eq("user_id", str(user_id))\
            .execute()

        if not project_result.data:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Form not found"
            )

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

        return {
            "message": "Decomposition approved, continuing generation",
            "form_id": str(form_id),
            "job_id": str(job_id),
            "status": "generating"
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error approving decomposition: {str(e)}"
        )


@router.post("/{form_id}/reject-decomposition")
async def reject_decomposition(
    form_id: UUID,
    feedback: str,
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

        # Verify form's project belongs to user
        project_result = supabase.table("projects")\
            .select("id")\
            .eq("id", form["project_id"])\
            .eq("user_id", str(user_id))\
            .execute()

        if not project_result.data:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Form not found"
            )

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
                "feedback": feedback
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
            feedback=feedback
        )

        # Update job with Celery task ID
        supabase.table("jobs").update({
            "celery_task_id": celery_task.id
        }).eq("id", str(job_id)).execute()

        return {
            "message": "Feedback received, regenerating decomposition",
            "form_id": str(form_id),
            "job_id": str(job_id),
            "feedback": feedback,
            "status": "regenerating"
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error rejecting decomposition: {str(e)}"
        )
