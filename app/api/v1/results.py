"""
Extraction results endpoints - View and export extraction results.
"""

import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status, Query, Request, Response
from fastapi.responses import StreamingResponse
from supabase import create_client
from uuid import UUID
from typing import List, Optional, Literal
import json
import csv
import io
from datetime import datetime, timezone

from app.dependencies import get_current_user
from app.config import settings
from app.services.project_access import check_project_access
from app.services.blinding_service import (
    filter_results_for_user,
    get_project_review_settings,
    get_user_assignments_for_documents,
    _decide_visible_rows,
)
from app.models.schemas import ExtractionResultResponse, ConsensusResultResponse, SourceIndexResponse
from app.services.settings_service import get_user_settings
from app.services.storage_service import storage_service
from app.services.activity_service import log_activity
from app.services.audit_service import log_audit
from app.services.cache_service import cache_service
from app.services.notification_service import create_notification
from app.rate_limit import limiter
from app.rate_limits import RATE_LIMIT_CONSENSUS_SAVE, RATE_LIMIT_CONSENSUS_READ
from pydantic import BaseModel
from typing import Dict, Any, Optional
from postgrest.exceptions import APIError as PostgRESTError


logger = logging.getLogger(__name__)

from utils import absence
from utils import consensus_summary
from utils import value_compare

router = APIRouter()

# Initialize Supabase client
supabase = create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_KEY)


# ── Export preference helpers ──────────────────────────────────────

DATE_FORMATTERS = {
    "ISO":  lambda dt: dt.strftime("%Y-%m-%d") if isinstance(dt, datetime) else str(dt),
    "US":   lambda dt: dt.strftime("%m/%d/%Y") if isinstance(dt, datetime) else str(dt),
    "EU":   lambda dt: dt.strftime("%d/%m/%Y") if isinstance(dt, datetime) else str(dt),
    "Long": lambda dt: dt.strftime("%B %-d, %Y") if isinstance(dt, datetime) else str(dt),
}

METADATA_KEYS = {"id", "extraction_id", "project_id", "form_id", "document_id", "created_at", "updated_at", "job_id", "extraction_type"}
CONFIDENCE_SUFFIXES = (".confidence", ".reasoning", "_confidence", "_reasoning")
SOURCE_LOCATION_SUFFIX = ".source_location"


