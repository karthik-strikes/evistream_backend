"""Service for adjudication of reviewer disagreements."""

import logging
from supabase import create_client, Client
from typing import Optional, Dict, Any, List
from uuid import UUID
from datetime import datetime, timezone

from app.config import settings
from app.services.audit_service import log_audit

logger = logging.getLogger(__name__)


def get_supabase() -> Client:
    return create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_KEY)


async def compare_reviewers(
    project_id: UUID,
    form_id: UUID,
    document_id: UUID,
) -> Dict[str, Any]:
    """Compare R1 vs R2 extraction results field-by-field."""
    supabase = get_supabase()

    # Get R1 and R2 results
    results = supabase.table("extraction_results")\
        .select("*")\
        .eq("document_id", str(document_id))\
        .eq("form_id", str(form_id))\
        .eq("extraction_type", "manual")\
        .in_("reviewer_role", ["reviewer_1", "reviewer_2"])\
        .execute()

    r1_data = {}
    r2_data = {}
    r1_result_id = None
    r2_result_id = None
    r1_user_id = None
    r2_user_id = None

    for r in (results.data or []):
        if r.get("reviewer_role") == "reviewer_1":
            r1_data = r.get("extracted_data", {})
            r1_result_id = r["id"]
            r1_user_id = r.get("extracted_by")
        elif r.get("reviewer_role") == "reviewer_2":
            r2_data = r.get("extracted_data", {})
            r2_result_id = r["id"]
            r2_user_id = r.get("extracted_by")

    # Also get AI result for reference
    ai_result = supabase.table("extraction_results")\
        .select("extracted_data")\
        .eq("document_id", str(document_id))\
        .eq("form_id", str(form_id))\
        .eq("extraction_type", "ai")\
        .limit(1)\
        .execute()
    ai_data = ai_result.data[0].get("extracted_data", {}) if ai_result.data else {}

    # Normalize AI keys (strip .value suffix)
    ai_normalized = {}
    for k, v in ai_data.items():
        if k.endswith(".value"):
            ai_normalized[k[:-6]] = v
    if not ai_normalized:
        ai_normalized = ai_data

    # Get reviewer names
    user_ids = [uid for uid in [r1_user_id, r2_user_id] if uid]
    user_map = {}
    if user_ids:
        users = supabase.table("users")\
            .select("id, full_name, email")\
            .in_("id", user_ids)\
            .execute()
        user_map = {u["id"]: u.get("full_name") or u["email"] for u in (users.data or [])}

    # Build field comparison
    all_fields = sorted(set(list(r1_data.keys()) + list(r2_data.keys())))
    fields = []
    agreed = 0
    disagreed = 0

    for field in all_fields:
        r1_val = r1_data.get(field)
        r2_val = r2_data.get(field)
        is_agreed = (
            str(r1_val).strip().lower() == str(r2_val).strip().lower()
            if r1_val is not None and r2_val is not None
            else r1_val == r2_val
        )

        if is_agreed:
            agreed += 1
        else:
            disagreed += 1

        fields.append({
            "field_name": field,
            "reviewer_1_value": r1_val,
            "reviewer_2_value": r2_val,
            "agreed": is_agreed,
            "ai_value": ai_normalized.get(field),
        })

    total = len(all_fields)

    return {
        "document_id": str(document_id),
        "form_id": str(form_id),
        "reviewer_1": {
            "user_id": r1_user_id,
            "full_name": user_map.get(r1_user_id, "Reviewer 1"),
            "result_id": r1_result_id,
        },
        "reviewer_2": {
            "user_id": r2_user_id,
            "full_name": user_map.get(r2_user_id, "Reviewer 2"),
            "result_id": r2_result_id,
        },
        "fields": fields,
        "statistics": {
            "agreed": agreed,
            "disagreed": disagreed,
            "total": total,
            "agreement_pct": round(agreed / total * 100, 2) if total > 0 else 0,
        },
    }


