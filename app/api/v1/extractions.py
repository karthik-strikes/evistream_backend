"""
Extraction job endpoints - Create and manage extraction jobs.
"""

import json
import logging

from fastapi import APIRouter, BackgroundTasks, Body, Depends, HTTPException, status, Query, Request
from supabase import create_client
from uuid import UUID
from typing import List, Optional

from utils.table_schema import AGENTIC, field_strategy, resolve_strategy
from app.dependencies import get_current_user
from app.config import settings
from app.models.schemas import ExtractionCreate, ExtractionResponse
from app.models.enums import JobType, JobStatus
from app.rate_limits import RATE_LIMIT_EXTRACTION_CREATE, RATE_LIMIT_EXTRACTION_LIST
from app.services.project_access import check_project_access
from app.services.settings_service import get_user_settings
from app.rate_limit import limiter
from app.services.activity_service import log_activity
from config.models import AVAILABLE_MODEL_IDS, DEFAULT_MODEL


logger = logging.getLogger(__name__)

from utils import absence

router = APIRouter()


# Sourced from utils/absence.py so this can no longer drift from the
# extraction path's own recognizer (it is a superset of the old literal set).
_NR_LIKE = absence.EMPTY_DISPLAY_TOKENS


# Shared with the dashboard endpoint and mirrored in frontend/lib/absence.ts.
_field_is_empty = absence.field_is_empty


_field_is_not_applicable = absence.field_is_not_applicable


def _flagged_more_than_half_empty(extracted_data) -> bool:
    """Flag a study whose latest result has more than half its fields empty —
    a signal the paper was likely under-extracted and worth re-running."""
    if not isinstance(extracted_data, dict):
        return False
    field_keys = [k for k in extracted_data.keys() if not k.startswith("_")]
    if not field_keys:
        return True
    considered = [k for k in field_keys if not _field_is_not_applicable(extracted_data[k])]
    if not considered:
        # Every field is inapplicable to this design — nothing was missed.
        return False
    empty = sum(1 for k in considered if _field_is_empty(extracted_data[k]))
    return empty * 2 > len(considered)


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
        if doc_ids:
            effective_count = len(doc_ids)
        else:
            # "Run all" — count completed documents in the project
            count_result = supabase.table("documents")\
                .select("id", count="exact")\
                .eq("project_id", str(extraction_data.project_id))\
                .eq("processing_status", "completed")\
                .execute()
            effective_count = count_result.count or 0
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

        # Resolve the user's preferred extraction model (Beta — set in Settings).
        # Fall back to DEFAULT_MODEL if unset or no longer in the allowlist.
        user_settings = await get_user_settings(user_id)
        chosen_model = (user_settings or {}).get("extraction_model")
        if chosen_model not in AVAILABLE_MODEL_IDS:
            chosen_model = DEFAULT_MODEL

        # Agentic table extraction runs on the Claude Agent SDK, so it is
        # Claude-only. If any table field on this form is in agentic mode
        # (per-field extraction_strategy == "agentic", or the legacy form-level
        # table_extraction_mode == "agentic") and the user's preferred model is
        # not Claude, override for THIS RUN ONLY — user_settings is never
        # touched. The form builder warns before the run; the job records why,
        # so the switch is auditable rather than silent.
        model_override_reason = None
        _sd = form.get("schema_def") or {}
        if isinstance(_sd, str):
            try:
                _sd = json.loads(_sd)
            except (json.JSONDecodeError, TypeError):
                _sd = {}
        _form_agentic = resolve_strategy(_sd.get("table_extraction_mode")) == AGENTIC
        _has_agentic_field = _form_agentic or any(
            field_strategy(of) == AGENTIC
            for sig in (_sd.get("signatures") or [])
            for of in (sig.get("output_fields") or [])
        )
        if _has_agentic_field and not chosen_model.startswith("anthropic/"):
            claude_model = DEFAULT_MODEL if DEFAULT_MODEL.startswith("anthropic/") else next(
                (m for m in AVAILABLE_MODEL_IDS if m.startswith("anthropic/")), None
            )
            if claude_model:
                logger.info(
                    "[extractions] agentic form %s: model %s -> %s",
                    extraction_data.form_id, chosen_model, claude_model,
                )
                model_override_reason = (
                    f"agentic_mode_requires_claude (was {chosen_model})"
                )
                chosen_model = claude_model
            else:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="This form uses agentic table extraction, which requires a "
                           "Claude model, but no Claude model is available.",
                )

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
                "max_documents": extraction_data.max_documents,
                "model": chosen_model,
                **({"model_override_reason": model_override_reason}
                   if model_override_reason else {}),
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
            max_documents=extraction_data.max_documents,
            model=chosen_model,
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


