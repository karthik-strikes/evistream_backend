"""
Form management endpoints - Create extraction forms and trigger code generation.
"""

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status, Request, Query
from pydantic import BaseModel
from typing import List as _List, Dict as _Dict, Any as _Any


class SubfieldEditRequest(BaseModel):
    """Body for POST /forms/{id}/subfield-edit."""
    field_name: str
    subform_fields: _List[_Dict[str, _Any]]
from supabase import create_client
from uuid import UUID, uuid4
from typing import List, Optional
import copy
import json

import logging
from datetime import datetime, timezone
from app.dependencies import get_current_user
from app.config import settings
from app.models.schemas import FormCreate, FormUpdate, FormResponse, ReviewNote, RejectDecompositionRequest, FieldEditsRequest, AddFieldRequest
from app.models.enums import FormStatus, JobType, JobStatus
from app.rate_limits import (
    RATE_LIMIT_FORM_CREATE, RATE_LIMIT_FORM_LIST,
    RATE_LIMIT_FORM_MUTATE, RATE_LIMIT_FORM_REVIEW,
)
from app.services.project_access import check_project_access
from app.rate_limit import limiter
from app.services.activity_service import log_activity
from app.services.audit_service import log_audit, get_entity_history
from app.services.cache_service import cache_service
from dspy_components.runtime_builders import _parse_embedded_sections, _strip_embedded_sections

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


