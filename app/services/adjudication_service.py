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


from utils import value_compare

# The comparison rules used to live here, as the most complete of the codebase's
# four independent agreement implementations. They now live in
# `utils/value_compare.py` and serve all four surfaces, so "did these agree?" has
# one answer.
#
# These five names are re-exported because
# `tests/test_services/test_adjudication_absence.py` imports them by name and
# pins their behaviour. New code should call `value_compare.agreement()` — it
# returns the tri-state verdict, so an incomparable pair (a failed extraction, or
# nothing recorded) can be told apart from a genuine conflict instead of being
# folded into one by a boolean.
_METADATA_KEYS = value_compare._METADATA_KEYS
_Failed = value_compare._Failed
_FAILED_SENTINEL = value_compare.FAILED_SENTINEL
_unwrap_for_compare = value_compare.unwrap_for_compare
_canon = value_compare.canon
_align_multiselect = value_compare.align_multiselect


async def compare_reviewers(
    project_id: UUID,
    form_id: UUID,
    document_id: UUID,
    requesting_user_id: Optional[UUID] = None,
) -> Dict[str, Any]:
    """Compare R1 vs R2 extraction results field-by-field."""
    supabase = get_supabase()

    # Get R1 and R2 results — order by created_at asc so oldest (canonical) row wins
    # if duplicates exist from a role-swap scenario.
    results = supabase.table("extraction_results")\
        .select("*")\
        .eq("document_id", str(document_id))\
        .eq("form_id", str(form_id))\
        .eq("extraction_type", "manual")\
        .in_("reviewer_role", ["reviewer_1", "reviewer_2"])\
        .order("created_at", desc=False)\
        .execute()

    r1_data = {}
    r2_data = {}
    r1_result_id = None
    r2_result_id = None
    r1_user_id = None
    r2_user_id = None

    for r in (results.data or []):
        if r.get("reviewer_role") == "reviewer_1" and r1_result_id is None:
            r1_data = r.get("extracted_data", {})
            r1_result_id = r["id"]
            r1_user_id = r.get("extracted_by")
        elif r.get("reviewer_role") == "reviewer_2" and r2_result_id is None:
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

    # Normalize AI keys (strip .value suffix). Shared with the consensus summary
    # and the review screen so all three count the same set of fields.
    ai_normalized = value_compare.normalize_ai_keys(ai_data)

    # Get reviewer names
    user_ids = [uid for uid in [r1_user_id, r2_user_id] if uid]
    user_map = {}
    if user_ids:
        users = supabase.table("users")\
            .select("id, full_name, email")\
            .in_("id", user_ids)\
            .execute()
        user_map = {u["id"]: u.get("full_name") or u["email"] for u in (users.data or [])}

    # Build field comparison. `comparable_fields` is what keeps the `_partial`
    # control flag — stored inside extracted_data by api/v1/results.py — out of
    # the field list, where it inflated the total and always read as a conflict.
    all_fields = sorted(value_compare.comparable_fields(r1_data, r2_data))
    fields = []
    agreed = 0
    disagreed = 0

    for field in all_fields:
        r1_val = r1_data.get(field)
        r2_val = r2_data.get(field)
        # One shared comparator: source_text differences never count as
        # conflicts, and the same leniency applies at every depth —
        # case/whitespace-insensitive, "3" == 3, boolean synonyms,
        # list-vs-comma-string multi-selects, and table rows in a different
        # order still count as agreement.
        verdict = value_compare.agreement(r1_val, r2_val)
        is_agreed = verdict == value_compare.AGREE

        if is_agreed:
            agreed += 1
        else:
            disagreed += 1

        fields.append({
            "field_name": field,
            "reviewer_1_value": r1_val,
            "reviewer_2_value": r2_val,
            "agreed": is_agreed,
            # An incomparable pair still needs a human, so it counts as
            # disagreed above — but say WHY, so the UI can distinguish "the two
            # reviewers differ" from "one side has no answer to compare".
            "comparable": verdict != value_compare.INCOMPARABLE,
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
            "self_authored": bool(requesting_user_id and r1_user_id and str(requesting_user_id) == str(r1_user_id)),
        },
        "reviewer_2": {
            "user_id": r2_user_id,
            "full_name": user_map.get(r2_user_id, "Reviewer 2"),
            "result_id": r2_result_id,
            "self_authored": bool(requesting_user_id and r2_user_id and str(requesting_user_id) == str(r2_user_id)),
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

    # Invalidate the consensus dashboard's 60s cache, the same way
    # api/v1/results.py does on save_consensus and save_manual_extraction. Only
    # this path was missing it, so an adjudication saved on its own left the
    # dashboard showing stale has_adjudication / done counts for up to a minute.
    # Imported here, not at module scope: cache_service builds a Redis client on
    # import, and this module is imported by the test suite.
    try:
        from app.services.cache_service import cache_service
        cache_service.delete(f"consensus_summary:{project_id}:{form_id}")
    except Exception:
        logger.warning(
            "Failed to invalidate consensus summary cache for project=%s form=%s",
            project_id, form_id, exc_info=True,
        )

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
