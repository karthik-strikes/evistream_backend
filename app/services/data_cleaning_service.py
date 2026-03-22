"""Service for data cleaning grid, validation, and bulk edits."""

import logging
import re
from supabase import create_client, Client
from typing import Optional, Dict, Any, List
from uuid import UUID
from datetime import datetime, timezone

from app.config import settings
from app.services.audit_service import log_audit

logger = logging.getLogger(__name__)


def get_supabase() -> Client:
    return create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_KEY)


async def get_grid_data(
    project_id: UUID,
    form_id: UUID,
) -> List[Dict[str, Any]]:
    """
    Build the data cleaning grid: all docs x fields with their best available values.
    Priority: adjudicated > reviewer_1 manual > ai
    """
    supabase = get_supabase()

    # Get all documents
    docs = supabase.table("documents")\
        .select("id, filename")\
        .eq("project_id", str(project_id))\
        .eq("processing_status", "completed")\
        .order("created_at")\
        .execute()

    if not docs.data:
        return []

    doc_ids = [d["id"] for d in docs.data]
    doc_map = {d["id"]: d["filename"] for d in docs.data}

    # Get adjudication results
    adj_results = supabase.table("adjudication_results")\
        .select("document_id, field_resolutions, status")\
        .eq("project_id", str(project_id))\
        .eq("form_id", str(form_id))\
        .eq("status", "completed")\
        .execute()
    adj_map = {a["document_id"]: a["field_resolutions"] for a in (adj_results.data or [])}

    # Get extraction results
    ext_results = supabase.table("extraction_results")\
        .select("document_id, extracted_data, extraction_type, reviewer_role")\
        .eq("project_id", str(project_id))\
        .eq("form_id", str(form_id))\
        .execute()

    # Group by document
    ext_by_doc: Dict[str, Dict[str, Any]] = {}
    for r in (ext_results.data or []):
        doc_id = r["document_id"]
        if doc_id not in ext_by_doc:
            ext_by_doc[doc_id] = {}
        key = r.get("reviewer_role") or r.get("extraction_type", "ai")
        ext_by_doc[doc_id][key] = r.get("extracted_data", {})

    # Get validation rules for this form
    violations_by_doc = await _validate_all(project_id, form_id, doc_ids, adj_map, ext_by_doc)

    # Build grid rows
    rows = []
    for doc_id in doc_ids:
        # Determine best data source
        if doc_id in adj_map:
            # Use adjudicated values
            resolutions = adj_map[doc_id]
            values = {k: v.get("final_value") for k, v in resolutions.items() if isinstance(v, dict)}
            data_source = "adjudicated"
        elif doc_id in ext_by_doc:
            doc_ext = ext_by_doc[doc_id]
            if "reviewer_1" in doc_ext:
                values = doc_ext["reviewer_1"]
                data_source = "reviewer_1"
            elif "manual" in doc_ext:
                values = doc_ext.get("manual", {})
                data_source = "manual"
            else:
                ai_data = doc_ext.get("ai", {})
                values = {}
                for k, v in ai_data.items():
                    if k.endswith(".value"):
                        values[k[:-6]] = v
                if not values:
                    values = ai_data
                data_source = "ai"
        else:
            values = {}
            data_source = "ai"

        rows.append({
            "document_id": doc_id,
            "filename": doc_map.get(doc_id, ""),
            "data_source": data_source,
            "values": values,
            "violations": violations_by_doc.get(doc_id, []),
        })

    return rows