def _reconcile_pilot_status(form: dict) -> dict:
    """
    If a form's pilot is marked 'running' but its latest job has finished,
    flip the pilot status to 'reviewing'/'failed' and persist. Mirrors the
    lazy reconciliation in pilot.get_pilot so the forms-list card doesn't
    stay stuck on 'PILOT RUNNING' after the job completes.
    """
    metadata = form.get("metadata")
    if isinstance(metadata, str):
        try:
            metadata = json.loads(metadata)
        except (json.JSONDecodeError, TypeError):
            return form

    pilot = (metadata or {}).get("pilot") or {}
    if pilot.get("status") != "running" or not pilot.get("iterations"):
        return form

    latest = pilot["iterations"][-1]
    job_id = latest.get("job_id")
    if not job_id:
        return form

    job_result = supabase.table("jobs").select("status").eq("id", job_id).execute()
    if not job_result.data:
        return form

    job_status = job_result.data[0]["status"]
    if job_status not in ("completed", "failed"):
        return form

    if job_status == "completed":
        extraction_id = latest.get("extraction_id")
        if extraction_id:
            results_data = supabase.table("extraction_results")\
                .select("document_id, extracted_data")\
                .eq("extraction_id", extraction_id)\
                .execute()
            latest["results"] = {
                r["document_id"]: r["extracted_data"]
                for r in (results_data.data or [])
            }

    pilot["status"] = "reviewing" if job_status == "completed" else "failed"
    metadata["pilot"] = pilot

    supabase.table("forms")\
        .update({"metadata": json.dumps(metadata)})\
        .eq("id", form["id"])\
        .execute()

    project_id = form.get("project_id")
    if project_id:
        cache_service.delete_pattern(f"forms:project:{project_id}:*")
    cache_service.delete(f"forms:detail:{form['id']}")

    form["metadata"] = metadata
    return form


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

        # Create form record — status is DRAFT when saving without generation
        initial_status = FormStatus.DRAFT.value if form_data.save_as_draft else FormStatus.GENERATING.value
        form_record = {
            "project_id": str(form_data.project_id),
            "form_name": form_data.form_name,
            "form_description": form_data.form_description,
            "fields": json.dumps(fields_dict),
            "status": initial_status,
            "schema_name": None,
            "task_dir": None,
            "statistics": None,
            "error": None,
            "created_by_user_id": str(user_id),
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

        # Skip code generation when saving as draft
        if not form_data.save_as_draft:
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

                # Store current_job_id on the form so frontend can connect WebSocket on refresh
                existing_meta = form.get("metadata") or {}
                if isinstance(existing_meta, str):
                    try:
                        existing_meta = json.loads(existing_meta)
                    except (json.JSONDecodeError, TypeError):
                        existing_meta = {}
                existing_meta["current_job_id"] = str(job_id)
                supabase.table("forms").update({
                    "metadata": json.dumps(existing_meta)
                }).eq("id", form["id"]).execute()

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


class FormDuplicate(BaseModel):
    """Duplicate an existing active form into the same or another project."""
    form_name: str
    target_project_id: Optional[UUID] = None
    form_description: Optional[str] = None


@router.post("/{form_id}/duplicate", response_model=FormResponse, status_code=status.HTTP_201_CREATED)
@limiter.limit(RATE_LIMIT_FORM_CREATE)
async def duplicate_form(
    request: Request,
    form_id: UUID,
    payload: FormDuplicate,
    background_tasks: BackgroundTasks,
    user_id: UUID = Depends(get_current_user),
):
    """
    Duplicate an active form — copying its fields and compiled schema_def — into the
    same or another project WITHOUT re-running code generation.

    The copy lands as ACTIVE immediately: schema_def (the self-contained DSPy pipeline
    spec) is cloned and re-registered under a fresh schema_name, so the duplicate is
    ready to extract. Pilot/calibration metadata is reset so the copy starts a fresh
    completion ledger.
    """
    try:
        # Load source form
        result = supabase.table("forms").select("*").eq("id", str(form_id)).execute()
        if not result.data:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Form not found")
        source = result.data[0]

        # Caller must be able to create forms in the source project (and read it)
        await check_project_access(UUID(source["project_id"]), user_id, "can_create_forms")

        # Resolve target project; if different, caller must also be able to create there
        target_project_id = payload.target_project_id or UUID(source["project_id"])
        if str(target_project_id) != source["project_id"]:
            await check_project_access(target_project_id, user_id, "can_create_forms")

        # Only active forms with a compiled schema can be duplicated without codegen
        source_schema_def = source.get("schema_def")
        if isinstance(source_schema_def, str):
            try:
                source_schema_def = json.loads(source_schema_def)
            except (json.JSONDecodeError, TypeError):
                source_schema_def = None
        if source["status"] != FormStatus.ACTIVE.value or not source_schema_def:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Only active forms with a generated schema can be duplicated",
            )

        # Duplicate-name guard in the target project
        existing = supabase.table("forms")\
            .select("id")\
            .eq("project_id", str(target_project_id))\
            .eq("form_name", payload.form_name)\
            .execute()
        if existing.data:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f'A form named "{payload.form_name}" already exists in this project',
            )

        # Mint a fresh schema identifier so the copy gets its own registry entry
        # (schema_name is the PK of the schemas table — reusing it would clobber the source).
        new_task_name = f"task_{uuid4().hex[:12]}"
        new_schema_def = copy.deepcopy(source_schema_def)
        new_schema_def["schema_name"] = new_task_name
        new_schema_def["task_name"] = new_task_name

        # Copy field definitions verbatim
        source_fields = source.get("fields")
        if isinstance(source_fields, str):
            source_fields = json.loads(source_fields)

        # Carry over the decomposition so the Edit-form UI keeps its
        # stage → signature → field grouping. (Runtime field→signature assignment
        # lives in schema_def, which is copied above; the editor's grouped rail and
        # the "Add field → signature group" picker read metadata.decomposition.)
        # Everything else in metadata is per-instance and intentionally dropped
        # (thread_id, current_job_id, pilot calibration, revision history, …).
        source_meta = source.get("metadata")
        if isinstance(source_meta, str):
            try:
                source_meta = json.loads(source_meta)
            except (json.JSONDecodeError, TypeError):
                source_meta = {}
        source_meta = source_meta or {}
        clone_metadata = {}
        if source_meta.get("decomposition") is not None:
            clone_metadata["decomposition"] = source_meta["decomposition"]
        if source_meta.get("decomposition_summary") is not None:
            clone_metadata["decomposition_summary"] = source_meta["decomposition_summary"]

        form_record = {
            "project_id": str(target_project_id),
            "form_name": payload.form_name,
            "form_description": payload.form_description or source.get("form_description"),
            "fields": json.dumps(source_fields),
            "status": FormStatus.ACTIVE.value,
            "schema_name": new_task_name,
            "task_dir": None,
            "statistics": None,
            "error": None,
            "metadata": json.dumps(clone_metadata),  # decomposition kept for editor grouping; per-instance keys dropped
            "schema_def": new_schema_def,
            "created_by_user_id": str(user_id),
        }
        insert_result = supabase.table("forms").insert(form_record).execute()
        if not insert_result.data:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to create duplicated form record",
            )
        new_form = insert_result.data[0]

        # Register the cloned schema in the 3-tier cache (memory → Redis → Supabase)
        # under the new schema_name. Mirrors generation_tasks.resume_after_approval.
        from schemas.config import DynamicSchemaConfig
        from schemas.registry import register_schema
        signature_names = [s["class_name"] for s in new_schema_def["signatures"]]
        pipeline_stages = new_schema_def.get("pipeline_stages", [])
        register_schema(DynamicSchemaConfig(
            schema_name=new_task_name,
            task_name=new_task_name,
            module_path=f"dspy_components.tasks.{new_task_name}",
            signatures_path=f"dspy_components.tasks.{new_task_name}.signatures",
            signature_class_names=signature_names,
            pipeline_stages=pipeline_stages,
            project_id="",
            form_id=new_form["id"],
            form_name=payload.form_name,
            schema_def=new_schema_def,
        ))

        _invalidate_form_cache(project_id=str(target_project_id))

        background_tasks.add_task(
            log_activity,
            user_id=user_id,
            action_type="form_duplicate",
            action="Form Duplicated",
            description=f'Duplicated form "{source["form_name"]}" as "{payload.form_name}"',
            project_id=target_project_id,
            metadata={
                "source_form_id": str(form_id),
                "new_form_id": new_form["id"],
                "form_name": payload.form_name,
            },
        )

        # Parse JSON strings back for the response model
        new_form["fields"] = json.loads(new_form["fields"]) if isinstance(new_form.get("fields"), str) else new_form.get("fields")
        if isinstance(new_form.get("statistics"), str):
            new_form["statistics"] = json.loads(new_form["statistics"])

        return FormResponse(**new_form)

    except HTTPException:
        raise
    except Exception:
        logger.exception("Failed to duplicate form %s", form_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred",
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
            # Membership-only check — forms are extraction schemas (not docs).
            # Any project member should be able to read them; mutating endpoints keep can_create_forms.
            await check_project_access(project_id, user_id)

            # Check cache (only for non-search queries)
            if not search:
                cache_key = f"forms:project:{project_id}:list:{limit}:{offset}"
                cached = cache_service.get(cache_key)
                if cached is not None:
                    reconciled = [_reconcile_pilot_status(f) for f in cached]
                    return [FormResponse(**f) for f in reconciled]

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

        # Reconcile any pilots whose job finished but whose row still says 'running'
        forms = [_reconcile_pilot_status(f) for f in forms]

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
            # Still verify membership on cached results
            await check_project_access(UUID(cached["project_id"]), user_id)
            return FormResponse(**_reconcile_pilot_status(cached))

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

        # Membership-only check — forms aren't documents
        await check_project_access(UUID(form["project_id"]), user_id)

        # Parse JSON strings to dicts/lists
        if isinstance(form.get("fields"), str):
            form["fields"] = json.loads(form["fields"])
        if isinstance(form.get("statistics"), str):
            form["statistics"] = json.loads(form["statistics"])

        # Reconcile a stuck-'running' pilot if its job has finished
        form = _reconcile_pilot_status(form)

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

            # Store current_job_id on the form so frontend can connect WebSocket on refresh
            existing_meta = form.get("metadata") or {}
            if isinstance(existing_meta, str):
                try:
                    existing_meta = json.loads(existing_meta)
                except (json.JSONDecodeError, TypeError):
                    existing_meta = {}
            existing_meta["current_job_id"] = str(job_id)
            supabase.table("forms").update({
                "metadata": json.dumps(existing_meta)
            }).eq("id", form["id"]).execute()

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


def _soft_warn_field_edits(field: dict, edits: dict) -> list:
    """Return list of soft warnings about example value/type mismatches.
    Never blocks — reviewer judgment wins."""
    warnings = []
    ftype = (field.get("field_type") or "").lower()
    options = field.get("options") or []
    for ex in (edits.get("examples") or []):
        val = ex.get("value")
        if val is None:
            continue
        if ftype == "number":
            try:
                float(val) if not isinstance(val, bool) else None
            except (TypeError, ValueError):
                warnings.append({
                    "field": field.get("field_name"),
                    "level": "soft",
                    "message": f"example value {val!r} is not numeric but field_type='number'",
                })
        elif ftype == "boolean":
            if not isinstance(val, bool) and str(val).lower() not in ("true", "false", "yes", "no", "1", "0"):
                warnings.append({
                    "field": field.get("field_name"),
                    "level": "soft",
                    "message": f"example value {val!r} doesn't look boolean",
                })
        elif ftype == "select" and options:
            if isinstance(val, str) and val and val not in options:
                warnings.append({
                    "field": field.get("field_name"),
                    "level": "soft",
                    "message": f"example value {val!r} is not in options {options}",
                })
    return warnings


@router.get("/{form_id}/field-prompts")
async def get_field_prompts(
    request: Request,
    form_id: UUID,
    user_id: UUID = Depends(get_current_user),
):
    """Return parsed extraction hints / rules / examples / description for every
    output field in the compiled schema_def.

    Available whenever schema_def is present (active, regenerating, failed,
    awaiting_review). Returns 409 only for draft/generating forms that have
    no schema_def yet.
    """
    result = supabase.table("forms").select("*").eq("id", str(form_id)).execute()
    if not result.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Form not found")
    form = result.data[0]

    await _check_can_edit_field_prompts(form, user_id)

    # Block only statuses that never have a schema_def yet
    _NO_SCHEMA_STATUSES = {FormStatus.DRAFT.value, FormStatus.GENERATING.value}
    if form["status"] in _NO_SCHEMA_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Edit Instructions not available yet — form is still being generated "
                f"(current status: {form['status']}). "
                "Wait for generation to complete first."
            ),
        )

    schema_def = form.get("schema_def")
    if not schema_def:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Form has no compiled schema yet — approve the decomposition plan or regenerate.",
        )

    field_prompts: dict = {}
    for sig_def in schema_def.get("signatures", []):
        sig_class_name = sig_def["class_name"]
        for out_field in sig_def.get("output_fields", []):
            fname = out_field["name"]
            raw_desc = out_field.get("description", "") or ""
            hints = out_field.get("hints", []) or []
            rules = out_field.get("rules", []) or []
            examples = out_field.get("examples", []) or []
            options = out_field.get("options", []) or []

            # Phase A back-compat shim: parse embedded prose for old schema_defs.
            # New forms generated after the write-time fix (signature_gen.py Moves 1+4)
            # carry canonical structured arrays and don't need this parse.
            # TODO: delete after running zscripts/backfill_subfield_structure.py --apply
            if not (hints or rules or examples):
                parsed = _parse_embedded_sections(raw_desc)
                clean_desc = parsed["description"]
                if not hints:
                    hints = parsed["hints"]
                if not rules:
                    rules = parsed["rules"]
                if not examples:
                    examples = parsed["examples"]
            else:
                clean_desc = _strip_embedded_sections(
                    raw_desc,
                    {
                        "hints": bool(hints),
                        "rules": bool(rules),
                        "examples": bool(examples),
                        "options": bool(options),
                    },
                )

            # Phase A back-compat shim for subfield columns (same reason as above).
            # TODO: delete after running zscripts/backfill_subfield_structure.py --apply
            raw_subfields = out_field.get("subform_fields") or []
            parsed_subfields = []
            for col in raw_subfields:
                col_hints = list(col.get("hints") or [])
                col_rules = list(col.get("rules") or [])
                col_examples = list(col.get("examples") or [])
                col_desc = col.get("field_description") or ""
                if not (col_hints or col_rules or col_examples):
                    col_parsed = _parse_embedded_sections(col_desc)
                    col_desc = col_parsed["description"]
                    col_hints = col_parsed["hints"]
                    col_rules = col_parsed["rules"]
                    col_examples = col_parsed["examples"]
                parsed_subfields.append({
                    **col,
                    "field_description": col_desc,
                    "hints": col_hints,
                    "rules": col_rules,
                    "examples": col_examples,
                })

            field_prompts[fname] = {
                "signature": sig_class_name,
                "description": clean_desc,
                "hints": hints,
                "rules": rules,
                "examples": examples,
                "subform_fields": parsed_subfields,
            }

    return {
        "form_id": str(form_id),
        "field_prompts": field_prompts,
    }


