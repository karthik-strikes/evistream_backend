"""
Extraction results endpoints - View and export extraction results.
"""

import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status, Query, Request, Response
from fastapi.responses import StreamingResponse
from supabase import create_client
from uuid import UUID
from typing import List, Optional
import json
import csv
import io
from datetime import datetime, timezone

from app.dependencies import get_current_user
from app.config import settings
from app.services.project_access import check_project_access
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


logger = logging.getLogger(__name__)

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
        # Filter source_location from nested dicts
        if isinstance(v, dict) and "source_location" in v:
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
        if extraction_id:
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

            extraction_project_id = extraction_result.data[0]["project_id"]

            # Verify project belongs to user
            project_result = supabase.table("projects")\
                .select("id")\
                .eq("id", extraction_project_id)\
                .eq("user_id", str(user_id))\
                .execute()

            if not project_result.data:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Extraction not found"
                )

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
            # Get all results from user's projects
            projects_result = supabase.table("projects")\
                .select("id")\
                .eq("user_id", str(user_id))\
                .execute()

            project_ids = [p["id"] for p in (projects_result.data or [])]

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
    extraction_type: str = "manual"  # "manual" | "consensus"
    reviewer_role: Optional[str] = None


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

        await check_project_access(UUID(project_id), user_id, "can_view_results")

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

        # Find or create an extraction record keyed by extraction_type
        # "manual" extractions use status="manual", "consensus" use status="consensus"
        extraction_status = data.extraction_type  # "manual" | "consensus"
        extraction_result = supabase.table("extractions")\
            .select("id")\
            .eq("project_id", project_id)\
            .eq("form_id", str(data.form_id))\
            .eq("status", extraction_status)\
            .limit(1)\
            .execute()

        if extraction_result.data:
            extraction_id = extraction_result.data[0]["id"]
        else:
            new_extraction = supabase.table("extractions").insert({
                "project_id": project_id,
                "form_id": str(data.form_id),
                "status": extraction_status
            }).execute()

            if not new_extraction.data:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Failed to create extraction record"
                )
            extraction_id = new_extraction.data[0]["id"]

        # Upsert extraction result — overwrite if this document was already extracted manually
        existing_result = supabase.table("extraction_results")\
            .select("id")\
            .eq("extraction_id", extraction_id)\
            .eq("document_id", str(data.document_id))\
            .limit(1)\
            .execute()

        if existing_result.data:
            result = supabase.table("extraction_results")\
                .update({
                    "extracted_data": data.extracted_data,
                    "extraction_type": data.extraction_type,
                    "extracted_by": str(user_id),
                    "reviewer_role": data.reviewer_role,
                })\
                .eq("id", existing_result.data[0]["id"])\
                .execute()
        else:
            result = supabase.table("extraction_results").insert({
                "extraction_id": extraction_id,
                "project_id": project_id,
                "form_id": str(data.form_id),
                "document_id": str(data.document_id),
                "extracted_data": data.extracted_data,
                "extraction_type": data.extraction_type,
                "extracted_by": str(user_id),
                "reviewer_role": data.reviewer_role,
            }).execute()

        if not result.data:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to save manual extraction"
            )

        # Auto-update review assignment status if applicable
        if data.reviewer_role:
            try:
                from app.services.assignment_service import check_and_auto_complete_assignment
                await check_and_auto_complete_assignment(
                    project_id=project_id,
                    document_id=str(data.document_id),
                    reviewer_role=data.reviewer_role,
                )
            except Exception:
                logger.warning("Failed to check/update review assignment status")

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

        # Verify project belongs to user
        project_result = supabase.table("projects")\
            .select("id")\
            .eq("id", project_id)\
            .eq("user_id", str(user_id))\
            .execute()

        if not project_result.data:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Document not found"
            )

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

        # Separate manual vs AI results
        manual_data = {}
        ai_data = {}

        for r in results.data:
            extracted = r.get("extracted_data", {})
            extraction_type = r.get("extraction_type", "ai")

            if extraction_type == "manual":
                if not manual_data:  # Use most recent
                    manual_data = {k: v for k, v in extracted.items()}
            else:
                if not ai_data:  # Use most recent
                    ai_data = {k: v for k, v in extracted.items()}

        # Build field-by-field comparison
        all_fields = set(list(manual_data.keys()) + list(ai_data.keys()))
        comparisons = []
        matching = 0

        for field in sorted(all_fields):
            manual_val = manual_data.get(field)
            ai_val = ai_data.get(field)
            is_match = str(manual_val).strip().lower() == str(ai_val).strip().lower() if manual_val and ai_val else False

            if is_match:
                matching += 1

            comparisons.append({
                "field": field,
                "manual_value": manual_val,
                "ai_value": ai_val,
                "match": is_match,
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

        # Get all documents in project
        docs_result = supabase.table("documents")\
            .select("id, filename")\
            .eq("project_id", str(project_id))\
            .order("created_at")\
            .execute()

        all_docs = docs_result.data or []
        doc_map = {d["id"]: d["filename"] for d in all_docs}

        # Get all extraction_results for this project + form
        results_raw = supabase.table("extraction_results")\
            .select("document_id, extraction_type, extracted_data")\
            .eq("project_id", str(project_id))\
            .eq("form_id", str(form_id))\
            .execute()

        # Try to get reviewer_role data (only exists after Phase 2 migration)
        role_data = []
        try:
            results_with_role = supabase.table("extraction_results")\
                .select("document_id, reviewer_role")\
                .eq("project_id", str(project_id))\
                .eq("form_id", str(form_id))\
                .neq("reviewer_role", "null")\
                .execute()
            role_data = results_with_role.data or []
        except Exception:
            pass

        def normalize_ai_data(extracted: dict) -> dict:
            """
            AI extraction stores keys as 'field_name.value'.
            Strip the '.value' suffix so keys match manual extraction keys.
            Skip non-value keys (e.g. confidence, reasoning, source_location).
            """
            normalized = {}
            for k, v in extracted.items():
                if k.endswith(".value"):
                    normalized[k[:-6]] = v
                elif k.endswith((".source_location", ".source_text", ".confidence", ".reasoning")):
                    continue  # Skip metadata keys
            # If no .value keys found, use raw (manual/consensus data)
            return normalized if normalized else extracted

        # Get docs that already have a consensus result (new table)
        consensus_rows = supabase.table("consensus_results")\
            .select("document_id, agreement_pct")\
            .eq("project_id", str(project_id))\
            .eq("form_id", str(form_id))\
            .execute()
        # Map doc_id → reviewed agreement_pct (human judgment, not string-match)
        consensus_map = {r["document_id"]: r["agreement_pct"] for r in (consensus_rows.data or [])}
        consensus_set = set(consensus_map.keys())

        # Track R1 and R2 results
        r1_doc_ids = set()
        r2_doc_ids = set()
        for r in role_data:
            reviewer_role = r.get("reviewer_role")
            if reviewer_role == "reviewer_1":
                r1_doc_ids.add(r["document_id"])
            elif reviewer_role == "reviewer_2":
                r2_doc_ids.add(r["document_id"])

        # Get adjudication results (table may not exist before migration)
        adjudication_map = {}
        adjudication_set = set()
        try:
            adjudication_rows = supabase.table("adjudication_results")\
                .select("document_id, agreement_pct, status")\
                .eq("project_id", str(project_id))\
                .eq("form_id", str(form_id))\
                .execute()
            adjudication_map = {r["document_id"]: r for r in (adjudication_rows.data or [])}
            adjudication_set = set(adjudication_map.keys())
        except Exception:
            pass

        # Group by document_id → {ai: {...}, manual: {...}}
        doc_data: dict = {}
        for r in (results_raw.data or []):
            doc_id = r["document_id"]
            etype = r.get("extraction_type", "ai")
            extracted = r.get("extracted_data") or {}
            if etype == "consensus":
                continue  # Skip legacy consensus rows — we use consensus_results now
            if doc_id not in doc_data:
                doc_data[doc_id] = {}
            # Keep most recent (last write wins; data already ordered by Supabase)
            if etype not in doc_data[doc_id]:
                # Normalize AI keys so they match manual keys
                if etype == "ai":
                    doc_data[doc_id][etype] = normalize_ai_data(extracted)
                else:
                    doc_data[doc_id][etype] = extracted

        # Build per-document summary
        documents_out = []
        for doc in all_docs:
            doc_id = doc["id"]
            data = doc_data.get(doc_id, {})
            has_ai = "ai" in data
            has_manual = "manual" in data
            has_consensus = doc_id in consensus_set

            agreement_pct = None
            disputed_fields = None
            total_fields = None

            if has_ai and has_manual:
                ai_vals = data["ai"]
                manual_vals = data["manual"]
                all_fields = set(list(ai_vals.keys()) + list(manual_vals.keys()))
                total = len(all_fields)
                if total > 0:
                    matching = sum(
                        1 for f in all_fields
                        if ai_vals.get(f) is not None and manual_vals.get(f) is not None
                        and str(ai_vals.get(f, "")).strip().lower() == str(manual_vals.get(f, "")).strip().lower()
                    )
                    agreement_pct = round(matching / total * 100)
                    disputed_fields = total - matching
                    total_fields = total

            # If reviewed, use the human-reviewed agreement_pct (not string-match)
            if has_consensus:
                agreement_pct = consensus_map[doc_id]

            # Dual-reviewer fields
            has_r1 = doc_id in r1_doc_ids
            has_r2 = doc_id in r2_doc_ids
            has_adjudication = doc_id in adjudication_set
            r1_r2_agreement_pct = None
            if has_adjudication:
                adj = adjudication_map[doc_id]
                r1_r2_agreement_pct = float(adj["agreement_pct"]) if adj.get("agreement_pct") is not None else None

            documents_out.append({
                "document_id": doc_id,
                "filename": doc["filename"],
                "has_ai": has_ai,
                "has_manual": has_manual,
                "has_consensus": has_consensus,
                "agreement_pct": agreement_pct,
                "disputed_fields": disputed_fields,
                "total_fields": total_fields,
                "has_r1": has_r1,
                "has_r2": has_r2,
                "has_adjudication": has_adjudication,
                "r1_r2_agreement_pct": r1_r2_agreement_pct,
            })

        # Aggregate stats
        ai_done = sum(1 for d in documents_out if d["has_ai"])
        manual_done = sum(1 for d in documents_out if d["has_manual"])
        consensus_done = sum(1 for d in documents_out if d["has_consensus"])
        r1_done = sum(1 for d in documents_out if d["has_r1"])
        r2_done = sum(1 for d in documents_out if d["has_r2"])
        adjudication_done = sum(1 for d in documents_out if d["has_adjudication"])
        agreements = [d["agreement_pct"] for d in documents_out if d["agreement_pct"] is not None]
        avg_agreement = round(sum(agreements) / len(agreements)) if agreements else None

        response = {
            "summary": {
                "total_docs": len(all_docs),
                "ai_done": ai_done,
                "manual_done": manual_done,
                "consensus_done": consensus_done,
                "avg_agreement_pct": avg_agreement,
                "r1_done": r1_done,
                "r2_done": r2_done,
                "adjudication_done": adjudication_done,
            },
            "documents": documents_out,
        }

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

        # Verify project belongs to user
        project_result = supabase.table("projects")\
            .select("id")\
            .eq("id", project_id)\
            .eq("user_id", str(user_id))\
            .execute()

        if not project_result.data:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")

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
            "created_by": str(user_id),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }

        if existing.data:
            result = supabase.table("consensus_results")\
                .update(payload)\
                .eq("id", existing.data[0]["id"])\
                .execute()
        else:
            result = supabase.table("consensus_results")\
                .insert(payload)\
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

        project_result = supabase.table("projects")\
            .select("id")\
            .eq("id", project_id)\
            .eq("user_id", str(user_id))\
            .execute()

        if not project_result.data:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")

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
            .select("extracted_data, extraction_id")\
            .eq("id", str(result_id))\
            .execute()

        if not result.data:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Result not found")

        extracted_data = result.data[0].get("extracted_data", {})

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
            .select("document_id")\
            .eq("id", str(result_id))\
            .execute()

        if not result.data:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Result not found")

        document_id = result.data[0]["document_id"]

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

        # Verify project belongs to user
        project_result = supabase.table("projects")\
            .select("id")\
            .eq("id", project_id)\
            .eq("user_id", str(user_id))\
            .execute()

        if not project_result.data:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Result not found"
            )

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

        project_result = supabase.table("projects")\
            .select("id")\
            .eq("id", project_id)\
            .eq("user_id", str(user_id))\
            .execute()

        if not project_result.data:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Result not found"
            )

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

        # Verify project belongs to user
        project_result = supabase.table("projects")\
            .select("id")\
            .eq("id", project_id)\
            .eq("user_id", str(user_id))\
            .execute()

        if not project_result.data:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Extraction not found"
            )

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

        # Load user export preferences and resolve format
        try:
            user_prefs = await get_user_settings(user_id)
        except Exception:
            user_prefs = {}
        export_format = format or user_prefs.get("export_format", "json")

        # Extract all extracted_data and apply preferences
        all_data = []
        for r in results.data:
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