async def _apply_blinding_grouped(
    results: List[Dict[str, Any]],
    user_id: UUID,
    project_id: Optional[UUID] = None,
    viewer_role: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Apply per-document blinding to a heterogeneous result list.

    Blinding decisions are per-document (a reviewer's role/visibility can
    differ document-to-document), so any endpoint that returns rows spanning
    multiple documents must group → filter → recombine. Without this,
    reviewer-role blinding silently leaks on the `?extraction_id`,
    `?project_id`, and `/export` paths (see issue #6 in the audit).

    When `project_id`/`viewer_role` are known up front (the common
    single-project case), they're reused for every group with no extra
    queries. When omitted (the "list across all my projects" branch), the
    caller's role is resolved and cached per distinct project_id found on
    the rows themselves.

    Performance: this used to call `filter_results_for_user` once per
    document group, which re-queried `projects.review_settings` (identical
    for every group in the same project) on every single call — an N+1 that
    made a ~100-document form page ~100 sequential round trips. Instead,
    `review_settings` is resolved once per distinct project_id, and
    `review_assignments` is fetched in one batched query for all documents
    (skipped entirely when every relevant project has blinding off, the
    default). `_decide_visible_rows` is blinding_service's pure, I/O-free
    rules function — shared here so the per-document loop below does no I/O.
    """
    if not results:
        return results
    from collections import defaultdict
    groups: Dict[str, list] = defaultdict(list)
    for r in results:
        doc_id = r.get("document_id")
        if not doc_id:
            # Defensive: a row missing this key can't be blinded.
            # Should not exist in practice (NOT NULL on schema).
            continue
        groups[doc_id].append(r)

    role_cache: Dict[str, Optional[str]] = {}
    if project_id is not None:
        role_cache[str(project_id)] = viewer_role

    # Resolve (project_id, viewer_role) per document group. Only touches the
    # DB (via check_project_access) in the multi-project branch, memoized per
    # distinct project_id — same as before.
    doc_context: Dict[str, tuple] = {}
    for doc_id, group in groups.items():
        group_project_id = project_id
        group_role = viewer_role
        if group_project_id is None:
            pid = group[0].get("project_id")
            if not pid:
                continue
            if pid not in role_cache:
                try:
                    access = await check_project_access(UUID(pid), user_id)
                    role_cache[pid] = access.get("role")
                except HTTPException:
                    role_cache[pid] = None
            group_role = role_cache.get(pid)
            group_project_id = UUID(pid)
        doc_context[doc_id] = (group_project_id, group_role)

    # review_settings once per distinct project_id — not once per document.
    settings_cache: Dict[str, Dict[str, Any]] = {}
    for group_project_id, _ in doc_context.values():
        pid_str = str(group_project_id)
        if pid_str not in settings_cache:
            settings_cache[pid_str] = await get_project_review_settings(group_project_id)

    # review_assignments in one batched query — and only if some relevant
    # project actually has blinding enabled. Default config never queries this.
    needs_assignments = any(
        settings_cache[str(pid)].get("blinding", "none") != "none"
        for pid, _ in doc_context.values()
    )
    assignments = (
        await get_user_assignments_for_documents(user_id, list(doc_context.keys()))
        if needs_assignments else {}
    )

    out: list = []
    for doc_id, group in groups.items():
        if doc_id not in doc_context:
            continue
        group_project_id, group_role = doc_context[doc_id]
        try:
            review_settings = settings_cache[str(group_project_id)]
            filtered = _decide_visible_rows(
                group, user_id, group_role, review_settings, assignments.get(doc_id)
            )
            out.extend(filtered)
        except Exception:
            logger.exception(
                "Blinding failed for doc=%s; dropping group", doc_id
            )
    return out


# Absence rendering lives in utils/absence so the rule is shared and testable;
# it also recurses into table rows, which _apply_export_prefs never did.
_export_cell = absence.export_cell


def _apply_export_prefs(data: dict, prefs: dict) -> dict:
    """Filter/transform extracted data according to user export preferences."""
    include_meta = prefs.get("export_include_metadata", True)
    include_conf = prefs.get("export_include_confidence", True)
    date_fmt = prefs.get("export_date_format", "ISO")
    formatter = DATE_FORMATTERS.get(date_fmt, DATE_FORMATTERS["ISO"])

    out = {}
    for k, v in data.items():
        # Filter metadata keys
        if not include_meta and k.lower() in METADATA_KEYS:
            continue
        # Filter confidence/reasoning keys
        if not include_conf and any(k.lower().endswith(s) for s in CONFIDENCE_SUFFIXES):
            continue
        # Filter source_location keys from CSV/JSON exports (too verbose)
        if k.endswith(SOURCE_LOCATION_SUFFIX):
            continue
        # Nested value-cell: label absence from `status` and drop internal keys.
        # not_reported → "NR", not_applicable → "NA", failure → the failure
        # marker (never blank — a blank column reads as "not reported").
        if isinstance(v, dict) and "value" in v:
            v = _export_cell(v)
        # Filter source_location from other nested dicts
        elif isinstance(v, dict) and "source_location" in v:
            v = {dk: dv for dk, dv in v.items() if dk != "source_location"}
        # Format dates
        if isinstance(v, str):
            try:
                dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
                v = formatter(dt)
            except (ValueError, TypeError):
                pass
        out[k] = v
    return out


@router.get("", response_model=List[ExtractionResultResponse])
async def list_results(
    extraction_id: Optional[UUID] = Query(None),
    project_id: Optional[UUID] = Query(None),
    form_id: Optional[UUID] = Query(None),
    document_id: Optional[UUID] = Query(None),
    limit: int = Query(default=50, le=500),
    offset: int = Query(default=0, ge=0),
    user_id: UUID = Depends(get_current_user)
):
    """
    List extraction results.

    - **extraction_id** (optional): Filter by specific extraction
    - **project_id** (optional): Filter by project
    - **form_id** (optional): Filter by form
    - **document_id** (optional): Filter by document

    Returns extraction results sorted by creation date (newest first).
    """
    try:
        resolved_project_id: Optional[UUID] = None
        resolved_role: Optional[str] = None

        if extraction_id:
            # Verify extraction exists and user has access to its project
            extraction_result = supabase.table("extractions")\
                .select("project_id")\
                .eq("id", str(extraction_id))\
                .execute()

            if not extraction_result.data:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Extraction not found"
                )

            extraction_project_id = extraction_result.data[0]["project_id"]
            access = await check_project_access(UUID(extraction_project_id), user_id, "can_view_results")
            resolved_project_id = UUID(extraction_project_id)
            resolved_role = access.get("role")

            # Get results for specific extraction
            query = supabase.table("extraction_results")\
                .select("*")\
                .eq("extraction_id", str(extraction_id))

            if form_id:
                query = query.eq("form_id", str(form_id))
            if document_id:
                query = query.eq("document_id", str(document_id))

            result = query.order("created_at", desc=True).range(offset, offset + limit - 1).execute()

        elif project_id:
            # Verify user has access to this project
            access = await check_project_access(project_id, user_id, "can_view_results")
            resolved_project_id = project_id
            resolved_role = access.get("role")

            # Get all extractions for project
            extractions_result = supabase.table("extractions")\
                .select("id")\
                .eq("project_id", str(project_id))\
                .execute()

            extraction_ids = [e["id"] for e in (extractions_result.data or [])]

            if not extraction_ids:
                return []

            # Get results for those extractions
            query = supabase.table("extraction_results")\
                .select("*")\
                .in_("extraction_id", extraction_ids)

            if form_id:
                query = query.eq("form_id", str(form_id))
            if document_id:
                query = query.eq("document_id", str(document_id))

            result = query.order("created_at", desc=True).range(offset, offset + limit - 1).execute()

        else:
            # Get all results from user's owned + member projects
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

            # Get all extractions for user's projects
            extractions_result = supabase.table("extractions")\
                .select("id")\
                .in_("project_id", project_ids)\
                .execute()

            extraction_ids = [e["id"] for e in (extractions_result.data or [])]

            if not extraction_ids:
                return []

            # Get results
            query = supabase.table("extraction_results")\
                .select("*")\
                .in_("extraction_id", extraction_ids)

            if form_id:
                query = query.eq("form_id", str(form_id))
            if document_id:
                query = query.eq("document_id", str(document_id))

            result = query.order("created_at", desc=True).range(offset, offset + limit - 1).execute()

        results = result.data or []

        # Attach the LLM model used for each AI result, derived from the job's
        # input_data.model in one batched lookup. Manual/consensus rows have no
        # model and are left as None.
        ai_job_ids = list({
            r.get("job_id") for r in results
            if r.get("job_id") and (r.get("extraction_type") or "ai") == "ai"
        })
        if ai_job_ids:
            jobs_result = supabase.table("jobs")\
                .select("id, input_data")\
                .in_("id", ai_job_ids)\
                .execute()
            model_by_job = {
                j["id"]: (j.get("input_data") or {}).get("model")
                for j in (jobs_result.data or [])
            }
            for r in results:
                if (r.get("extraction_type") or "ai") == "ai":
                    r["model_name"] = model_by_job.get(r.get("job_id"))

        # Apply blinding on every path. The grouped helper handles broad
        # queries (?extraction_id, ?project_id, no-filter) by partitioning by
        # document and filtering each group.
        results = await _apply_blinding_grouped(results, user_id, project_id=resolved_project_id, viewer_role=resolved_role)

        return [ExtractionResultResponse(**r) for r in results]

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to list results")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred"
        )


class ManualExtractionCreate(BaseModel):
    """Manual or consensus extraction submission."""
    document_id: UUID
    form_id: UUID
    extracted_data: Dict[str, Any]
    extraction_type: Literal["manual", "consensus"] = "manual"
    reviewer_role: Optional[str] = None
    is_partial: bool = False


@router.post("/manual", response_model=ExtractionResultResponse, status_code=status.HTTP_201_CREATED)
async def save_manual_extraction(
    data: ManualExtractionCreate,
    user_id: UUID = Depends(get_current_user)
):
    """
    Save a manual extraction result.

    - **document_id**: Document that was manually extracted
    - **form_id**: Form used for extraction
    - **extracted_data**: The manually extracted field values
    - **extraction_type**: Should be "manual"
    """
    try:
        # Verify document exists
        doc_result = supabase.table("documents")\
            .select("project_id")\
            .eq("id", str(data.document_id))\
            .execute()

        if not doc_result.data:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Document not found"
            )

        project_id = doc_result.data[0]["project_id"]

        await check_project_access(UUID(project_id), user_id, "can_run_manual_extractions")

        # Auto-detect reviewer_role from assignment when caller omits it.
        reviewer_role = data.reviewer_role
        if reviewer_role is None:
            try:
                asg = supabase.table("review_assignments")\
                    .select("reviewer_role")\
                    .eq("project_id", project_id)\
                    .eq("document_id", str(data.document_id))\
                    .eq("reviewer_user_id", str(user_id))\
                    .neq("reviewer_role", "adjudicator")\
                    .limit(1)\
                    .execute()
                if asg.data:
                    reviewer_role = asg.data[0]["reviewer_role"]
            except Exception:
                pass
        was_auto_detected = reviewer_role is not None and data.reviewer_role is None

        # Validate that an explicitly-passed reviewer_role matches a current assignment.
        # Prevents a stale frontend session from writing data under the wrong role.
        if data.reviewer_role is not None and not was_auto_detected:
            try:
                asg_check = supabase.table("review_assignments")\
                    .select("id")\
                    .eq("project_id", project_id)\
                    .eq("document_id", str(data.document_id))\
                    .eq("reviewer_user_id", str(user_id))\
                    .eq("reviewer_role", data.reviewer_role)\
                    .limit(1)\
                    .execute()
                if not asg_check.data:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail=f"Role '{data.reviewer_role}' does not match a current assignment for this document — reload the page to pick up your updated role.",
                    )
            except HTTPException:
                raise
            except Exception:
                pass  # Don't block save if the assignment check itself errors

        logger.info(
            "save_manual_extraction: user=%s doc=%s form=%s requested_role=%s resolved_role=%s auto=%s",
            user_id, data.document_id, data.form_id,
            data.reviewer_role, reviewer_role, was_auto_detected,
        )

        # Verify form exists and belongs to same project
        form_result = supabase.table("forms")\
            .select("id")\
            .eq("id", str(data.form_id))\
            .eq("project_id", project_id)\
            .execute()

        if not form_result.data:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Form not found or doesn't belong to this project"
            )

        # Tag extracted_data with the partial marker so listing endpoints can distinguish
        # in-progress saves from completed ones without a schema change.
        extracted_data = dict(data.extracted_data)
        if data.is_partial:
            extracted_data["_partial"] = True
        else:
            extracted_data.pop("_partial", None)

        # Find or create a grouping extraction record. The unique index on
        # (project_id, form_id, status) is partial (WHERE status IN ('manual','consensus'))
        # per phase3_006, which PostgREST's on_conflict=cols cannot reference — so use a
        # SELECT-then-INSERT with 23505 race-tolerance instead of upsert.
        extraction_status = data.extraction_type  # "manual" | "consensus"
        existing_extraction = supabase.table("extractions")\
            .select("id")\
            .eq("project_id", project_id)\
            .eq("form_id", str(data.form_id))\
            .eq("status", extraction_status)\
            .limit(1)\
            .execute()

        if existing_extraction.data:
            extraction_id = existing_extraction.data[0]["id"]
        else:
            try:
                inserted = supabase.table("extractions").insert({
                    "project_id": project_id,
                    "form_id": str(data.form_id),
                    "status": extraction_status,
                }).execute()
                if not inserted.data:
                    raise HTTPException(
                        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                        detail="Failed to create extraction record"
                    )
                extraction_id = inserted.data[0]["id"]
            except PostgRESTError as e:
                if getattr(e, "code", None) == "23505":
                    retry = supabase.table("extractions")\
                        .select("id")\
                        .eq("project_id", project_id)\
                        .eq("form_id", str(data.form_id))\
                        .eq("status", extraction_status)\
                        .limit(1)\
                        .execute()
                    if not retry.data:
                        raise
                    extraction_id = retry.data[0]["id"]
                else:
                    raise

        # Upsert extraction result — match on (extraction_id, document_id, reviewer_role)
        # so R1 and R2 saves for the same doc never overwrite each other.
        # Supabase .eq(col, None) generates `= NULL` (always false), not IS NULL — use .is_() instead.
        existing_query = supabase.table("extraction_results")\
            .select("id, extracted_by")\
            .eq("extraction_id", extraction_id)\
            .eq("document_id", str(data.document_id))
        if reviewer_role is None:
            existing_query = existing_query.is_("reviewer_role", "null")
        else:
            existing_query = existing_query.eq("reviewer_role", reviewer_role)
        existing_result = existing_query.limit(1).execute()

        # If no role-specific row found, migrate any pre-existing null-role row.
        # This handles old saves (before reviewer_role was tracked) and avoids a
        # duplicate-key error on extraction_results_extraction_document_unique.
        if not existing_result.data and reviewer_role is not None:
            try:
                null_row = supabase.table("extraction_results")\
                    .select("id, extracted_by")\
                    .eq("extraction_id", extraction_id)\
                    .eq("document_id", str(data.document_id))\
                    .is_("reviewer_role", "null")\
                    .limit(1)\
                    .execute()
                if null_row.data:
                    existing_result = null_row
            except Exception:
                pass

        if existing_result.data:
            existing_row = existing_result.data[0]
            existing_author = existing_row.get("extracted_by")
            # Block overwriting another reviewer's row — admin must clear stale data first.
            if existing_author and existing_author != str(user_id):
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Role conflict: this slot contains another reviewer's extraction. An admin must clear the stale data before you can save here.",
                )
            result = supabase.table("extraction_results")\
                .update({
                    "extracted_data": extracted_data,
                    "extraction_type": data.extraction_type,
                    "extracted_by": str(user_id),
                    "reviewer_role": reviewer_role,
                })\
                .eq("id", existing_row["id"])\
                .execute()
        else:
            try:
                result = supabase.table("extraction_results").insert({
                    "extraction_id": extraction_id,
                    "project_id": project_id,
                    "form_id": str(data.form_id),
                    "document_id": str(data.document_id),
                    "extracted_data": extracted_data,
                    "extraction_type": data.extraction_type,
                    "extracted_by": str(user_id),
                    "reviewer_role": reviewer_role,
                }).execute()
            except PostgRESTError as e:
                if getattr(e, "code", None) == "23505":
                    # Race: another concurrent save just inserted this row — UPDATE it.
                    race_query = supabase.table("extraction_results")\
                        .select("id, extracted_by")\
                        .eq("extraction_id", extraction_id)\
                        .eq("document_id", str(data.document_id))
                    if reviewer_role is None:
                        race_query = race_query.is_("reviewer_role", "null")
                    else:
                        race_query = race_query.eq("reviewer_role", reviewer_role)
                    race_row = race_query.limit(1).execute()
                    if race_row.data:
                        result = supabase.table("extraction_results")\
                            .update({
                                "extracted_data": extracted_data,
                                "extraction_type": data.extraction_type,
                                "extracted_by": str(user_id),
                                "reviewer_role": reviewer_role,
                            })\
                            .eq("id", race_row.data[0]["id"])\
                            .execute()
                    else:
                        raise
                else:
                    raise

        if not result.data:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to save manual extraction"
            )

        # Invalidate consensus summary cache so the next page load reflects this save.
        try:
            cache_service.delete(f"consensus_summary:{project_id}:{data.form_id}")
        except Exception:
            pass

        # Auto-update review assignment status if applicable.
        # Partial saves never trigger assignment completion — they're explicitly in-progress.
        if reviewer_role and not data.is_partial:
            try:
                from app.services.assignment_service import check_and_auto_complete_assignment
                await check_and_auto_complete_assignment(
                    project_id=project_id,
                    document_id=str(data.document_id),
                    reviewer_role=reviewer_role,
                )
            except Exception:
                logger.warning("Failed to check/update review assignment status", exc_info=True)

        return ExtractionResultResponse(**result.data[0])

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to save manual extraction")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred"
        )


@router.get("/compare")
async def compare_results(
    document_id: UUID = Query(...),
    form_id: UUID = Query(...),
    user_id: UUID = Depends(get_current_user)
):
    """
    Compare extraction results for a document and form.

    Returns field-by-field comparison between manual and AI extractions.

    - **document_id**: Document to compare results for
    - **form_id**: Form to compare results for
    """
    try:
        # Verify document exists and get project
        doc_result = supabase.table("documents")\
            .select("project_id")\
            .eq("id", str(document_id))\
            .execute()

        if not doc_result.data:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Document not found"
            )

        project_id = doc_result.data[0]["project_id"]

        access = await check_project_access(UUID(project_id), user_id, "can_view_results")

        # Get all results for this document + form
        results = supabase.table("extraction_results")\
            .select("*")\
            .eq("document_id", str(document_id))\
            .eq("form_id", str(form_id))\
            .order("created_at", desc=True)\
            .execute()

        if not results.data:
            return {
                "comparisons": [],
                "statistics": {
                    "total_fields": 0,
                    "matching": 0,
                    "mismatched": 0,
                    "accuracy": 0.0
                }
            }

        # Apply blinding rules — hide other reviewer's manual results when caller
        # is an active reviewer for this document and blinding is enabled.
        visible_results = await filter_results_for_user(
            results.data, user_id, document_id, UUID(project_id), viewer_role=access.get("role")
        )

        # Separate manual vs AI results
        manual_data = {}
        ai_data = {}

        for r in visible_results:
            extracted = r.get("extracted_data", {})
            extraction_type = r.get("extraction_type", "ai")

            if extraction_type == "manual":
                if not manual_data:  # Use most recent
                    manual_data = {k: v for k, v in extracted.items()}
            else:
                if not ai_data:  # Use most recent
                    ai_data = {k: v for k, v in extracted.items()}

        # Build field-by-field comparison
        all_fields = value_compare.comparable_fields(manual_data, ai_data)
        comparisons = []
        matching = 0

        for field in sorted(all_fields):
            manual_val = manual_data.get(field)
            ai_val = ai_data.get(field)
            # One shared comparator (utils/value_compare) so this endpoint, the
            # consensus dashboard and the adjudication screen cannot disagree
            # about whether a field matched. A failed cell is incomparable, not
            # agreement with a reviewer's genuine NR.
            verdict = value_compare.agreement(manual_val, ai_val)
            is_match = verdict == value_compare.AGREE

            if is_match:
                matching += 1

            comparisons.append({
                "field": field,
                "manual_value": manual_val,
                "ai_value": ai_val,
                "match": is_match,
                "comparable": verdict != value_compare.INCOMPARABLE,
                "manual_present": manual_val is not None,
                "ai_present": ai_val is not None
            })

        total_fields = len(all_fields)

        return {
            "comparisons": comparisons,
            "statistics": {
                "total_fields": total_fields,
                "matching": matching,
                "mismatched": total_fields - matching,
                "accuracy": round(matching / total_fields, 4) if total_fields > 0 else 0.0
            }
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to compare results for document %s", document_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred"
        )


@router.get("/consensus-summary")
@limiter.limit(RATE_LIMIT_CONSENSUS_READ)
async def get_consensus_summary(
    request: Request,
    project_id: UUID = Query(...),
    form_id: UUID = Query(...),
    user_id: UUID = Depends(get_current_user)
):
    """
    Return a corpus-level consensus summary for a project + form.

    For each document in the project, reports whether AI, manual, and consensus
    extractions exist, plus agreement percentage where both AI and manual exist.
    """
    try:
        # Check cache first
        cache_key = f"consensus_summary:{project_id}:{form_id}"
        cached = cache_service.get(cache_key)
        if cached:
            return cached

        await check_project_access(project_id, user_id, "can_view_results")

        # Get all documents in project
        docs_result = supabase.table("documents")\
            .select("id, filename, ref_id")\
            .eq("project_id", str(project_id))\
            .order("created_at")\
            .execute()

        all_docs = docs_result.data or []

        # Get all extraction_results for this project + form
        # reviewer_role is selected so the AI-vs-manual comparison can prefer the
        # R1 row instead of whichever manual row happens to sort first.
        results_raw = supabase.table("extraction_results")\
            .select("document_id, extraction_type, reviewer_role, extracted_data")\
            .eq("project_id", str(project_id))\
            .eq("form_id", str(form_id))\
            .order("created_at", desc=False)\
            .execute()

        # Fetch manual extraction rows that have an explicit reviewer role tag.
        role_data = []
        try:
            results_with_role = supabase.table("extraction_results")\
                .select("document_id, reviewer_role, extracted_data, extracted_by")\
                .eq("project_id", str(project_id))\
                .eq("form_id", str(form_id))\
                .eq("extraction_type", "manual")\
                .filter("reviewer_role", "not.is", "null")\
                .order("created_at", desc=False)\
                .execute()
            role_data = results_with_role.data or []
        except Exception:
            logger.warning("role_data query failed for project=%s form=%s", project_id, form_id, exc_info=True)

        # Fetch current reviewer assignments so saved work can be credited to the
        # role its author holds *now* (see consensus_summary._resolve_reviewer_roles).
        assignment_data = []
        try:
            asg_rows = supabase.table("review_assignments")\
                .select("document_id, reviewer_role, reviewer_user_id")\
                .eq("project_id", str(project_id))\
                .execute()
            assignment_data = asg_rows.data or []
        except Exception:
            logger.warning(
                "review_assignments query failed for project=%s", project_id, exc_info=True
            )

        # Docs that already have a consensus result. disputed_count and
        # total_fields are selected alongside agreement_pct so a reviewed document
        # reports all three from the human's decision — previously only the
        # percentage was taken from here, leaving the conflict counts at their
        # string-match values in the same row.
        consensus_rows = supabase.table("consensus_results")\
            .select("document_id, agreement_pct, disputed_count, total_fields")\
            .eq("project_id", str(project_id))\
            .eq("form_id", str(form_id))\
            .execute()
        consensus_data = consensus_rows.data or []

        # Get adjudication results (table may not exist before migration)
        adjudication_data = []
        try:
            adjudication_rows = supabase.table("adjudication_results")\
                .select("document_id, agreement_pct, status")\
                .eq("project_id", str(project_id))\
                .eq("form_id", str(form_id))\
                .execute()
            adjudication_data = adjudication_rows.data or []
        except Exception:
            pass

        # All the arithmetic lives in utils/consensus_summary so it can be tested:
        # this module can't be imported in a test (storage_service calls
        # sts:GetCallerIdentity at import), which is why this endpoint had no test
        # coverage at all while carrying four separate counting bugs.
        response = consensus_summary.build_consensus_summary(
            documents=all_docs,
            extraction_rows=results_raw.data or [],
            role_rows=role_data,
            consensus_rows=consensus_data,
            adjudication_rows=adjudication_data,
            assignment_rows=assignment_data,
        )

        # Cache for 60 seconds (invalidated on consensus save)
        cache_service.set(cache_key, response, ttl=60)

        return response

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to compute consensus summary")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred"
        )


class ConsensusCreate(BaseModel):
    """Upsert request for a consensus review result."""
    document_id: UUID
    form_id: UUID
    review_mode: str = "ai_only"       # "ai_only" | "ai_manual"
    field_decisions: Dict[str, Any]
    agreed_count: int = 0
    disputed_count: int = 0
    total_fields: int = 0
    agreement_pct: Optional[int] = None


@router.post("/consensus", response_model=ConsensusResultResponse, status_code=status.HTTP_201_CREATED)
@limiter.limit(RATE_LIMIT_CONSENSUS_SAVE)
async def save_consensus(
    request: Request,
    data: ConsensusCreate,
    background_tasks: BackgroundTasks,
    user_id: UUID = Depends(get_current_user)
):
    """
    Upsert a consensus review result into consensus_results table.

    If a row already exists for (project_id, form_id, document_id), it is updated.
    Otherwise a new row is inserted.
    """
    try:
        # Verify document exists → get project_id
        doc_result = supabase.table("documents")\
            .select("project_id")\
            .eq("id", str(data.document_id))\
            .execute()

        if not doc_result.data:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")

        project_id = doc_result.data[0]["project_id"]

        await check_project_access(UUID(project_id), user_id, "can_adjudicate")

        # Verify form belongs to same project
        form_result = supabase.table("forms")\
            .select("id")\
            .eq("id", str(data.form_id))\
            .eq("project_id", project_id)\
            .execute()

        if not form_result.data:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Form not found or doesn't belong to this project"
            )

        # Check for existing row
        existing = supabase.table("consensus_results")\
            .select("id")\
            .eq("project_id", project_id)\
            .eq("form_id", str(data.form_id))\
            .eq("document_id", str(data.document_id))\
            .limit(1)\
            .execute()

        payload = {
            "project_id": project_id,
            "form_id": str(data.form_id),
            "document_id": str(data.document_id),
            "review_mode": data.review_mode,
            "field_decisions": data.field_decisions,
            "agreed_count": data.agreed_count,
            "disputed_count": data.disputed_count,
            "total_fields": data.total_fields,
            "agreement_pct": data.agreement_pct,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }

        if existing.data:
            # created_by is deliberately NOT in the update: it records who first
            # submitted this consensus, and rewriting it on every re-save erased
            # the original author the moment anyone else edited the review.
            result = supabase.table("consensus_results")\
                .update(payload)\
                .eq("id", existing.data[0]["id"])\
                .execute()
        else:
            result = supabase.table("consensus_results")\
                .insert({**payload, "created_by": str(user_id)})\
                .execute()

        if not result.data:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to save consensus result"
            )

        saved = result.data[0]
        is_update = bool(existing.data)

        # Invalidate consensus summary cache
        cache_key = f"consensus_summary:{project_id}:{data.form_id}"
        cache_service.delete(cache_key)

        # Activity logging
        background_tasks.add_task(
            log_activity,
            user_id=user_id,
            action_type="consensus",
            action="Consensus Updated" if is_update else "Consensus Submitted",
            description=f"{'Updated' if is_update else 'Submitted'} consensus for document (agreement: {data.agreement_pct}%)",
            project_id=UUID(project_id),
            metadata={
                "document_id": str(data.document_id),
                "form_id": str(data.form_id),
                "review_mode": data.review_mode,
                "agreed_count": data.agreed_count,
                "disputed_count": data.disputed_count,
                "total_fields": data.total_fields,
                "agreement_pct": data.agreement_pct,
            },
        )

        # Audit trail
        background_tasks.add_task(
            log_audit,
            user_id=user_id,
            entity_type="consensus_result",
            entity_id=UUID(saved["id"]),
            action="update" if is_update else "create",
            project_id=UUID(project_id),
            field_name=None,
            old_value=None,
            new_value={"review_mode": data.review_mode, "agreement_pct": data.agreement_pct},
            metadata={"document_id": str(data.document_id), "form_id": str(data.form_id)},
        )

        # Notification
        background_tasks.add_task(
            create_notification,
            user_id=user_id,
            type="success",
            title="Consensus Saved",
            message=f"Consensus review saved ({data.agreement_pct}% agreement, {data.agreed_count}/{data.total_fields} fields agreed).",
            action_label="View Consensus",
            action_url="/consensus",
            related_entity_type="consensus_result",
            related_entity_id=UUID(saved["id"]),
        )

        return ConsensusResultResponse(**saved)

    except HTTPException:
        raise
    except Exception:
        logger.exception("Failed to save consensus result")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred"
        )


@router.get("/consensus/{document_id}", response_model=ConsensusResultResponse)
@limiter.limit(RATE_LIMIT_CONSENSUS_READ)
async def get_consensus(
    request: Request,
    document_id: UUID,
    form_id: UUID = Query(...),
    user_id: UUID = Depends(get_current_user)
):
    """
    Fetch the saved consensus review for a document + form combination.

    Returns 404 if no consensus review has been saved yet.
    """
    try:
        # Verify document → project ownership
        doc_result = supabase.table("documents")\
            .select("project_id")\
            .eq("id", str(document_id))\
            .execute()

        if not doc_result.data:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")

        project_id = doc_result.data[0]["project_id"]

        await check_project_access(UUID(project_id), user_id, "can_view_results")

        result = supabase.table("consensus_results")\
            .select("*")\
            .eq("document_id", str(document_id))\
            .eq("form_id", str(form_id))\
            .limit(1)\
            .execute()

        if not result.data:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No consensus result found")

        return ConsensusResultResponse(**result.data[0])

    except HTTPException:
        raise
    except Exception:
        logger.exception("Failed to get consensus result for document %s", document_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred"
        )


@router.get("/{result_id}/source-index", response_model=SourceIndexResponse)
async def get_source_index(
    result_id: UUID,
    user_id: UUID = Depends(get_current_user)
):
    """
    Build an inverted index from extracted data for backward linking (PDF → fields).

    Returns a mapping of page numbers to the fields extracted from that page,
    including character offset ranges for highlight positioning.
    """
    try:
        result = supabase.table("extraction_results")\
            .select("*")\
            .eq("id", str(result_id))\
            .execute()

        if not result.data:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Result not found")

        row = result.data[0]

        # Project access check + blinding enforcement
        extraction_q = supabase.table("extractions")\
            .select("project_id")\
            .eq("id", row["extraction_id"])\
            .execute()
        if not extraction_q.data:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Result not found")
        access = await check_project_access(UUID(extraction_q.data[0]["project_id"]), user_id, "can_view_results")

        visible = await filter_results_for_user(
            [row], user_id, UUID(row["document_id"]), UUID(extraction_q.data[0]["project_id"]), viewer_role=access.get("role")
        )
        if not visible:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Result not visible under blinding rules")

        extracted_data = row.get("extracted_data", {})

        # Build page index from source_location fields
        page_index: Dict[str, list] = {}

        for key, value in extracted_data.items():
            source_loc = None

            # Flat format: field.source_location
            if key.endswith(".source_location") and isinstance(value, dict):
                field_name = key[:-len(".source_location")]
                source_loc = value
            # Nested format: {"field": {"value": ..., "source_location": {...}}}
            elif isinstance(value, dict) and "source_location" in value:
                field_name = key
                source_loc = value["source_location"]

            if source_loc and isinstance(source_loc, dict):
                page_str = str(source_loc.get("page", 1))
                if page_str not in page_index:
                    page_index[page_str] = []
                page_index[page_str].append({
                    "field": field_name,
                    "start_char": source_loc.get("start_char", 0),
                    "end_char": source_loc.get("end_char", 0),
                    "matched_text": source_loc.get("matched_text", ""),
                })

        return SourceIndexResponse(page_index=page_index)

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to build source index for result %s", result_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred"
        )


@router.get("/{result_id}/page-map")
async def get_page_map(
    result_id: UUID,
    user_id: UUID = Depends(get_current_user)
):
    """
    Get the page boundary map for the document associated with an extraction result.

    Parses Marker API page separators from the stored markdown to return
    character offset ranges per page.
    """
    try:
        # Get the result to find the document
        result = supabase.table("extraction_results")\
            .select("document_id, extraction_id")\
            .eq("id", str(result_id))\
            .execute()

        if not result.data:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Result not found")

        document_id = result.data[0]["document_id"]

        # Project access check — page boundaries are doc metadata,
        # but viewing them implies viewing the result they came from.
        extraction_q = supabase.table("extractions")\
            .select("project_id")\
            .eq("id", result.data[0]["extraction_id"])\
            .execute()
        if not extraction_q.data:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Result not found")
        await check_project_access(UUID(extraction_q.data[0]["project_id"]), user_id, "can_view_results")

        # Get the document's S3 markdown path
        doc = supabase.table("documents")\
            .select("s3_markdown_path")\
            .eq("id", document_id)\
            .execute()

        if not doc.data or not doc.data[0].get("s3_markdown_path"):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document markdown not found")

        s3_key = doc.data[0]["s3_markdown_path"]

        # Download markdown from S3
        try:
            response = storage_service.s3_client.get_object(
                Bucket=settings.S3_BUCKET,
                Key=s3_key
            )
            markdown_content = response["Body"].read().decode("utf-8")
        except Exception as e:
            logger.error(f"Failed to download markdown from S3: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to retrieve document markdown"
            )

        # Parse page boundaries
        from utils.source_linker import parse_page_boundaries
        pages = parse_page_boundaries(markdown_content)

        return {"pages": pages}

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to get page map for result %s", result_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred"
        )


@router.get("/{result_id}", response_model=ExtractionResultResponse)
async def get_result(
    result_id: UUID,
    user_id: UUID = Depends(get_current_user)
):
    """
    Get extraction result by ID.

    Returns the full extracted data for a single result.
    """
    try:
        # Get result
        result = supabase.table("extraction_results")\
            .select("*")\
            .eq("id", str(result_id))\
            .execute()

        if not result.data:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Result not found"
            )

        extraction_result = result.data[0]

        # Verify result's extraction belongs to user's project
        extraction_query = supabase.table("extractions")\
            .select("project_id")\
            .eq("id", extraction_result["extraction_id"])\
            .execute()

        if not extraction_query.data:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Result not found"
            )

        project_id = extraction_query.data[0]["project_id"]

        access = await check_project_access(UUID(project_id), user_id, "can_view_results")

        # Blinding: a reviewer with can_view_results could otherwise fetch a peer
        # reviewer's row by id. filter_results_for_user enforces per-project rules.
        visible = await filter_results_for_user(
            [extraction_result], user_id,
            UUID(extraction_result["document_id"]), UUID(project_id), viewer_role=access.get("role"),
        )
        if not visible:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Result not visible under blinding rules")

        return ExtractionResultResponse(**extraction_result)

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to get result %s", result_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred"
        )


@router.get("/{result_id}/export")
async def export_result(
    result_id: UUID,
    format: Optional[str] = Query(None, pattern="^(json|csv)$"),
    user_id: UUID = Depends(get_current_user)
):
    """
    Export extraction result to JSON or CSV.

    - **format**: Export format (json or csv)

    Returns the result data in the requested format.
    """
    try:
        # Get result (reuse get_result logic for authorization)
        result = supabase.table("extraction_results")\
            .select("*")\
            .eq("id", str(result_id))\
            .execute()

        if not result.data:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Result not found"
            )

        extraction_result = result.data[0]

        # Verify authorization
        extraction_query = supabase.table("extractions")\
            .select("project_id")\
            .eq("id", extraction_result["extraction_id"])\
            .execute()

        if not extraction_query.data:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Result not found"
            )

        project_id = extraction_query.data[0]["project_id"]

        access = await check_project_access(UUID(project_id), user_id, "can_view_results")

        # Blinding: same rule as get_result — block exports of peer reviewer rows.
        visible = await filter_results_for_user(
            [extraction_result], user_id,
            UUID(extraction_result["document_id"]), UUID(project_id), viewer_role=access.get("role"),
        )
        if not visible:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Result not visible under blinding rules")

        # Load user export preferences and resolve format
        try:
            user_prefs = await get_user_settings(user_id)
        except Exception:
            user_prefs = {}
        export_format = format or user_prefs.get("export_format", "json")

        # Get extracted data and apply preferences
        extracted_data = extraction_result.get("extracted_data", {})
        if isinstance(extracted_data, dict):
            extracted_data = _apply_export_prefs(extracted_data, user_prefs)
        elif isinstance(extracted_data, list):
            extracted_data = [_apply_export_prefs(item, user_prefs) if isinstance(item, dict) else item for item in extracted_data]

        if export_format == "json":
            return Response(
                content=json.dumps(extracted_data, indent=2),
                media_type="application/json",
                headers={
                    "Content-Disposition": f"attachment; filename=result_{result_id}.json"
                }
            )

        elif export_format == "csv":
            def flatten_dict(d, parent_key='', sep='_'):
                items = []
                for k, v in d.items():
                    new_key = f"{parent_key}{sep}{k}" if parent_key else k
                    if isinstance(v, dict):
                        items.extend(flatten_dict(v, new_key, sep=sep).items())
                    elif isinstance(v, list):
                        items.append((new_key, ', '.join(map(str, v))))
                    else:
                        items.append((new_key, v))
                return dict(items)

            if isinstance(extracted_data, list):
                flattened_data = [flatten_dict(item) for item in extracted_data]
            else:
                flattened_data = [flatten_dict(extracted_data)]

            output = io.StringIO()
            if flattened_data:
                fieldnames = list(flattened_data[0].keys())
                writer = csv.DictWriter(output, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(flattened_data)

            return Response(
                content=output.getvalue(),
                media_type="text/csv",
                headers={
                    "Content-Disposition": f"attachment; filename=result_{result_id}.csv"
                }
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to export result %s", result_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred"
        )


@router.get("/extraction/{extraction_id}/export")
async def export_extraction_results(
    extraction_id: UUID,
    format: Optional[str] = Query(None, pattern="^(json|csv)$"),
    user_id: UUID = Depends(get_current_user)
):
    """
    Export all results from an extraction to JSON or CSV.

    - **format**: Export format (json or csv)

    Returns all results from the extraction in the requested format.
    """
    try:
        # Verify extraction exists and belongs to user's project
        extraction_result = supabase.table("extractions")\
            .select("project_id")\
            .eq("id", str(extraction_id))\
            .execute()

        if not extraction_result.data:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Extraction not found"
            )

        project_id = extraction_result.data[0]["project_id"]

        access = await check_project_access(UUID(project_id), user_id, "can_view_results")

        # Get all results for extraction
        results = supabase.table("extraction_results")\
            .select("*")\
            .eq("extraction_id", str(extraction_id))\
            .order("created_at")\
            .execute()

        if not results.data:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="No results found for this extraction"
            )

        # Apply blinding before export — the previous code dumped every row
        # in the extraction regardless of reviewer_role, leaking R2/R1 data.
        rows = await _apply_blinding_grouped(list(results.data), user_id, project_id=UUID(project_id), viewer_role=access.get("role"))
        if not rows:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="No results visible to this user for this extraction"
            )

        # Load user export preferences and resolve format
        try:
            user_prefs = await get_user_settings(user_id)
        except Exception:
            user_prefs = {}
        export_format = format or user_prefs.get("export_format", "json")

        # Extract all extracted_data and apply preferences
        all_data = []
        for r in rows:
            item = r.get("extracted_data", {})
            if isinstance(item, dict):
                item = _apply_export_prefs(item, user_prefs)
            all_data.append(item)

        if export_format == "json":
            return Response(
                content=json.dumps(all_data, indent=2),
                media_type="application/json",
                headers={
                    "Content-Disposition": f"attachment; filename=extraction_{extraction_id}_results.json"
                }
            )

        elif export_format == "csv":
            def flatten_dict(d, parent_key='', sep='_'):
                items = []
                for k, v in d.items():
                    new_key = f"{parent_key}{sep}{k}" if parent_key else k
                    if isinstance(v, dict):
                        items.extend(flatten_dict(v, new_key, sep=sep).items())
                    elif isinstance(v, list):
                        items.append((new_key, ', '.join(map(str, v))))
                    else:
                        items.append((new_key, v))
                return dict(items)

            flattened_data = []
            for item in all_data:
                if isinstance(item, list):
                    flattened_data.extend([flatten_dict(sub_item) for sub_item in item])
                else:
                    flattened_data.append(flatten_dict(item))

            output = io.StringIO()
            if flattened_data:
                all_fieldnames = set()
                for item in flattened_data:
                    all_fieldnames.update(item.keys())
                fieldnames = sorted(list(all_fieldnames))

                writer = csv.DictWriter(output, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(flattened_data)

            return Response(
                content=output.getvalue(),
                media_type="text/csv",
                headers={
                    "Content-Disposition": f"attachment; filename=extraction_{extraction_id}_results.csv"
                }
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to export extraction %s results", extraction_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred"
        )