@router.patch("/{form_id}/fields")
@limiter.limit(RATE_LIMIT_FORM_MUTATE)
async def update_field_edits(
    request: Request,
    form_id: UUID,
    body: FieldEditsRequest,
    user_id: UUID = Depends(get_current_user)
):
    """Save field-level edits (description/examples/hints/rules).

    Patches forms.fields JSONB and forms.schema_def atomically so the
    runtime builder sees the new values on the next pipeline build.
    Also mirrors to schemas table and invalidates L1/L2 cache.
    """
    try:
        result = supabase.table("forms")\
            .select("*")\
            .eq("id", str(form_id))\
            .execute()
        if not result.data:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Form not found")
        form = result.data[0]

        await _check_can_edit_field_prompts(form, user_id)

        _EDITABLE_STATUSES = {
            FormStatus.AWAITING_REVIEW.value,
            FormStatus.ACTIVE.value,
            FormStatus.FAILED.value,
        }
        if form["status"] not in _EDITABLE_STATUSES:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Field edits not allowed while form is {form['status']}.",
            )

        # ── C5: warn if extraction jobs are running for this project ─────────
        extraction_warning: Optional[str] = None
        try:
            running = (
                supabase.table("jobs")
                .select("id")
                .eq("project_id", form["project_id"])
                .eq("job_type", JobType.EXTRACTION.value)
                .in_("status", [JobStatus.PENDING.value, JobStatus.PROCESSING.value])
                .limit(1)
                .execute()
            )
            if running.data:
                extraction_warning = (
                    "An extraction job is currently running. These edits will not "
                    "apply to in-progress extractions — only to future runs."
                )
        except Exception:
            pass  # non-critical check, never block on it

        # Load current fields JSONB
        fields_raw = form.get("fields") or []
        if isinstance(fields_raw, str):
            fields_raw = json.loads(fields_raw)
        if not isinstance(fields_raw, list):
            raise HTTPException(status_code=500, detail="Form has malformed fields column")

        fields_by_name = {f.get("field_name"): f for f in fields_raw if isinstance(f, dict)}

        warnings = []
        updated_field_names = []

        for upd in body.field_updates:
            target = fields_by_name.get(upd.field_name)
            if target is None:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Unknown field: {upd.field_name}",
                )
            # M4: filter out structurally invalid examples before persisting
            if upd.examples is not None:
                upd.examples = [
                    e for e in upd.examples
                    if isinstance(e, dict) and e.get("value") is not None
                ]
            if upd.description is not None:
                target["field_description"] = upd.description
            if upd.examples is not None:
                target["examples"] = upd.examples
                if upd.examples:
                    first_val = upd.examples[0].get("value", "")
                    if first_val:
                        target["example"] = str(first_val)
            if upd.hints is not None:
                target["hints"] = upd.hints
                target["extraction_hints"] = upd.hints[0] if upd.hints else None
            if upd.rules is not None:
                target["rules"] = upd.rules
            if upd.options is not None:
                target["options"] = upd.options
            # Mirror table-extraction strategy onto fields JSONB so the form editor
            # roundtrips correctly. (schema_def is patched separately below.)
            if upd.extraction_strategy is not None:
                target["extraction_strategy"] = upd.extraction_strategy
            if upd.anchor_columns is not None:
                target["anchor_columns"] = upd.anchor_columns
            warnings.extend(_soft_warn_field_edits(target, {
                "examples": target.get("examples") or [],
            }))
            updated_field_names.append(upd.field_name)

        # ── Patch schema_def ─────────────────────────────────────────────────
        schema_name = form.get("schema_name")
        schema_def = form.get("schema_def")
        signatures_rewritten = False
        prev_schema_version: Optional[int] = None

        if schema_def:
            from core.generators.signature_splicer import update_schema_def_field
            from schemas.registry import invalidate_schema
            prev_schema_version = int(schema_def.get("version", 1))
            ftm = schema_def.get("field_to_signature_map", {})
            for upd in body.field_updates:
                raw_mapping = ftm.get(upd.field_name)

                # ── Description/hints/rules patch — requires field_to_signature_map ──
                if not raw_mapping:
                    logger.warning(
                        "[field_edits] No signature mapping for '%s' — "
                        "description/hints/rules NOT patched in schema_def", upd.field_name
                    )
                else:
                    # C2: field_to_signature_map may store a plain string class name
                    # or a FieldMapping dict — normalise to string.
                    if isinstance(raw_mapping, dict):
                        sig_class_name = (
                            raw_mapping.get("signature")
                            or raw_mapping.get("class_name")
                            or raw_mapping.get("sig_class_name")
                        )
                    else:
                        sig_class_name = raw_mapping
                    if not sig_class_name:
                        logger.warning("[field_edits] Could not resolve signature name for '%s'", upd.field_name)
                    else:
                        try:
                            schema_def = update_schema_def_field(
                                schema_def,
                                signature_name=sig_class_name,
                                field_name=upd.field_name,
                                description=upd.description,
                                hints=upd.hints,
                                rules=upd.rules,
                                examples=upd.examples,
                                options=upd.options,
                            )
                            signatures_rewritten = True
                        except ValueError as e:
                            logger.warning("[field_edits] schema_def patch skipped for '%s': %s", upd.field_name, e)

                # ── Strategy patch — matches by field name, no map needed ──────────
                # Runs even when field_to_signature_map has no entry (e.g. table fields).
                if upd.extraction_strategy is not None or upd.anchor_columns is not None:
                    for sig in (schema_def.get("signatures") or []):
                        for of in (sig.get("output_fields") or []):
                            if of.get("name") == upd.field_name:
                                if upd.extraction_strategy is not None:
                                    of["extraction_strategy"] = upd.extraction_strategy
                                if upd.anchor_columns is not None:
                                    # Trim anchor names so trailing-whitespace typos
                                    # ('intervention   ') don't silently bypass the
                                    # subfield-membership check.
                                    trimmed_anchors = [
                                        (a or "").strip() for a in upd.anchor_columns
                                        if (a or "").strip()
                                    ]
                                    known = {
                                        sf.get("field_name")
                                        for sf in (of.get("subform_fields") or [])
                                    }
                                    missing = [a for a in trimmed_anchors if a not in known]
                                    if missing:
                                        raise HTTPException(
                                            status_code=status.HTTP_400_BAD_REQUEST,
                                            detail=(
                                                f"Anchor columns not present in '{upd.field_name}' subfields: "
                                                f"{missing}. Add the subfield first, then save the strategy."
                                            ),
                                        )
                                    of["anchor_columns"] = trimmed_anchors
                                    anchor_set = set(trimmed_anchors)
                                    for sf in (of.get("subform_fields") or []):
                                        sf["extraction_role"] = (
                                            "anchor" if sf.get("field_name") in anchor_set else "value"
                                        )
                                signatures_rewritten = True
                                logger.info(
                                    "[field_edits] strategy patched for '%s': strategy=%s anchors=%s",
                                    upd.field_name, upd.extraction_strategy, of.get("anchor_columns"),
                                )
                                break
        else:
            logger.warning("[field_edits] Form %s has no schema_def — fields JSONB updated only", form_id)

        # ── M8: metadata as native dict (not json.dumps) ─────────────────────
        existing_meta = form.get("metadata") or {}
        if isinstance(existing_meta, str):
            try:
                existing_meta = json.loads(existing_meta)
            except (json.JSONDecodeError, TypeError):
                existing_meta = {}
        existing_meta["field_edits_version"] = int(existing_meta.get("field_edits_version") or 0) + 1
        existing_meta["field_edits_updated_at"] = datetime.now(timezone.utc).isoformat()

        # ── C3+C6: single atomic update (schema_def + fields + metadata) ─────
        combined_payload: dict = {
            "fields": fields_raw,
            "metadata": existing_meta,
        }
        if signatures_rewritten:
            combined_payload["schema_def"] = schema_def
            # OCC: reject if another editor bumped schema_def version since our read
            occ_result = (
                supabase.table("forms")
                .update(combined_payload)
                .eq("id", str(form_id))
                .eq("schema_def->>version", str(prev_schema_version))
                .execute()
            )
            if not occ_result.data:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="schema_def was modified by another editor — reload the form and retry.",
                )
            logger.info(
                "[field_edits] schema_def patched for %s (v%s→v%s, %d field(s))",
                schema_name, prev_schema_version, schema_def.get("version"), len(updated_field_names),
            )
        else:
            supabase.table("forms").update(combined_payload).eq("id", str(form_id)).execute()

        # ── C1: always invalidate schema cache after any successful write ─────
        if schema_name:
            from schemas.registry import invalidate_schema
            # C4: mirror to schemas table — log at ERROR if it fails so drift is visible
            if signatures_rewritten:
                try:
                    supabase.table("schemas").update(
                        {"schema_def": schema_def}
                    ).eq("schema_name", schema_name).execute()
                except Exception as mirror_err:
                    logger.error(
                        "[field_edits] schemas table mirror FAILED for %s — "
                        "L3 cache is now stale until next registry refresh: %s",
                        schema_name, mirror_err,
                    )
            invalidate_schema(schema_name)

        _invalidate_form_cache(project_id=form["project_id"], form_id=str(form_id))

        resp: dict = {
            "form_id": str(form_id),
            "updated_fields": updated_field_names,
            "warnings": warnings,
            "field_edits_version": existing_meta["field_edits_version"],
            "signatures_rewritten": signatures_rewritten,
        }
        if extraction_warning:
            resp["extraction_warning"] = extraction_warning
        return resp

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to update field edits for form %s", form_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to save field edits — please try again.",
        )