@router.get("/coverage")
@limiter.limit(RATE_LIMIT_EXTRACTION_LIST)
async def get_extraction_coverage(
    request: Request,
    project_id: UUID = Query(...),
    user_id: UUID = Depends(get_current_user),
):
    """
    Get per-form extraction coverage for a project.

    Returns aggregated data: for each form that has been used in at least one
    extraction, how many project documents have been extracted, failed, or not
    yet attempted.
    """
    try:
        await check_project_access(project_id, user_id, "can_view_results")
        pid = str(project_id)

        # 1) Total completed documents in the project
        docs_result = supabase.table("documents")\
            .select("id", count="exact")\
            .eq("project_id", pid)\
            .eq("processing_status", "completed")\
            .execute()
        total_docs = docs_result.count or 0

        # 2) All extractions for this project (to know which forms were used)
        extractions_result = supabase.table("extractions")\
            .select("id, form_id, status, created_at")\
            .eq("project_id", pid)\
            .order("created_at", desc=True)\
            .execute()
        extractions_data = extractions_result.data or []

        if not extractions_data:
            return []

        # Group extractions by form_id
        from collections import defaultdict
        form_extractions: dict = defaultdict(list)
        for ext in extractions_data:
            form_extractions[ext["form_id"]].append(ext)

        # 3) Get form names
        form_ids = list(form_extractions.keys())
        forms_result = supabase.table("forms")\
            .select("id, form_name")\
            .in_("id", form_ids)\
            .execute()
        form_names = {f["id"]: f["form_name"] for f in (forms_result.data or [])}

        # 4) Count distinct successfully extracted document_ids per form, and
        #    flag documents whose latest result has field-level extraction
        #    failures (cells with status missing/error masquerading as NR).
        #
        # extracted_data (the full {value, source_text, status} envelope per
        # field, including grounding quotes) is only ever needed for the
        # LATEST result per (form_id, document_id) — fetching it for every
        # historical row here was the dominant cost on projects with a long
        # extraction history, since this endpoint is polled every 5-15s while
        # a job is active. Fetch row identity first (cheap), then fetch
        # extracted_data only for the rows that turn out to be latest.
        #
        # PostgREST caps an unranged select at 1000 rows — paginate with
        # .range() so a project with a long history (this query has no other
        # filter) doesn't silently drop its oldest extraction_results rows,
        # which would undercount coverage for documents whose only
        # successful run happened outside the first 1000 rows returned.
        results_data = []
        page_size = 1000
        offset = 0
        while True:
            page = supabase.table("extraction_results")\
                .select("id, form_id, document_id")\
                .eq("project_id", pid)\
                .order("created_at", desc=True)\
                .range(offset, offset + page_size - 1)\
                .execute()
            page_data = page.data or []
            results_data.extend(page_data)
            if len(page_data) < page_size:
                break
            offset += page_size

        # Build set of extracted doc_ids per form, and remember which row id
        # is the latest per (form, document) — results are already ordered
        # created_at desc, so the first row seen per pair is the latest one.
        form_extracted_docs: dict = defaultdict(set)
        seen_form_doc: dict = defaultdict(set)
        latest_ids_by_form_doc: dict = {}
        for r in results_data:
            fid, did = r["form_id"], r["document_id"]
            form_extracted_docs[fid].add(did)
            if did not in seen_form_doc[fid]:
                seen_form_doc[fid].add(did)
                latest_ids_by_form_doc[r["id"]] = (fid, did)

        form_flagged_docs: dict = defaultdict(set)
        latest_ids = list(latest_ids_by_form_doc.keys())
        # One request per ~1000 ids: chunking exists only as a safety valve for
        # pathologically large id lists, not as the common path — each extra
        # round trip has fixed overhead that dominates for typical list sizes,
        # so a small chunk_size here was making this slower, not faster.
        chunk_size = 1000
        for i in range(0, len(latest_ids), chunk_size):
            chunk = latest_ids[i:i + chunk_size]
            latest_rows = supabase.table("extraction_results")\
                .select("id, extracted_data")\
                .in_("id", chunk)\
                .execute()
            for row in (latest_rows.data or []):
                fid, did = latest_ids_by_form_doc[row["id"]]
                if _flagged_more_than_half_empty(row.get("extracted_data")):
                    form_flagged_docs[fid].add(did)

        # 5) Get all extraction jobs for this project to find failed doc_ids and active jobs
        extraction_ids = [ext["id"] for ext in extractions_data]
        jobs_result = supabase.table("jobs")\
            .select("id, status, progress, input_data, result_data, created_at")\
            .eq("job_type", "extraction")\
            .eq("project_id", pid)\
            .order("created_at", desc=True)\
            .execute()
        jobs_data = jobs_result.data or []

        # Map jobs by extraction_id → form_id in one pass. This used to be an
        # O(jobs × extractions) linear scan (re-scanning all of
        # extractions_data for every job) — extraction_id → form_id is a
        # simple dict lookup since extraction ids are unique.
        extraction_id_to_form_id = {ext["id"]: ext["form_id"] for ext in extractions_data}
        form_jobs: dict = defaultdict(list)
        for job in jobs_data:
            eid = (job.get("input_data") or {}).get("extraction_id")
            form_id = extraction_id_to_form_id.get(eid) if eid else None
            if form_id:
                form_jobs[form_id].append(job)

        # 6) Build coverage response per form
        coverage = []
        for form_id, exts in form_extractions.items():
            extracted_doc_ids = form_extracted_docs.get(form_id, set())
            extracted_count = len(extracted_doc_ids)

            # Collect failed doc_ids from all jobs for this form,
            # excluding any that were later successfully extracted
            failed_doc_ids = set()
            for job in form_jobs.get(form_id, []):
                rd = job.get("result_data") or {}
                for doc_id in (rd.get("failed_document_ids") or []):
                    if doc_id not in extracted_doc_ids:
                        failed_doc_ids.add(doc_id)
            failed_count = len(failed_doc_ids)

            not_run_count = max(0, total_docs - extracted_count - failed_count)

            # Active jobs (pending or processing)
            active_jobs = []
            for job in form_jobs.get(form_id, []):
                if job["status"] in ("pending", "processing"):
                    rd = job.get("result_data") or {}
                    active_jobs.append({
                        "job_id": job["id"],
                        "extraction_id": (job.get("input_data") or {}).get("extraction_id"),
                        "status": job["status"],
                        "progress": job.get("progress", 0),
                        "papers_total": rd.get("total_documents", 0),
                        "papers_done": (rd.get("successful_extractions") or 0) + (rd.get("failed_extractions") or 0),
                    })

            # Most recent extraction date and ID (exts already sorted desc by created_at)
            last_run_at = exts[0]["created_at"]
            latest_extraction_id = exts[0]["id"]

            # Find the most recent failed extraction (for retry action)
            latest_failed_extraction_id = None
            for ext in exts:
                if ext["status"] == "failed":
                    latest_failed_extraction_id = ext["id"]
                    break

            coverage.append({
                "form_id": form_id,
                "form_name": form_names.get(form_id, "Unknown Form"),
                "total_project_documents": total_docs,
                "extracted_count": extracted_count,
                "failed_count": failed_count,
                "not_run_count": not_run_count,
                "total_runs": len(exts),
                "last_run_at": last_run_at,
                "latest_extraction_id": latest_extraction_id,
                "latest_failed_extraction_id": latest_failed_extraction_id,
                "active_jobs": active_jobs,
                "extracted_document_ids": list(extracted_doc_ids),
                "failed_document_ids": list(failed_doc_ids),
                "flagged_count": len(form_flagged_docs.get(form_id, set())),
                "flagged_document_ids": list(form_flagged_docs.get(form_id, set())),
            })

        # Sort: active jobs first, then by last_run_at desc within each group
        coverage.sort(
            key=lambda c: (0 if c["active_jobs"] else 1, c["last_run_at"]),
        )
        # Reverse the date within groups (we want newest first)
        active = sorted([c for c in coverage if c["active_jobs"]], key=lambda c: c["last_run_at"], reverse=True)
        inactive = sorted([c for c in coverage if not c["active_jobs"]], key=lambda c: c["last_run_at"], reverse=True)
        coverage = active + inactive

        return coverage

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to get extraction coverage")
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
                from datetime import datetime, timezone
                supabase.table("jobs").update({
                    "status": JobStatus.CANCELLED.value,
                    "completed_at": datetime.now(timezone.utc).isoformat(),
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

        # Verify project access and extraction permission.
        # mutating=False: cancelling in-flight work must stay possible after
        # the project is archived.
        await check_project_access(
            UUID(extraction["project_id"]), user_id, "can_run_extractions", mutating=False
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
    payload: Optional[dict] = Body(default=None),
    user_id: UUID = Depends(get_current_user)
):
    """
    Retry documents from a prior extraction run as a NEW run.

    By default retries only the documents that hard-failed (recorded in the
    job's failed_document_ids). Callers may instead pass an explicit
    {"document_ids": [...]} body to retry specific studies — e.g. ones that
    succeeded at the document level but under-extracted (fields with status
    "missing"/"error").

    Creates a brand-new extraction row (so it appears as its own entry in
    "View by run" with its own results) and runs the selected documents under
    it. The source extraction is left untouched.
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

        # Explicit document_ids (e.g. studies whose fields came back
        # missing/error) take precedence over the job's recorded hard failures.
        explicit_ids = (payload or {}).get("document_ids") or None
        if explicit_ids:
            failed_document_ids = explicit_ids
        else:
            failed_document_ids = result_data.get("failed_document_ids") or []
            # If no specific failed docs recorded, the whole extraction failed — retry all docs
            if not failed_document_ids:
                failed_document_ids = None  # None = run on all project documents

        # Reuse the model from the most recent job's input_data; fall back to
        # the user's current preference, then DEFAULT_MODEL. Retries should not
        # silently change which LLM extracted the rows.
        prev_input = job.get("input_data") or {}
        retry_model = prev_input.get("model")
        if retry_model not in AVAILABLE_MODEL_IDS:
            user_settings_retry = await get_user_settings(user_id)
            retry_model = (user_settings_retry or {}).get("extraction_model")
            if retry_model not in AVAILABLE_MODEL_IDS:
                retry_model = DEFAULT_MODEL

        # Create a NEW extraction row so the retry shows as its own run in
        # "View by run" and the source run's results are preserved.
        new_ext_result = supabase.table("extractions").insert({
            "project_id": extraction["project_id"],
            "form_id": extraction["form_id"],
            "status": "pending",
        }).execute()

        if not new_ext_result.data:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to create retry run"
            )

        new_extraction_id = new_ext_result.data[0]["id"]

        # Create the job for the retry, pointing at the new extraction
        new_job_data = {
            "user_id": str(user_id),
            "project_id": extraction["project_id"],
            "job_type": JobType.EXTRACTION.value,
            "status": JobStatus.PENDING.value,
            "progress": 0,
            "input_data": {
                "extraction_id": str(new_extraction_id),
                "form_id": str(extraction["form_id"]),
                "document_ids": failed_document_ids,
                "max_documents": None,
                "model": retry_model,
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

        # Trigger the extraction task against the new extraction
        from app.workers.extraction_tasks import run_extraction

        celery_task = run_extraction.delay(
            extraction_id=str(new_extraction_id),
            job_id=new_job_id,
            document_ids=failed_document_ids,
            max_documents=None,
            model=retry_model,
        )

        # Update job with Celery task ID
        supabase.table("jobs").update({
            "celery_task_id": celery_task.id
        }).eq("id", new_job_id).execute()

        retrying_count = len(failed_document_ids) if failed_document_ids else "all"
        background_tasks.add_task(
            log_activity,
            user_id=user_id,
            action_type="extraction",
            action="Retry Failed Papers",
            description=f"Retrying {retrying_count} papers from extraction {extraction_id} as a new run",
            project_id=UUID(extraction["project_id"]),
            metadata={
                "source_extraction_id": str(extraction_id),
                "extraction_id": str(new_extraction_id),
                "retrying_count": retrying_count,
            },
        )

        return {
            "job_id": new_job_id,
            "extraction_id": str(new_extraction_id),
            "retrying_count": retrying_count
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to retry failed extraction %s", extraction_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred"
        )