async def _validate_all(
    project_id: UUID,
    form_id: UUID,
    doc_ids: List[str],
    adj_map: Dict[str, Any],
    ext_by_doc: Dict[str, Dict[str, Any]],
) -> Dict[str, List[Dict[str, Any]]]:
    """Run all validation rules against all documents."""
    supabase = get_supabase()

    rules = supabase.table("validation_rules")\
        .select("*")\
        .eq("form_id", str(form_id))\
        .eq("is_active", True)\
        .execute()

    all_rules = rules.data or []
    if not all_rules:
        return {}

    violations: Dict[str, List[Dict[str, Any]]] = {}

    for doc_id in doc_ids:
        doc_violations = []

        # Get the best values for this doc
        if doc_id in adj_map:
            resolutions = adj_map[doc_id]
            values = {k: v.get("final_value") for k, v in resolutions.items() if isinstance(v, dict)}
        elif doc_id in ext_by_doc:
            doc_ext = ext_by_doc[doc_id]
            values = doc_ext.get("reviewer_1") or doc_ext.get("manual") or doc_ext.get("ai", {})
        else:
            values = {}

        for rule in all_rules:
            field_name = rule["field_name"]
            value = values.get(field_name)
            violation = _check_rule(rule, value, values)
            if violation:
                doc_violations.append(violation)

        if doc_violations:
            violations[doc_id] = doc_violations

    return violations