@router.post("/{form_id}/subfield-edit")
@limiter.limit(RATE_LIMIT_FORM_MUTATE)
async def update_subfield_edit(
    request: Request,
    form_id: UUID,
    body: SubfieldEditRequest,
    user_id: UUID = Depends(get_current_user)
):
    """Replace the subform_fields (column definitions) for one output field.

    Patches schema_def and forms.fields atomically. No codegen triggered.
    Column structure changes are in-place edits — no LangGraph / Celery regen.
    Cache-invalidates on success.
    """
    try:
        result = supabase.table("forms").select("*").eq("id", str(form_id)).execute()
        if not result.data:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Form not found")
        form = result.data[0]

        await _check_can_edit_field_prompts(form, user_id)

        _EDITABLE_STATUSES = {
            FormStatus.AWAITING_REVIEW.value,
            FormStatus.ACTIVE.value,
            FormStatus.FAILED.value,
        }
        if form["status"] not in _EDITABLE_STATUSES:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Subfield edits not allowed while form is {form['status']}.",
            )

        # ── Update forms.fields JSONB ─────────────────────────────────────────
        fields_raw = form.get("fields") or []
        if isinstance(fields_raw, str):
            fields_raw = json.loads(fields_raw)
        if not isinstance(fields_raw, list):
            raise HTTPException(status_code=500, detail="Form has malformed fields column")

        fields_by_name = {f.get("field_name"): f for f in fields_raw if isinstance(f, dict)}
        target_field = fields_by_name.get(body.field_name)
        if target_field is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Unknown field: {body.field_name}",
            )

        # Drop blank/whitespace-only subfield rows and trim names so the UI's
        # "+ Add subfield" placeholder can't pollute the persisted schema.
        filtered_subfields: list = []
        for sf in body.subform_fields:
            if not isinstance(sf, dict):
                continue
            name = (sf.get("field_name") or "").strip()
            if not name:
                continue
            sf["field_name"] = name
            filtered_subfields.append(sf)
        if len(filtered_subfields) != len(body.subform_fields):
            logger.info(
                "[subfield_edit] Dropped %d blank subfield row(s) for form=%s field=%s",
                len(body.subform_fields) - len(filtered_subfields), form_id, body.field_name,
            )

        target_field["subform_fields"] = filtered_subfields

        # ── Patch schema_def ─────────────────────────────────────────────────
        schema_name = form.get("schema_name")
        schema_def = form.get("schema_def")
        prev_version: Optional[int] = None
        schema_patched = False

        if schema_def:
            from core.generators.signature_splicer import update_schema_def_subfield
            ftm = schema_def.get("field_to_signature_map", {})
            raw_mapping = ftm.get(body.field_name)
            if raw_mapping:
                sig_class_name = (
                    raw_mapping if isinstance(raw_mapping, str)
                    else (
                        raw_mapping.get("signature")
                        or raw_mapping.get("class_name")
                        or raw_mapping.get("sig_class_name")
                    )
                )
                if sig_class_name:
                    try:
                        prev_version = int(schema_def.get("version", 1))
                        schema_def = update_schema_def_subfield(
                            schema_def,
                            signature_name=sig_class_name,
                            field_name=body.field_name,
                            subform_fields=filtered_subfields,
                        )
                        schema_patched = True
                    except ValueError as e:
                        logger.warning(
                            "[subfield_edit] schema_def patch skipped for '%s': %s",
                            body.field_name, e,
                        )
            else:
                logger.warning(
                    "[subfield_edit] No signature mapping for '%s' — "
                    "fields JSONB updated but schema_def NOT patched", body.field_name
                )
        else:
            logger.warning(
                "[subfield_edit] Form %s has no schema_def — fields JSONB updated only", form_id
            )

        # ── Bump metadata version ─────────────────────────────────────────────
        existing_meta = form.get("metadata") or {}
        if isinstance(existing_meta, str):
            try:
                existing_meta = json.loads(existing_meta)
            except (json.JSONDecodeError, TypeError):
                existing_meta = {}
        existing_meta["field_edits_version"] = int(existing_meta.get("field_edits_version") or 0) + 1
        existing_meta["field_edits_updated_at"] = datetime.now(timezone.utc).isoformat()

        combined_payload: dict = {"fields": fields_raw, "metadata": existing_meta}

        if schema_patched:
            combined_payload["schema_def"] = schema_def
            occ_result = (
                supabase.table("forms")
                .update(combined_payload)
                .eq("id", str(form_id))
                .eq("schema_def->>version", str(prev_version))
                .execute()
            )
            if not occ_result.data:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="schema_def was modified by another editor — reload the form and retry.",
                )
            logger.info(
                "[subfield_edit] schema_def patched for %s (v%s→v%s, field=%s)",
                schema_name, prev_version, schema_def.get("version"), body.field_name,
            )
        else:
            supabase.table("forms").update(combined_payload).eq("id", str(form_id)).execute()

        # ── Mirror + invalidate ───────────────────────────────────────────────
        if schema_name and schema_patched:
            try:
                supabase.table("schemas").update(
                    {"schema_def": schema_def}
                ).eq("schema_name", schema_name).execute()
            except Exception as mirror_err:
                logger.error(
                    "[subfield_edit] schemas mirror FAILED for %s: %s", schema_name, mirror_err
                )
            from schemas.registry import invalidate_schema
            invalidate_schema(schema_name)

        _invalidate_form_cache(project_id=form["project_id"], form_id=str(form_id))

        return {
            "form_id": str(form_id),
            "field_name": body.field_name,
            "schema_def_version": schema_def.get("version") if schema_def else None,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to update subfield edit for form %s", form_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to save subfield edits — please try again.",
        )


