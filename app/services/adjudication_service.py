"""Service for adjudication of reviewer disagreements."""

import json
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


_METADATA_KEYS = frozenset({
    "source_text", "source_location", "page", "section", "confidence", "reasoning",
    "status", "error",
})


def _unwrap_for_compare(v: Any) -> Any:
    """Recursively strip {value, source_text} envelopes and grounding metadata so
    two reviewers with identical column values but different cited quotes are
    treated as agreement, not conflict. Mirrors the rule that source_text is
    metadata about a value — never the value itself."""
    if isinstance(v, dict):
        if "value" in v:
            return _unwrap_for_compare(v["value"])
        # Row dict inside a table: drop metadata keys, recurse on the rest.
        return {k: _unwrap_for_compare(val) for k, val in v.items() if k not in _METADATA_KEYS}
    if isinstance(v, list):
        return [_unwrap_for_compare(x) for x in v]
    return v


def _canon(v: Any) -> Any:
    """Canonicalize an unwrapped value for agreement checks: case/whitespace-
    insensitive strings, numeric strings equal to numbers ("3" == 3), and table
    row lists compared as order-insensitive multisets — the same leniency
    scalar fields already get, so row order never counts as a conflict."""
    if isinstance(v, bool):
        # Compare as the lowercase string so True == "true" (manual reviewers
        # save strings; AI may save real booleans).
        return "true" if v else "false"
    if isinstance(v, str):
        s = v.strip().lower()
        # Boolean synonyms — mirror the consensus UI's displayBoolean, which
        # renders yes/true/y as "Yes": values that display identically must
        # never count as a conflict.
        if s in ("true", "yes", "y"):
            return "true"
        if s in ("false", "no"):
            return "false"
        try:
            f = float(s)
            if f == f and f not in (float("inf"), float("-inf")):
                return f
        except ValueError:
            pass
        return s
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, dict):
        return {k: _canon(val) for k, val in v.items()}
    if isinstance(v, list):
        items = [_canon(x) for x in v]
        try:
            return sorted(items, key=lambda x: json.dumps(x, sort_keys=True, default=str))
        except TypeError:
            return items
    return v


def _align_multiselect(a: Any, b: Any):
    """When one side saved a multi-select as a list and the other as a
    comma-joined string, split the string so both compare as multisets."""
    def _split(s: str) -> list:
        return [p.strip() for p in s.split(",") if p.strip()]

    def _scalar_list(x) -> bool:
        return isinstance(x, list) and all(not isinstance(i, (dict, list)) for i in x)

    if _scalar_list(a) and isinstance(b, str):
        return a, _split(b)
    if _scalar_list(b) and isinstance(a, str):
        return _split(a), b
    return a, b


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
        # Compare on unwrapped values so per-cell / per-field source_text
        # differences never count as conflicts — only divergent values do.
        r1_norm = _unwrap_for_compare(r1_val)
        r2_norm = _unwrap_for_compare(r2_val)
        # Same leniency at every depth: case/whitespace-insensitive, "3" == 3,
        # boolean synonyms, list-vs-comma-string multi-selects, and table rows
        # in a different order still count as agreement.
        r1_norm, r2_norm = _align_multiselect(r1_norm, r2_norm)
        is_agreed = _canon(r1_norm) == _canon(r2_norm)

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
