"""
Pilot study endpoints — calibration loop for extraction quality.

Allows researchers to run extraction on a small sample (1-10 papers),
review results field-by-field, provide corrections, and re-run with
feedback-augmented prompts until satisfied.
"""

import json
import logging
import random
from uuid import UUID, uuid4
from typing import List, Optional, Dict, Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status, Request
from pydantic import BaseModel, Field
from supabase import create_client

from app.dependencies import get_current_user
from app.config import settings
from app.models.enums import JobType, JobStatus
from app.rate_limits import RATE_LIMIT_FORM_MUTATE, RATE_LIMIT_FORM_LIST
from app.services.project_access import check_project_access
from app.rate_limit import limiter
from app.services.activity_service import log_activity
from app.services.cache_service import cache_service

logger = logging.getLogger(__name__)

from utils import absence

router = APIRouter()
supabase = create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_KEY)


# ── Request / Response Models ────────────────────────────────────────────────

class PilotStartRequest(BaseModel):
    document_ids: Optional[List[str]] = None
    count: int = Field(default=3, ge=1, le=10)


class SubfieldCorrection(BaseModel):
    rating: Optional[str] = None  # "correct" | "incorrect" — per-column thumbs
    correct_value: Optional[str] = None
    correct_source_text: Optional[str] = None
    note: Optional[str] = None


class PilotFeedbackEntry(BaseModel):
    # None when the reviewer rated only individual table columns and never the
    # parent field itself — every column carries its own thumbs.
    rating: Optional[str] = None  # "correct" | "incorrect"
    correct_value: Optional[str] = None
    correct_source_text: Optional[str] = None
    note: Optional[str] = None
    document_id: str
    subfield_corrections: Optional[Dict[str, SubfieldCorrection]] = None
    # {col_name: rating + optional correction} — one entry per rated table column


class PilotFeedbackRequest(BaseModel):
    iteration: int
    feedback: Dict[str, PilotFeedbackEntry]


# ── Helpers ──────────────────────────────────────────────────────────────────

def _get_form_with_access(form_id: str, user_id: UUID, permission: str = "can_run_extractions"):
    """Load form, verify project access, return (form, metadata)."""
    result = supabase.table("forms")\
        .select("*")\
        .eq("id", form_id)\
        .execute()

    if not result.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Form not found")

    form = result.data[0]
    return form


def _get_pilot(form: dict) -> dict:
    """Extract pilot state from form metadata, or empty dict."""
    metadata = form.get("metadata")
    if isinstance(metadata, str):
        metadata = json.loads(metadata)
    return (metadata or {}).get("pilot", {})


def _save_pilot(form_id: str, project_id: str, pilot_data: dict):
    """Persist pilot state into form.metadata.pilot."""
    # Read current metadata
    result = supabase.table("forms").select("metadata").eq("id", form_id).execute()
    metadata = (result.data[0].get("metadata") if result.data else None) or {}
    if isinstance(metadata, str):
        metadata = json.loads(metadata)

    metadata["pilot"] = pilot_data
    supabase.table("forms").update({"metadata": json.dumps(metadata)}).eq("id", form_id).execute()

    # Invalidate cache
    if project_id:
        cache_service.delete_pattern(f"forms:project:{project_id}:*")
    cache_service.delete(f"forms:detail:{form_id}")


def _harvest_column_examples(
    doc_results: dict,
    field_name: str,
    col_name: str,
    iter_num: int,
    doc_id: str,
    limit: int = 2,
) -> list:
    """
    Turn a thumbs-up on one table column into per-column few-shot examples.

    Reads the parent table's extracted rows and pulls that column's cell out of
    each. Blank cells are skipped, and so are pipeline failures — a failure
    placeholder must never become a worked example (same rule as the
    top-level branch in _accumulate_feedback).
    """
    field_data = doc_results.get(field_name)
    rows = field_data.get("value") if isinstance(field_data, dict) else field_data
    if not isinstance(rows, list):
        return []

    out: list = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        cell = row.get(col_name)
        if isinstance(cell, dict):
            if absence.normalize_status(cell.get("status")) in absence.FAILURE_STATUSES:
                continue
            value = cell.get("value")
            source = cell.get("source_text", "") or ""
        else:
            value = cell
            source = ""
        if value is None or (isinstance(value, str) and not value.strip()):
            continue
        out.append({
            "value": value,
            "source_text": source,
            "iteration": iter_num,
            "document_id": doc_id,
        })
        if len(out) >= limit:
            break
    return out