async def _check_can_review_form(form: dict, user_id: UUID) -> None:
    """Decomposition approve/reject: creator or project admin/owner only.

    Falls back to can_manage_members for legacy forms with NULL creator.
    """
    project_id = UUID(form["project_id"])
    creator = form.get("created_by_user_id")
    if creator and UUID(creator) == user_id:
        await check_project_access(project_id, user_id, "can_create_forms")
        return
    await check_project_access(project_id, user_id, "can_manage_members")


async def _check_can_edit_field_prompts(form: dict, user_id: UUID) -> None:
    """Edit Instructions (field prompts): any member with can_create_forms."""
    project_id = UUID(form["project_id"])
    await check_project_access(project_id, user_id, "can_create_forms")


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

        await _check_can_review_form(form, user_id)

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

        # CAS update — only succeeds if form is still awaiting_review. Without
        # this guard, a concurrent approve+reject (or double-click) both pass
        # the read-check above and both dispatch codegen jobs racing to write
        # signatures.py / modules.py.
        cas = supabase.table("forms").update({
            "status": FormStatus.GENERATING.value
        }).eq("id", str(form_id))\
          .eq("status", FormStatus.AWAITING_REVIEW.value)\
          .execute()

        if not cas.data:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Form status changed concurrently — refresh and retry."
            )

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

        # Store current_job_id on form for frontend WebSocket connection
        existing_meta = metadata if isinstance(metadata, dict) else {}
        existing_meta["current_job_id"] = str(job_id)
        supabase.table("forms").update({
            "metadata": json.dumps(existing_meta)
        }).eq("id", str(form_id)).execute()

        # Trigger Celery task to resume workflow
        try:
            celery_task = resume_after_approval.delay(
                form_id=str(form_id),
                job_id=str(job_id),
                thread_id=thread_id,
                task_name=task_name
            )
        except Exception as dispatch_err:
            logger.exception("Celery broker unreachable when dispatching resume_after_approval for form %s", form_id)
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Job dispatch failed — the generation queue is temporarily unavailable. Please retry in a moment."
            ) from dispatch_err

        # Update job with Celery task ID
        supabase.table("jobs").update({
            "celery_task_id": celery_task.id
        }).eq("id", str(job_id)).execute()

        # Write review audit columns
        supabase.table("forms").update({
            "reviewed_by_user_id": str(user_id),
            "reviewed_at": datetime.now(timezone.utc).isoformat(),
            "review_decision": "approved",
        }).eq("id", str(form_id)).execute()

        # Write audit trail entry
        background_tasks.add_task(
            log_audit,
            user_id=user_id,
            entity_type="form",
            entity_id=form_id,
            action="approved",
            project_id=UUID(form["project_id"]),
            metadata={
                "attempt": metadata.get("decomposition", {}).get("attempt", 1) if isinstance(metadata, dict) else 1,
                "decomposition_snapshot": metadata.get("decomposition") if isinstance(metadata, dict) else None,
            }
        )

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
            detail=f"An unexpected error occurred ({type(e).__name__}). Check server logs for details."
        )