async def save_adjudication(
    project_id: UUID,
    form_id: UUID,
    document_id: UUID,
    adjudicator_id: UUID,
    field_resolutions: Dict[str, Any],
    reviewer_1_result_id: Optional[UUID] = None,
    reviewer_2_result_id: Optional[UUID] = None,
    status: str = "in_progress",
) -> Dict[str, Any]:
    """Save or update adjudication decisions."""
    supabase = get_supabase()

    # Count agreed/disagreed
    agreed_count = sum(1 for f in field_resolutions.values() if f.get("agreed", False))
    total_fields = len(field_resolutions)
    disagreed_count = total_fields - agreed_count
    agreement_pct = round(agreed_count / total_fields * 100, 2) if total_fields > 0 else 0

    payload = {
        "project_id": str(project_id),
        "form_id": str(form_id),
        "document_id": str(document_id),
        "adjudicator_id": str(adjudicator_id),
        "reviewer_1_result_id": str(reviewer_1_result_id) if reviewer_1_result_id else None,
        "reviewer_2_result_id": str(reviewer_2_result_id) if reviewer_2_result_id else None,
        "field_resolutions": field_resolutions,
        "agreed_count": agreed_count,
        "disagreed_count": disagreed_count,
        "total_fields": total_fields,
        "agreement_pct": agreement_pct,
        "status": status,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }

    # Check for existing
    existing = supabase.table("adjudication_results")\
        .select("id, updated_at")\
        .eq("project_id", str(project_id))\
        .eq("form_id", str(form_id))\
        .eq("document_id", str(document_id))\
        .limit(1)\
        .execute()

    if existing.data:
        result = supabase.table("adjudication_results")\
            .update(payload)\
            .eq("id", existing.data[0]["id"])\
            .execute()
    else:
        result = supabase.table("adjudication_results")\
            .insert(payload)\
            .execute()

    saved = result.data[0] if result.data else {}

    # Log audit for each field resolution
    if saved:
        await log_audit(
            user_id=adjudicator_id,
            entity_type="adjudication_result",
            entity_id=UUID(saved["id"]),
            action="saved" if not existing.data else "updated",
            project_id=project_id,
            metadata={"status": status, "agreement_pct": agreement_pct},
        )

    return saved


async def get_adjudication(
    project_id: UUID,
    form_id: UUID,
    document_id: UUID,
) -> Optional[Dict[str, Any]]:
    """Get existing adjudication for a document."""
    supabase = get_supabase()
    result = supabase.table("adjudication_results")\
        .select("*")\
        .eq("project_id", str(project_id))\
        .eq("form_id", str(form_id))\
        .eq("document_id", str(document_id))\
        .limit(1)\
        .execute()
    return result.data[0] if result.data else None


async def get_adjudication_summary(
    project_id: UUID,
    form_id: UUID,
) -> Dict[str, Any]:
    """Get adjudication progress for a project+form."""
    supabase = get_supabase()

    # Count assignments needing adjudication (both R1 and R2 completed)
    r1_done = supabase.table("review_assignments")\
        .select("document_id")\
        .eq("project_id", str(project_id))\
        .eq("form_id", str(form_id))\
        .eq("reviewer_role", "reviewer_1")\
        .eq("status", "completed")\
        .execute()
    r1_doc_ids = set(a["document_id"] for a in (r1_done.data or []))

    r2_done = supabase.table("review_assignments")\
        .select("document_id")\
        .eq("project_id", str(project_id))\
        .eq("form_id", str(form_id))\
        .eq("reviewer_role", "reviewer_2")\
        .eq("status", "completed")\
        .execute()
    r2_doc_ids = set(a["document_id"] for a in (r2_done.data or []))

    ready_for_adjudication = r1_doc_ids & r2_doc_ids

    # Get completed adjudications
    adjudications = supabase.table("adjudication_results")\
        .select("document_id, status, agreement_pct")\
        .eq("project_id", str(project_id))\
        .eq("form_id", str(form_id))\
        .execute()
    adj_data = adjudications.data or []

    completed = [a for a in adj_data if a["status"] == "completed"]
    in_progress = [a for a in adj_data if a["status"] == "in_progress"]
    adj_doc_ids = set(a["document_id"] for a in adj_data)

    pending_doc_ids = ready_for_adjudication - adj_doc_ids

    agreements = [a["agreement_pct"] for a in completed if a["agreement_pct"] is not None]

    return {
        "ready_for_adjudication": len(ready_for_adjudication),
        "pending": len(pending_doc_ids),
        "in_progress": len(in_progress),
        "completed": len(completed),
        "avg_agreement_pct": round(sum(agreements) / len(agreements), 2) if agreements else None,
    }
