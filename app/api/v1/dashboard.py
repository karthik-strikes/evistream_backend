"""
Dashboard aggregated stats endpoint.
Returns all dashboard data in a single request, eliminating N+1 queries.
"""

import json

from fastapi import APIRouter, Depends, HTTPException, status, Query
from supabase import create_client
from uuid import UUID
from collections import Counter

import logging
from app.dependencies import get_current_user

logger = logging.getLogger(__name__)
from app.config import settings


router = APIRouter()

supabase = create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_KEY)


def _fmt_form_status(s: str) -> str:
    mapping = {
        "active": "Active",
        "generating": "Generating",
        "awaiting_review": "Review",
        "regenerating": "Generating",
        "draft": "Draft",
        "failed": "Failed",
    }
    return mapping.get(s, s)


@router.get("/stats")
async def get_dashboard_stats(
    project_id: UUID = Query(...),
    user_id: UUID = Depends(get_current_user),
):
    """
    Return aggregated dashboard statistics for a project in a single response.
    Eliminates N+1 queries from the frontend dashboard page.
    """
    try:
        pid = str(project_id)

        # 1. Document count
        doc_resp = (
            supabase.table("documents")
            .select("id", count="exact")
            .eq("project_id", pid)
            .execute()
        )
        doc_count = doc_resp.count or 0

        # 2. Form count + status breakdown + field counts
        form_resp = (
            supabase.table("forms")
            .select("id,status,form_name,fields")
            .eq("project_id", pid)
            .execute()
        )
        forms_data = form_resp.data or []
        form_count = len(forms_data)
        form_counts = dict(Counter(_fmt_form_status(f["status"]) for f in forms_data))
        def _fields_len(val) -> int:
            if val is None:
                return 0
            if isinstance(val, list):
                return len(val)
            if isinstance(val, str):
                try:
                    parsed = json.loads(val)
                    return len(parsed) if isinstance(parsed, list) else 0
                except Exception:
                    return 0
            return 0

        form_fields_count_map = {
            f["id"]: _fields_len(f.get("fields")) for f in forms_data
        }

        # 3. Extraction count + status breakdown
        ext_resp = (
            supabase.table("extractions")
            .select("id,status,form_id,created_at")
            .eq("project_id", pid)
            .order("created_at", desc=True)
            .execute()
        )
        extractions_data = ext_resp.data or []
        ext_count = len(extractions_data)
        extraction_status_counts = dict(Counter(e["status"] for e in extractions_data))

        # 4. Total results count (extraction_results has no project_id column;
        #    join through extraction_ids). Also track first document_id per extraction.
        extraction_ids = [e["id"] for e in extractions_data]
        total_results = 0
        result_count_map: dict = {}
        first_doc_map: dict = {}
        if extraction_ids:
            res_resp = (
                supabase.table("extraction_results")
                .select("id,extraction_id,document_id")
                .in_("extraction_id", extraction_ids)
                .execute()
            )
            results_data = res_resp.data or []
            total_results = len(results_data)
            result_count_map = dict(Counter(r["extraction_id"] for r in results_data))
            for r in results_data:
                eid = r["extraction_id"]
                if eid not in first_doc_map and r.get("document_id"):
                    first_doc_map[eid] = r["document_id"]

        # 4b. For the 5 recent extractions: fetch extracted_data to count filled fields,
        #     and resolve document filenames. Both done with targeted single queries.
        recent_ext_ids = [e["id"] for e in extractions_data[:5]]

        # Fetch first result row per recent extraction (for field fill count)
        def _extract_value(data) -> str:
            """Mirror the frontend extractValue() helper."""
            if data is None:
                return "—"
            if isinstance(data, str):
                return data
            if isinstance(data, (int, float, bool)):
                return str(data)
            if isinstance(data, dict) and "value" in data:
                v = str(data["value"]) if data["value"] is not None else "—"
                return v[:100] + "…" if len(v) > 100 else v
            if isinstance(data, list):
                return f"[{len(data)} items]"
            return str(data)[:100]

        fields_filled_map: dict = {}
        total_fields_map: dict = {}
        if recent_ext_ids:
            detail_resp = (
                supabase.table("extraction_results")
                .select("extraction_id,extracted_data")
                .in_("extraction_id", recent_ext_ids)
                .execute()
            )
            # Take first row per extraction, mirror allFieldNames + getCompleteness logic
            seen: set = set()
            for row in (detail_resp.data or []):
                eid = row["extraction_id"]
                if eid in seen:
                    continue
                seen.add(eid)
                data_blob = row.get("extracted_data") or {}
                if isinstance(data_blob, str):
                    try:
                        data_blob = json.loads(data_blob)
                    except Exception:
                        data_blob = {}
                field_keys = [
                    k for k in data_blob
                    if "source_text" not in k.lower() and "source text" not in k.lower()
                ]
                filled = sum(
                    1 for k in field_keys
                    if (v := _extract_value(data_blob[k])) and v not in ("—", "N/A")
                )
                fields_filled_map[eid] = filled
                total_fields_map[eid] = len(field_keys)

        doc_ids_needed = [
            first_doc_map[eid] for eid in recent_ext_ids if eid in first_doc_map
        ]
        doc_name_map: dict = {}
        if doc_ids_needed:
            docs_resp = (
                supabase.table("documents")
                .select("id,filename")
                .in_("id", doc_ids_needed)
                .execute()
            )
            doc_name_map = {
                d["id"]: d.get("filename", "") for d in (docs_resp.data or [])
            }

        # 5. Form names map (already have forms_data)
        form_name_map = {f["id"]: f.get("form_name", "") for f in forms_data}

        # 6. Recent 5 extractions (from already-fetched extractions_data)
        recent = []
        for e in extractions_data[:5]:
            first_doc_id = first_doc_map.get(e["id"])
            doc_name = (
                doc_name_map.get(first_doc_id, first_doc_id[:12] if first_doc_id else "—")
                if first_doc_id
                else "—"
            )
            recent.append(
                {
                    "id": e["id"],
                    "status": e["status"],
                    "form_id": e["form_id"],
                    "form_name": form_name_map.get(e["form_id"], ""),
                    "created_at": e["created_at"],
                    "result_count": result_count_map.get(e["id"], 0),
                    "doc_name": doc_name,
                    "fields_filled": fields_filled_map.get(e["id"], 0),
                    "total_fields": total_fields_map.get(e["id"], 0),
                }
            )

        # 7. Projects overview (all user projects with doc + form counts)
        projects_resp = (
            supabase.table("projects")
            .select("id,name,description,created_at")
            .eq("user_id", str(user_id))
            .order("created_at", desc=True)
            .execute()
        )
        all_projects = projects_resp.data or []

        # Batch-fetch counts for all projects in 2 queries (not 2N)
        all_project_ids = [proj["id"] for proj in all_projects]
        if all_project_ids:
            ov_docs = (
                supabase.table("documents")
                .select("project_id")
                .in_("project_id", all_project_ids)
                .execute()
            )
            ov_forms = (
                supabase.table("forms")
                .select("project_id")
                .in_("project_id", all_project_ids)
                .execute()
            )
            from collections import Counter as _Counter
            ov_docs_map = _Counter(r["project_id"] for r in (ov_docs.data or []))
            ov_forms_map = _Counter(r["project_id"] for r in (ov_forms.data or []))
        else:
            ov_docs_map = {}
            ov_forms_map = {}

        projects_overview = [
            {
                "id": proj["id"],
                "name": proj["name"],
                "description": proj.get("description", ""),
                "created_at": proj["created_at"],
                "document_count": ov_docs_map.get(proj["id"], 0),
                "form_count": ov_forms_map.get(proj["id"], 0),
            }
            for proj in all_projects
        ]

        return {
            "stats": {
                "documents": doc_count,
                "forms": form_count,
                "extractions": ext_count,
                "results": total_results,
            },
            "form_counts": form_counts,
            "extraction_status_counts": extraction_status_counts,
            "recent_extractions": recent,
            "projects_overview": projects_overview,
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred",
        )