def _render_review_feedback(notes: list, general_feedback: str = "") -> str:
    """Render structured review notes to a formatted feedback string for the LLM."""
    parts = []
    if general_feedback:
        parts.append(f"[GENERAL]\n{general_feedback}")
    for note in notes:
        t, ref, comment = note.target_type, note.target_ref, note.comment
        if t == "field":
            parts.append(f"[ON FIELD: {ref}]\n{comment}")
        elif t == "group":
            parts.append(f"[ON GROUP: {ref}]\n{comment}")
        elif t == "stage":
            parts.append(f"[ON STAGE: {ref}]\n{comment}")
        elif t == "pipeline":
            parts.append(f"[ON PIPELINE]\n{comment}")
    if not parts:
        return general_feedback or ""
    return "REVIEWER FEEDBACK (address each item):\n\n" + "\n\n".join(parts)


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

        await _check_can_review_form(form, user_id)

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

        # Validate note target refs and accepted_refs before mutating anything
        form_fields_raw = form.get("fields") or []
        if isinstance(form_fields_raw, str):
            form_fields_raw = json.loads(form_fields_raw)
        form_field_names = {f["field_name"] for f in form_fields_raw}
        decomp = metadata.get("decomposition", {}) if isinstance(metadata, dict) else {}
        sig_names = {s["name"] for s in (decomp.get("signatures") or [])}
        pipeline_stages = {str(p["stage"]) for p in (decomp.get("pipeline") or [])}

        if body.notes:
            for note in body.notes:
                if note.target_type == "field" and note.target_ref not in form_field_names:
                    raise HTTPException(status_code=400, detail=f"Unknown field: {note.target_ref}")
                elif note.target_type == "group" and note.target_ref not in sig_names:
                    raise HTTPException(status_code=400, detail=f"Unknown group: {note.target_ref}")
                elif note.target_type == "stage" and note.target_ref not in pipeline_stages:
                    raise HTTPException(status_code=400, detail=f"Unknown stage: {note.target_ref}")
                elif note.target_type == "pipeline" and note.target_ref != "*":
                    raise HTTPException(status_code=400, detail="Pipeline target_ref must be '*'")

        if body.accepted_refs:
            for ref in body.accepted_refs:
                if ref not in sig_names:
                    raise HTTPException(status_code=400, detail=f"Unknown signature in accepted_refs: {ref}")

        # Render notes + feedback to structured string for the LLM
        rendered_feedback = _render_review_feedback(body.notes, body.feedback or "")

        # CAS update — only succeeds if form is still awaiting_review. Without
        # this guard, concurrent approve+reject both pass the read-check and
        # both dispatch codegen jobs racing to write the same task dir.
        cas = supabase.table("forms").update({
            "status": FormStatus.REGENERATING.value,
            "reviewed_by_user_id": str(user_id),
            "reviewed_at": datetime.now(timezone.utc).isoformat(),
            "review_decision": "rejected",
            "error": None,
        }).eq("id", str(form_id))\
          .eq("status", FormStatus.AWAITING_REVIEW.value)\
          .execute()

        if not cas.data:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Form status changed concurrently — refresh and retry."
            )

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
                "feedback": rendered_feedback
            }
        }
        job_result = supabase.table("jobs").insert(job_data).execute()
        job_id = job_result.data[0]["id"]

        # Persist notes + current_job_id in form metadata
        existing_meta = metadata if isinstance(metadata, dict) else {}
        existing_meta["current_job_id"] = str(job_id)
        existing_meta["review_notes"] = existing_meta.get("review_notes", []) + [
            n.model_dump() for n in body.notes
        ]
        supabase.table("forms").update({
            "metadata": json.dumps(existing_meta)
        }).eq("id", str(form_id)).execute()

        # Trigger Celery task to resume workflow with rendered feedback
        celery_task = resume_after_rejection.delay(
            form_id=str(form_id),
            job_id=str(job_id),
            thread_id=thread_id,
            task_name=task_name,
            feedback=rendered_feedback,
            accepted_refs=body.accepted_refs or [],
        )

        # Update job with Celery task ID
        supabase.table("jobs").update({
            "celery_task_id": celery_task.id
        }).eq("id", str(job_id)).execute()

        # Write audit trail
        background_tasks.add_task(
            log_audit,
            user_id=user_id,
            entity_type="form",
            entity_id=form_id,
            action="rejected",
            project_id=UUID(form["project_id"]),
            metadata={
                "feedback": body.feedback,
                "notes": [n.model_dump() for n in body.notes],
                "decomposition_snapshot": existing_meta.get("decomposition"),
            }
        )

        background_tasks.add_task(
            log_activity,
            user_id=user_id,
            action_type="code_generation",
            action="Decomposition Rejected",
            description=f"Rejected decomposition for form: {form.get('form_name', str(form_id))}",
            project_id=UUID(form["project_id"]),
            metadata={"form_id": str(form_id), "form_name": form.get("form_name"), "feedback": rendered_feedback},
        )

        return {
            "message": "Feedback received, regenerating decomposition",
            "form_id": str(form_id),
            "job_id": str(job_id),
            "feedback": rendered_feedback,
            "status": "regenerating"
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to reject decomposition for form %s", form_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"{type(e).__name__}: {str(e)[:200]}"
        )