def _accumulate_feedback(pilot_data: dict) -> dict:
    """
    Recompute field_examples and field_instructions from all iterations' feedback.

    - 'correct' ratings: the original extraction becomes a few-shot example
    - 'incorrect' ratings: the user's corrected value becomes a few-shot example,
      and the note (if any) is appended to field_instructions

    Table columns are rated individually and land under a "parent.col" compound
    key, which augment_signature_with_feedback splits back out into per-column
    calibration blocks.
    """
    field_examples: Dict[str, list] = {}
    field_instructions: Dict[str, list] = {}

    for iteration in pilot_data.get("iterations", []):
        iter_num = iteration.get("iteration", 0)
        iter_results = iteration.get("results", {})
        iter_feedback = iteration.get("feedback", {})

        for field_name, fb in iter_feedback.items():
            rating = fb.get("rating")
            doc_id = fb.get("document_id", "")

            if rating == "correct":
                # Use the original extraction as an example
                doc_results = iter_results.get(doc_id, {})
                field_data = doc_results.get(field_name, {})
                value = field_data.get("value") if isinstance(field_data, dict) else field_data
                source = field_data.get("source_text", "") if isinstance(field_data, dict) else ""
                # A thumbs-up on a cell the pipeline failed on must not become a
                # worked example — that would teach the model to emit the
                # failure placeholder as if it were an answer.
                failed = (
                    isinstance(field_data, dict)
                    and absence.normalize_status(field_data.get("status"))
                    in absence.FAILURE_STATUSES
                )
                if value is not None and not failed:
                    field_examples.setdefault(field_name, []).append({
                        "value": value,
                        "source_text": source,
                        "iteration": iter_num,
                        "document_id": doc_id,
                    })

            elif rating == "incorrect":
                # Use the corrected value as an example
                correct_val = fb.get("correct_value")
                correct_src = fb.get("correct_source_text", "")
                if correct_val is not None:
                    field_examples.setdefault(field_name, []).append({
                        "value": correct_val,
                        "source_text": correct_src,
                        "note": fb.get("note", ""),
                        "iteration": iter_num,
                        "document_id": doc_id,
                    })

                # Append note to instructions
                note = (fb.get("note") or "").strip()
                if note:
                    field_instructions.setdefault(field_name, []).append(note)

            # Per-column feedback on table fields. Runs regardless of the parent's
            # rating: each column carries its own thumbs, so a column can be rated
            # while the parent field itself never was.
            subfield_corrections = fb.get("subfield_corrections") or {}
            for col_name, col_fb in subfield_corrections.items():
                if isinstance(col_fb, dict):
                    col_rating = col_fb.get("rating")
                    col_val = col_fb.get("correct_value")
                    col_src = col_fb.get("correct_source_text", "")
                    col_note = (col_fb.get("note") or "").strip()
                else:
                    col_rating = getattr(col_fb, "rating", None)
                    col_val = getattr(col_fb, "correct_value", None)
                    col_src = getattr(col_fb, "correct_source_text", "") or ""
                    col_note = (getattr(col_fb, "note", "") or "").strip()
                compound_key = f"{field_name}.{col_name}"
                if col_val is not None:
                    field_examples.setdefault(compound_key, []).append({
                        "value": col_val,
                        "source_text": col_src,
                        "iteration": iter_num,
                        "document_id": doc_id,
                    })
                elif col_rating == "correct":
                    # No correction supplied — the extracted cells are the example.
                    field_examples.setdefault(compound_key, []).extend(
                        _harvest_column_examples(
                            iter_results.get(doc_id, {}), field_name, col_name, iter_num, doc_id
                        )
                    )
                if col_note:
                    field_instructions.setdefault(compound_key, []).append(col_note)

    # Cap examples per field (keep most recent)
    max_per_field = 5
    for fname in field_examples:
        field_examples[fname] = field_examples[fname][-max_per_field:]

    # Deduplicate and join instructions
    field_instructions_str = {}
    for fname, notes in field_instructions.items():
        unique = list(dict.fromkeys(notes))  # preserve order, deduplicate
        field_instructions_str[fname] = "\n".join(f"- {n}" for n in unique)

    return {
        "field_examples": field_examples,
        "field_instructions": field_instructions_str,
    }


# ── Endpoints ────────────────────────────────────────────────────────────────