def _check_rule(
    rule: Dict[str, Any],
    value: Any,
    all_values: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """Check a single validation rule against a value."""
    rule_type = rule["rule_type"]
    config = rule.get("rule_config", {})
    field_name = rule["field_name"]

    if rule_type == "required":
        if value is None or (isinstance(value, str) and not value.strip()):
            return {
                "field_name": field_name,
                "rule_id": rule["id"],
                "severity": rule["severity"],
                "message": rule["message"],
            }

    elif rule_type == "range":
        if value is not None:
            try:
                num_val = float(value)
                min_val = config.get("min")
                max_val = config.get("max")
                if min_val is not None and num_val < float(min_val):
                    return {
                        "field_name": field_name,
                        "rule_id": rule["id"],
                        "severity": rule["severity"],
                        "message": rule["message"],
                    }
                if max_val is not None and num_val > float(max_val):
                    return {
                        "field_name": field_name,
                        "rule_id": rule["id"],
                        "severity": rule["severity"],
                        "message": rule["message"],
                    }
            except (ValueError, TypeError):
                pass

    elif rule_type == "regex":
        pattern = config.get("pattern", "")
        if value is not None and isinstance(value, str):
            if not re.match(pattern, value):
                return {
                    "field_name": field_name,
                    "rule_id": rule["id"],
                    "severity": rule["severity"],
                    "message": rule["message"],
                }

    elif rule_type == "format":
        fmt = config.get("format", "")
        if value is not None and isinstance(value, str):
            if fmt == "email" and "@" not in value:
                return {
                    "field_name": field_name,
                    "rule_id": rule["id"],
                    "severity": rule["severity"],
                    "message": rule["message"],
                }
            elif fmt == "date":
                try:
                    datetime.fromisoformat(value)
                except ValueError:
                    return {
                        "field_name": field_name,
                        "rule_id": rule["id"],
                        "severity": rule["severity"],
                        "message": rule["message"],
                    }

    elif rule_type == "cross_field":
        dependent_field = config.get("depends_on")
        condition = config.get("condition")
        if dependent_field and condition == "required_if_present":
            dep_value = all_values.get(dependent_field)
            if dep_value is not None and (value is None or (isinstance(value, str) and not value.strip())):
                return {
                    "field_name": field_name,
                    "rule_id": rule["id"],
                    "severity": rule["severity"],
                    "message": rule["message"],
                }

    return None


async def validate_data(
    project_id: UUID,
    form_id: UUID,
) -> List[Dict[str, Any]]:
    """Run validation rules and return all violations."""
    grid = await get_grid_data(project_id, form_id)
    all_violations = []
    for row in grid:
        for v in row.get("violations", []):
            v["document_id"] = row["document_id"]
            v["filename"] = row["filename"]
            all_violations.append(v)
    return all_violations


async def bulk_edit(
    project_id: UUID,
    form_id: UUID,
    edits: List[Dict[str, Any]],
    user_id: UUID,
) -> Dict[str, Any]:
    """Apply batch cell edits with audit trail."""
    supabase = get_supabase()
    updated = 0
    errors = []

    for edit in edits:
        doc_id = str(edit["document_id"])
        field_name = edit["field_name"]
        old_value = edit.get("old_value")
        new_value = edit["new_value"]

        try:
            # Find the result to edit (prefer adjudicated, then manual, then ai)
            adj = supabase.table("adjudication_results")\
                .select("id, field_resolutions")\
                .eq("project_id", str(project_id))\
                .eq("form_id", str(form_id))\
                .eq("document_id", doc_id)\
                .limit(1)\
                .execute()

            if adj.data:
                # Edit adjudication result
                resolutions = adj.data[0].get("field_resolutions", {})
                if field_name in resolutions:
                    resolutions[field_name]["final_value"] = new_value
                else:
                    resolutions[field_name] = {
                        "final_value": new_value,
                        "resolution_source": "custom",
                        "agreed": False,
                    }
                supabase.table("adjudication_results")\
                    .update({
                        "field_resolutions": resolutions,
                        "updated_at": datetime.now(timezone.utc).isoformat(),
                    })\
                    .eq("id", adj.data[0]["id"])\
                    .execute()
                entity_id = UUID(adj.data[0]["id"])
                entity_type = "adjudication_result"
            else:
                # Edit extraction result
                ext = supabase.table("extraction_results")\
                    .select("id, extracted_data")\
                    .eq("project_id", str(project_id))\
                    .eq("form_id", str(form_id))\
                    .eq("document_id", doc_id)\
                    .order("created_at", desc=True)\
                    .limit(1)\
                    .execute()

                if ext.data:
                    extracted = ext.data[0].get("extracted_data", {})
                    extracted[field_name] = new_value
                    supabase.table("extraction_results")\
                        .update({"extracted_data": extracted})\
                        .eq("id", ext.data[0]["id"])\
                        .execute()
                    entity_id = UUID(ext.data[0]["id"])
                    entity_type = "extraction_result"
                else:
                    errors.append({"document_id": doc_id, "field": field_name, "error": "No result found"})
                    continue

            await log_audit(
                user_id=user_id,
                entity_type=entity_type,
                entity_id=entity_id,
                action="bulk_edit",
                project_id=project_id,
                field_name=field_name,
                old_value=old_value,
                new_value=new_value,
            )
            updated += 1

        except Exception as e:
            logger.exception("Bulk edit error for doc %s field %s", doc_id, field_name)
            errors.append({"document_id": doc_id, "field": field_name, "error": str(e)})

    return {"updated": updated, "errors": errors}


# ── Validation Rule CRUD ──

async def list_rules(form_id: UUID) -> List[Dict[str, Any]]:
    supabase = get_supabase()
    result = supabase.table("validation_rules")\
        .select("*")\
        .eq("form_id", str(form_id))\
        .order("created_at")\
        .execute()
    return result.data or []


async def create_rule(
    form_id: UUID,
    field_name: str,
    rule_type: str,
    rule_config: Dict[str, Any],
    severity: str,
    message: str,
    created_by: UUID,
) -> Dict[str, Any]:
    supabase = get_supabase()
    result = supabase.table("validation_rules").insert({
        "form_id": str(form_id),
        "field_name": field_name,
        "rule_type": rule_type,
        "rule_config": rule_config,
        "severity": severity,
        "message": message,
        "created_by": str(created_by),
    }).execute()
    return result.data[0] if result.data else {}


async def update_rule(
    rule_id: UUID,
    updates: Dict[str, Any],
) -> Dict[str, Any]:
    supabase = get_supabase()
    result = supabase.table("validation_rules")\
        .update(updates)\
        .eq("id", str(rule_id))\
        .execute()
    return result.data[0] if result.data else {}


async def delete_rule(rule_id: UUID) -> bool:
    supabase = get_supabase()
    supabase.table("validation_rules")\
        .delete()\
        .eq("id", str(rule_id))\
        .execute()
    return True