@router.post("/{form_id}/dismiss-issue")
@limiter.limit(RATE_LIMIT_FORM_REVIEW)
async def dismiss_issue(
    request: Request,
    form_id: UUID,
    body: dict,
    background_tasks: BackgroundTasks,
    user_id: UUID = Depends(get_current_user)
):
    """Dismiss a risk signal issue so it is filtered from the review panel."""
    try:
        issue_id = (body.get("issue_id") or "").strip()
        if not issue_id:
            raise HTTPException(status_code=400, detail="issue_id is required")

        result = supabase.table("forms").select("*").eq("id", str(form_id)).execute()
        if not result.data:
            raise HTTPException(status_code=404, detail="Form not found")
        form = result.data[0]

        await check_project_access(UUID(form["project_id"]), user_id, "can_create_forms")

        metadata_raw = form.get("metadata")
        metadata = json.loads(metadata_raw) if isinstance(metadata_raw, str) else (metadata_raw or {})
        dismissed = list(metadata.get("dismissed_issue_ids") or [])
        if issue_id not in dismissed:
            dismissed.append(issue_id)
        metadata["dismissed_issue_ids"] = dismissed

        supabase.table("forms").update({
            "metadata": json.dumps(metadata)
        }).eq("id", str(form_id)).execute()

        background_tasks.add_task(
            log_audit,
            user_id=user_id,
            entity_type="form",
            entity_id=form_id,
            action="dismissed_issue",
            project_id=UUID(form["project_id"]),
            metadata={"issue_id": issue_id},
        )

        return {"form_id": str(form_id), "dismissed_issue_ids": dismissed}

    except HTTPException:
        raise
    except Exception:
        logger.exception("Failed to dismiss issue for form %s", form_id)
        raise HTTPException(status_code=500, detail="An unexpected error occurred")


@router.get("/{form_id}/review-history")
@limiter.limit(RATE_LIMIT_FORM_LIST)
async def get_review_history(
    request: Request,
    form_id: UUID,
    user_id: UUID = Depends(get_current_user)
):
    """Get the audit trail review history for a form."""
    try:
        result = supabase.table("forms")\
            .select("project_id")\
            .eq("id", str(form_id))\
            .execute()
        if not result.data:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Form not found")

        await check_project_access(UUID(result.data[0]["project_id"]), user_id)

        history = await get_entity_history(entity_type="form", entity_id=form_id)
        return {"form_id": str(form_id), "history": history}

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to get review history for form %s", form_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred"
        )


# ── Field dependency check ────────────────────────────────────────────────────

@router.get("/{form_id}/fields/{field_name}/dependencies")
async def get_field_dependencies(
    form_id: UUID,
    field_name: str,
    user_id: UUID = Depends(get_current_user),
):
    """Return which signatures consume field_name as an input dependency."""
    result = supabase.table("forms").select("schema_def, project_id").eq("id", str(form_id)).execute()
    if not result.data:
        raise HTTPException(status_code=404, detail="Form not found")
    form = result.data[0]
    await check_project_access(UUID(form["project_id"]), user_id, "can_create_forms")

    schema_def = form.get("schema_def") or {}
    from core.generators.signature_splicer import signatures_consuming_field
    consumers = signatures_consuming_field(schema_def, field_name)
    return {"field_name": field_name, "consuming_signatures": consumers}


# ── Add field to active form ──────────────────────────────────────────────────