@router.post("/{form_id}/pilot/start")
@limiter.limit(RATE_LIMIT_FORM_MUTATE)
async def start_pilot(
    request: Request,
    form_id: UUID,
    body: PilotStartRequest,
    background_tasks: BackgroundTasks,
    user_id: UUID = Depends(get_current_user),
):
    """Start a pilot extraction on a small sample of documents."""
    try:
        form = _get_form_with_access(str(form_id), user_id)
        # Pilot is part of form design (calibrate the schema you just built),
        # not a production extraction. Gate on can_create_forms.
        await check_project_access(UUID(form["project_id"]), user_id, "can_create_forms")

        if form["status"] != "active":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Form must be active to run pilot (current: {form['status']})"
            )

        if not form.get("schema_name"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Form has no generated schema"
            )

        project_id = form["project_id"]

        # Get existing pilot state (may be re-piloting)
        pilot = _get_pilot(form)

        # Determine document IDs
        if body.document_ids:
            doc_ids = body.document_ids[:10]
        else:
            # Random selection from completed documents
            docs_result = supabase.table("documents")\
                .select("id")\
                .eq("project_id", project_id)\
                .eq("processing_status", "completed")\
                .execute()
            all_doc_ids = [d["id"] for d in (docs_result.data or [])]
            if not all_doc_ids:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="No completed documents in this project"
                )
            count = min(body.count, len(all_doc_ids))
            doc_ids = random.sample(all_doc_ids, count)

        # Determine iteration number
        iterations = pilot.get("iterations", [])
        next_iter = len(iterations) + 1

        # Create extraction record (flagged as pilot)
        extraction_record = {
            "project_id": project_id,
            "form_id": str(form_id),
            "status": "pending",
        }
        ext_result = supabase.table("extractions").insert(extraction_record).execute()
        if not ext_result.data:
            raise HTTPException(status_code=500, detail="Failed to create extraction record")
        extraction = ext_result.data[0]

        # Create job
        job_data = {
            "user_id": str(user_id),
            "project_id": project_id,
            "job_type": JobType.EXTRACTION.value,
            "status": JobStatus.PENDING.value,
            "progress": 0,
            "input_data": {
                "extraction_id": extraction["id"],
                "form_id": str(form_id),
                "document_ids": doc_ids,
                "is_pilot": True,
            }
        }
        job_result = supabase.table("jobs").insert(job_data).execute()
        if not job_result.data:
            raise HTTPException(status_code=500, detail="Failed to create job record")
        job = job_result.data[0]

        # Trigger Celery task
        from app.workers.extraction_tasks import run_extraction
        run_extraction.delay(
            extraction_id=extraction["id"],
            job_id=job["id"],
            document_ids=doc_ids,
        )

        # Update pilot state
        new_iteration = {
            "iteration": next_iter,
            "job_id": job["id"],
            "extraction_id": extraction["id"],
            "results": {},
            "feedback": {},
        }
        iterations.append(new_iteration)

        pilot_data = {
            "status": "running",
            "sample_document_ids": doc_ids,
            "current_iteration": next_iter,
            "iterations": iterations,
            "field_examples": pilot.get("field_examples", {}),
            "field_instructions": pilot.get("field_instructions", {}),
        }
        _save_pilot(str(form_id), project_id, pilot_data)

        background_tasks.add_task(
            log_activity,
            user_id=user_id,
            action_type="pilot",
            action="Pilot Started",
            description=f"Started pilot iteration {next_iter} on {len(doc_ids)} documents",
            project_id=UUID(project_id),
            metadata={"form_id": str(form_id), "iteration": next_iter, "doc_count": len(doc_ids)},
        )

        return {
            "status": "running",
            "iteration": next_iter,
            "job_id": job["id"],
            "extraction_id": extraction["id"],
            "document_ids": doc_ids,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to start pilot")
        raise HTTPException(status_code=500, detail="An unexpected error occurred")


@router.get("/{form_id}/pilot")
@limiter.limit(RATE_LIMIT_FORM_LIST)
async def get_pilot(
    request: Request,
    form_id: UUID,
    user_id: UUID = Depends(get_current_user),
):
    """Get current pilot state for a form."""
    try:
        form = _get_form_with_access(str(form_id), user_id)
        await check_project_access(UUID(form["project_id"]), user_id, "can_view_results")

        pilot = _get_pilot(form)
        if not pilot:
            return {"status": "none"}

        # If pilot is running, check if the latest job has completed. The
        # historical results snapshot is still written to JSONB here so that
        # _accumulate_feedback (which reads iteration["results"] for "correct"
        # ratings) keeps working across re-piloting cycles.
        if pilot.get("status") == "running" and pilot.get("iterations"):
            latest = pilot["iterations"][-1]
            job_id = latest.get("job_id")
            if job_id:
                job_result = supabase.table("jobs")\
                    .select("status")\
                    .eq("id", job_id)\
                    .execute()
                if job_result.data:
                    job_status = job_result.data[0]["status"]
                    if job_status in ("completed", "failed"):
                        extraction_id = latest.get("extraction_id")
                        if extraction_id and job_status == "completed":
                            results_data = supabase.table("extraction_results")\
                                .select("document_id, extracted_data")\
                                .eq("extraction_id", extraction_id)\
                                .execute()
                            results_map = {}
                            for r in (results_data.data or []):
                                results_map[r["document_id"]] = r["extracted_data"]
                            latest["results"] = results_map

                        pilot["status"] = "reviewing" if job_status == "completed" else "failed"
                        _save_pilot(str(form_id), form["project_id"], pilot)

        # Overlay live results for every iteration into the response so the
        # dialog never shows rows for documents that have since been deleted.
        # ON DELETE CASCADE on extraction_results.document_id makes the live
        # extraction_results query naturally exclude deleted-doc rows.
        #
        # IMPORTANT: this mutates the in-memory copy only — we do NOT call
        # _save_pilot afterwards, so the JSONB snapshot used by
        # _accumulate_feedback is untouched.
        iterations = pilot.get("iterations") or []
        for it in iterations:
            ext_id = it.get("extraction_id")
            if not ext_id:
                continue
            try:
                live = supabase.table("extraction_results")\
                    .select("document_id, extracted_data")\
                    .eq("extraction_id", ext_id)\
                    .execute()
                it["results"] = {
                    r["document_id"]: r["extracted_data"]
                    for r in (live.data or [])
                }
            except Exception as live_err:
                # Live query failure shouldn't break the dialog — fall back to
                # whatever snapshot is already on the iteration dict.
                logger.warning(
                    f"Live-query failed for pilot iteration extraction_id={ext_id}: {live_err}"
                )

        return pilot

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to get pilot state")
        raise HTTPException(status_code=500, detail="An unexpected error occurred")


@router.post("/{form_id}/pilot/feedback")
@limiter.limit(RATE_LIMIT_FORM_MUTATE)
async def submit_pilot_feedback(
    request: Request,
    form_id: UUID,
    body: PilotFeedbackRequest,
    background_tasks: BackgroundTasks,
    user_id: UUID = Depends(get_current_user),
):
    """
    Submit field-level feedback for a pilot iteration and trigger re-extraction.

    Accumulates feedback across all iterations into field_examples and
    field_instructions, then runs a new pilot iteration with augmented prompts.
    """
    try:
        form = _get_form_with_access(str(form_id), user_id)
        await check_project_access(UUID(form["project_id"]), user_id, "can_create_forms")

        pilot = _get_pilot(form)
        if not pilot or pilot.get("status") not in ("reviewing", "completed"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No pilot in review state"
            )

        # Find the matching iteration
        iterations = pilot.get("iterations", [])
        target_iter = None
        for it in iterations:
            if it.get("iteration") == body.iteration:
                target_iter = it
                break

        if target_iter is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Iteration {body.iteration} not found"
            )

        # Store feedback on the iteration
        target_iter["feedback"] = {
            fname: fb.model_dump() for fname, fb in body.feedback.items()
        }

        # Recompute accumulated examples and instructions from all iterations
        accumulated = _accumulate_feedback(pilot)
        pilot["field_examples"] = accumulated["field_examples"]
        pilot["field_instructions"] = accumulated["field_instructions"]

        # Save before triggering next iteration
        _save_pilot(str(form_id), form["project_id"], pilot)

        # Now trigger the next pilot iteration
        project_id = form["project_id"]
        doc_ids = pilot.get("sample_document_ids", [])
        next_iter = len(iterations) + 1

        # Create extraction record
        ext_result = supabase.table("extractions").insert({
            "project_id": project_id,
            "form_id": str(form_id),
            "status": "pending",
        }).execute()
        if not ext_result.data:
            raise HTTPException(status_code=500, detail="Failed to create extraction record")
        extraction = ext_result.data[0]

        # Create job
        job_result = supabase.table("jobs").insert({
            "user_id": str(user_id),
            "project_id": project_id,
            "job_type": JobType.EXTRACTION.value,
            "status": JobStatus.PENDING.value,
            "progress": 0,
            "input_data": {
                "extraction_id": extraction["id"],
                "form_id": str(form_id),
                "document_ids": doc_ids,
                "is_pilot": True,
            }
        }).execute()
        if not job_result.data:
            raise HTTPException(status_code=500, detail="Failed to create job record")
        job = job_result.data[0]

        # Trigger extraction
        from app.workers.extraction_tasks import run_extraction
        run_extraction.delay(
            extraction_id=extraction["id"],
            job_id=job["id"],
            document_ids=doc_ids,
        )

        # Add new iteration
        iterations.append({
            "iteration": next_iter,
            "job_id": job["id"],
            "extraction_id": extraction["id"],
            "results": {},
            "feedback": {},
        })

        pilot["status"] = "running"
        pilot["current_iteration"] = next_iter
        pilot["iterations"] = iterations
        _save_pilot(str(form_id), project_id, pilot)

        background_tasks.add_task(
            log_activity,
            user_id=user_id,
            action_type="pilot",
            action="Pilot Feedback Submitted",
            description=f"Submitted feedback for iteration {body.iteration}, starting iteration {next_iter}",
            project_id=UUID(project_id),
            metadata={
                "form_id": str(form_id),
                "feedback_fields": len(body.feedback),
                "next_iteration": next_iter,
            },
        )

        return {
            "status": "running",
            "iteration": next_iter,
            "job_id": job["id"],
            "extraction_id": extraction["id"],
            "accumulated_examples": len(pilot["field_examples"]),
            "accumulated_instructions": len(pilot["field_instructions"]),
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to submit pilot feedback")
        raise HTTPException(status_code=500, detail="An unexpected error occurred")


@router.post("/{form_id}/pilot/complete")
@limiter.limit(RATE_LIMIT_FORM_MUTATE)
async def complete_pilot(
    request: Request,
    form_id: UUID,
    background_tasks: BackgroundTasks,
    user_id: UUID = Depends(get_current_user),
):
    """Mark pilot as completed. Accumulated feedback persists for all future extractions."""
    try:
        form = _get_form_with_access(str(form_id), user_id)
        await check_project_access(UUID(form["project_id"]), user_id, "can_create_forms")

        pilot = _get_pilot(form)
        if not pilot:
            raise HTTPException(status_code=400, detail="No pilot to complete")

        pilot["status"] = "completed"
        _save_pilot(str(form_id), form["project_id"], pilot)

        total_examples = sum(len(v) for v in pilot.get("field_examples", {}).values())
        total_fields = len(pilot.get("field_examples", {}))

        background_tasks.add_task(
            log_activity,
            user_id=user_id,
            action_type="pilot",
            action="Pilot Completed",
            description=f"Pilot completed with {total_examples} examples across {total_fields} fields",
            project_id=UUID(form["project_id"]),
            metadata={"form_id": str(form_id), "total_examples": total_examples},
        )

        return {
            "status": "completed",
            "total_examples": total_examples,
            "fields_with_examples": total_fields,
            "fields_with_instructions": len(pilot.get("field_instructions", {})),
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to complete pilot")
        raise HTTPException(status_code=500, detail="An unexpected error occurred")


@router.delete("/{form_id}/pilot")
@limiter.limit(RATE_LIMIT_FORM_MUTATE)
async def reset_pilot(
    request: Request,
    form_id: UUID,
    background_tasks: BackgroundTasks,
    user_id: UUID = Depends(get_current_user),
):
    """Discard pilot state and all accumulated feedback."""
    try:
        form = _get_form_with_access(str(form_id), user_id)
        await check_project_access(UUID(form["project_id"]), user_id, "can_create_forms")

        # Remove pilot key from metadata
        metadata = form.get("metadata") or {}
        if isinstance(metadata, str):
            metadata = json.loads(metadata)
        metadata.pop("pilot", None)

        supabase.table("forms").update({"metadata": json.dumps(metadata)}).eq("id", str(form_id)).execute()

        project_id = form["project_id"]
        cache_service.delete_pattern(f"forms:project:{project_id}:*")
        cache_service.delete(f"forms:detail:{str(form_id)}")

        background_tasks.add_task(
            log_activity,
            user_id=user_id,
            action_type="pilot",
            action="Pilot Reset",
            description="Pilot data and calibration feedback cleared",
            project_id=UUID(project_id),
            metadata={"form_id": str(form_id)},
        )

        return {"status": "reset"}

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to reset pilot")
        raise HTTPException(status_code=500, detail="An unexpected error occurred")