@router.post("/{form_id}/fields")
@limiter.limit(RATE_LIMIT_FORM_MUTATE)
async def add_field_to_form(
    request: Request,
    form_id: UUID,
    body: AddFieldRequest,
    user_id: UUID = Depends(get_current_user),
):
    """Add a new field to an active form's signature with LLM-generated calibration.

    Splices directly into schema_def JSON — no LangGraph re-run.
    Past extraction_results are unaffected; new field will be NULL for old docs.
    """
    import asyncio
    try:
        result = supabase.table("forms").select("*").eq("id", str(form_id)).execute()
        if not result.data:
            raise HTTPException(status_code=404, detail="Form not found")
        form = result.data[0]

        await _check_can_edit_field_prompts(form, user_id)

        if form["status"] != "active":
            raise HTTPException(status_code=400, detail="Field add is only supported on active forms.")

        schema_def = form.get("schema_def")
        if not schema_def:
            raise HTTPException(status_code=400, detail="Form has no compiled schema_def — regenerate first.")

        # Validate target signature exists
        sig_names = [s.get("class_name") for s in schema_def.get("signatures", [])]
        if body.target_signature_class not in sig_names:
            raise HTTPException(status_code=400, detail=f"Signature '{body.target_signature_class}' not found.")

        # Validate field_name unique
        if body.field_name in schema_def.get("field_to_signature_map", {}):
            raise HTTPException(status_code=400, detail=f"Field '{body.field_name}' already exists.")

        # Parallel-stage uniqueness: if target sig is in a parallel stage, no sibling can share the name
        for stage in schema_def.get("pipeline_stages", []):
            if body.target_signature_class in stage.get("signatures", []) and len(stage["signatures"]) > 1:
                for other_sig_name in stage["signatures"]:
                    if other_sig_name == body.target_signature_class:
                        continue
                    for other_sig in schema_def.get("signatures", []):
                        if other_sig.get("class_name") == other_sig_name:
                            sibling_fields = {f.get("name") for f in other_sig.get("output_fields", [])}
                            if body.field_name in sibling_fields:
                                raise HTTPException(
                                    status_code=400,
                                    detail=f"Field '{body.field_name}' already produced by sibling signature '{other_sig_name}' in the same parallel stage.",
                                )

        # Find target sig metadata for context
        target_sig_meta = next((s for s in schema_def.get("signatures", []) if s.get("class_name") == body.target_signature_class), {})

        # LLM call — run sync in executor to avoid blocking event loop
        from core.generators.signature_gen import SignatureGenerator
        sig_gen = SignatureGenerator()

        target_sig_payload = {
            "class_name": body.target_signature_class,
            "docstring": target_sig_meta.get("docstring", ""),
            "existing_fields": [
                {"name": f.get("name"), "description": f.get("description", "")}
                for f in target_sig_meta.get("output_fields", [])
            ],
        }
        new_field_payload = {
            "field_name": body.field_name,
            "field_type": body.field_type,
            "description": body.description,
            "examples": body.examples or [],
            "options": body.options or [],
        }

        def _run_llm():
            return sig_gen.enrich_new_field(target_sig_payload, new_field_payload)

        loop = asyncio.get_event_loop()
        gen_result = await loop.run_in_executor(None, _run_llm)

        if not gen_result.get("is_valid") or not gen_result.get("spec"):
            logger.error("[add_field] LLM autofill failed: %s", gen_result.get("errors"))
            hints, rules, llm_examples = [], [], []
        else:
            spec = gen_result["spec"]
            hints = spec.get("hints") or []
            rules = spec.get("rules") or []
            llm_examples = spec.get("examples") or []

        # User-wins merge: description is always from user; user examples prepend LLM examples
        description = body.description
        examples = (body.examples or []) + llm_examples

        # All output fields use the source-grounded {value, source_text}
        # envelope, matching generated fields — the semantic type (text/number/
        # boolean/select) lives inside the "value" key. A bare str/float/bool
        # annotation would drop the grounding block from the prompt and make a
        # legitimate "NR" answer fail DSPy's type validation.
        dspy_type = "Dict[str, Any]"
        source_grounded = True

        # Splice schema_def
        from core.generators.signature_splicer import add_schema_def_field
        prev_version = int(schema_def.get("version", 1))
        schema_def = add_schema_def_field(
            schema_def,
            target_signature_class=body.target_signature_class,
            field_name=body.field_name,
            field_type_str=dspy_type,
            description=description,
            hints=hints,
            rules=rules,
            examples=examples,
            options=body.options or [],
            source_grounded=source_grounded,
        )

        # Build canonical form field entry
        new_form_field: dict = {
            "field_name": body.field_name,
            "display_name": body.display_name or body.field_name.replace("_", " ").title(),
            "field_type": body.field_type,
            "field_description": description,
            "field_control_type": "text_input",
            "multiple": body.multiple or False,
        }
        if body.options:
            new_form_field["options"] = body.options
        if hints:
            new_form_field["hints"] = hints
            new_form_field["extraction_hints"] = hints[0]
        if rules:
            new_form_field["rules"] = rules
        if examples:
            new_form_field["examples"] = examples

        fields_raw = form.get("fields") or []
        if isinstance(fields_raw, str):
            fields_raw = json.loads(fields_raw)
        fields_raw = list(fields_raw)
        fields_raw.append(new_form_field)

        # Metadata bump
        existing_meta = form.get("metadata") or {}
        if isinstance(existing_meta, str):
            try:
                existing_meta = json.loads(existing_meta)
            except Exception:
                existing_meta = {}
        existing_meta["field_edits_version"] = int(existing_meta.get("field_edits_version") or 0) + 1
        existing_meta["field_edits_updated_at"] = datetime.now(timezone.utc).isoformat()

        # Sync metadata.decomposition so the Edit Form dialog rail stays in sync
        from core.generators.signature_splicer import add_decomposition_field
        decomposition = existing_meta.get("decomposition")
        if decomposition:
            existing_meta["decomposition"] = add_decomposition_field(
                decomposition, body.target_signature_class, body.field_name
            )

        # OCC update
        occ_result = (
            supabase.table("forms")
            .update({"schema_def": schema_def, "fields": fields_raw, "metadata": existing_meta})
            .eq("id", str(form_id))
            .eq("schema_def->>version", str(prev_version))
            .execute()
        )
        if not occ_result.data:
            raise HTTPException(
                status_code=409,
                detail="schema_def was modified by another editor — reload the form and retry.",
            )

        # Mirror to schemas table
        schema_name = form.get("schema_name")
        if schema_name:
            try:
                supabase.table("schemas").update({"schema_def": schema_def}).eq("schema_name", schema_name).execute()
            except Exception as e:
                logger.error("[add_field] schemas mirror failed for %s: %s", schema_name, e)
            from schemas.registry import invalidate_schema
            invalidate_schema(schema_name)

        _invalidate_form_cache(project_id=form["project_id"], form_id=str(form_id))

        return {
            "form_id": str(form_id),
            "field_name": body.field_name,
            "calibration": {
                "description": description,
                "hints": hints,
                "rules": rules,
                "examples": examples,
            },
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("[add_field] Failed to add field to form %s", form_id)
        raise HTTPException(status_code=500, detail="Failed to add field — please try again.")


# ── Remove field from active form ─────────────────────────────────────────────

@router.delete("/{form_id}/fields/{field_name}")
@limiter.limit(RATE_LIMIT_FORM_MUTATE)
async def remove_field_from_form(
    request: Request,
    form_id: UUID,
    field_name: str,
    user_id: UUID = Depends(get_current_user),
):
    """Remove a field from an active form's schema_def.

    Blocked if any downstream signature depends on this field.
    Past extraction_results rows are NOT deleted — orphaned column values
    are hidden by the UI's current-fields filter.
    """
    try:
        result = supabase.table("forms").select("*").eq("id", str(form_id)).execute()
        if not result.data:
            raise HTTPException(status_code=404, detail="Form not found")
        form = result.data[0]

        await _check_can_edit_field_prompts(form, user_id)

        if form["status"] != "active":
            raise HTTPException(status_code=400, detail="Field remove is only supported on active forms.")

        schema_def = form.get("schema_def")
        if not schema_def:
            raise HTTPException(status_code=400, detail="Form has no compiled schema_def.")

        # Dependency check (server-side enforcement)
        from core.generators.signature_splicer import signatures_consuming_field, remove_schema_def_field
        consumers = signatures_consuming_field(schema_def, field_name)
        if consumers:
            raise HTTPException(
                status_code=409,
                detail=f"Field '{field_name}' is consumed by: {', '.join(consumers)}. Remove the dependency first.",
            )

        prev_version = int(schema_def.get("version", 1))
        schema_def = remove_schema_def_field(schema_def, field_name=field_name)

        # Remove from forms.fields
        fields_raw = form.get("fields") or []
        if isinstance(fields_raw, str):
            fields_raw = json.loads(fields_raw)
        fields_raw = [f for f in fields_raw if isinstance(f, dict) and f.get("field_name") != field_name]

        existing_meta = form.get("metadata") or {}
        if isinstance(existing_meta, str):
            try:
                existing_meta = json.loads(existing_meta)
            except Exception:
                existing_meta = {}
        existing_meta["field_edits_version"] = int(existing_meta.get("field_edits_version") or 0) + 1
        existing_meta["field_edits_updated_at"] = datetime.now(timezone.utc).isoformat()

        # Sync metadata.decomposition so the Edit Form dialog rail stays in sync
        from core.generators.signature_splicer import remove_decomposition_field
        decomposition = existing_meta.get("decomposition")
        if decomposition:
            existing_meta["decomposition"] = remove_decomposition_field(decomposition, field_name)

        occ_result = (
            supabase.table("forms")
            .update({"schema_def": schema_def, "fields": fields_raw, "metadata": existing_meta})
            .eq("id", str(form_id))
            .eq("schema_def->>version", str(prev_version))
            .execute()
        )
        if not occ_result.data:
            raise HTTPException(
                status_code=409,
                detail="schema_def was modified by another editor — reload the form and retry.",
            )

        schema_name = form.get("schema_name")
        if schema_name:
            try:
                supabase.table("schemas").update({"schema_def": schema_def}).eq("schema_name", schema_name).execute()
            except Exception as e:
                logger.error("[remove_field] schemas mirror failed for %s: %s", schema_name, e)
            from schemas.registry import invalidate_schema
            invalidate_schema(schema_name)

        _invalidate_form_cache(project_id=form["project_id"], form_id=str(form_id))

        return {"form_id": str(form_id), "field_name": field_name}

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("[remove_field] Failed to remove field %s from form %s", field_name, form_id)
        raise HTTPException(status_code=500, detail="Failed to remove field — please try again.")
