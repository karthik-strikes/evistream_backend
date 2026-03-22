"""Data cleaning endpoints."""

import logging
import json
import csv
import io
from fastapi import APIRouter, Depends, HTTPException, status, Query, Response
from uuid import UUID
from typing import Optional, List

from app.dependencies import get_current_user
from app.services.project_access import check_project_access
from app.services import data_cleaning_service
from app.models.schemas import (
    BulkEditRequest, ValidationRuleCreate, ValidationRuleUpdate,
    ValidationRuleResponse,
)

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/grid")
async def get_grid(
    project_id: UUID = Query(...),
    form_id: UUID = Query(...),
    user_id: UUID = Depends(get_current_user),
):
    """Get data cleaning grid (all docs x fields with violations)."""
    await check_project_access(project_id, user_id, "can_view_results")
    return await data_cleaning_service.get_grid_data(project_id, form_id)


@router.post("/validate")
async def validate_data(
    project_id: UUID = Query(...),
    form_id: UUID = Query(...),
    user_id: UUID = Depends(get_current_user),
):
    """Run validation rules and return violations."""
    await check_project_access(project_id, user_id, "can_view_results")
    return await data_cleaning_service.validate_data(project_id, form_id)


@router.post("/bulk-edit")
async def bulk_edit(
    data: BulkEditRequest,
    user_id: UUID = Depends(get_current_user),
):
    """Batch cell edits with audit trail."""
    await check_project_access(data.project_id, user_id, "can_view_results")
    edits = [e.model_dump() for e in data.edits]
    return await data_cleaning_service.bulk_edit(
        project_id=data.project_id,
        form_id=data.form_id,
        edits=edits,
        user_id=user_id,
    )


@router.get("/rules", response_model=List[ValidationRuleResponse])
async def list_rules(
    form_id: UUID = Query(...),
    user_id: UUID = Depends(get_current_user),
):
    """List validation rules for a form."""
    result = await data_cleaning_service.list_rules(form_id)
    return [ValidationRuleResponse(**r) for r in result]


@router.post("/rules", response_model=ValidationRuleResponse, status_code=status.HTTP_201_CREATED)
async def create_rule(
    data: ValidationRuleCreate,
    user_id: UUID = Depends(get_current_user),
):
    """Create a validation rule."""
    result = await data_cleaning_service.create_rule(
        form_id=data.form_id,
        field_name=data.field_name,
        rule_type=data.rule_type.value,
        rule_config=data.rule_config,
        severity=data.severity,
        message=data.message,
        created_by=user_id,
    )
    return ValidationRuleResponse(**result)


@router.put("/rules/{rule_id}", response_model=ValidationRuleResponse)
async def update_rule(
    rule_id: UUID,
    data: ValidationRuleUpdate,
    user_id: UUID = Depends(get_current_user),
):
    """Update a validation rule."""
    updates = data.model_dump(exclude_unset=True)
    result = await data_cleaning_service.update_rule(rule_id, updates)
    if not result:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Rule not found")
    return ValidationRuleResponse(**result)


@router.delete("/rules/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_rule(
    rule_id: UUID,
    user_id: UUID = Depends(get_current_user),
):
    """Delete a validation rule."""
    await data_cleaning_service.delete_rule(rule_id)


@router.get("/export")
async def export_clean_data(
    project_id: UUID = Query(...),
    form_id: UUID = Query(...),
    format: str = Query("csv", pattern="^(csv|json)$"),
    user_id: UUID = Depends(get_current_user),
):
    """Export clean data with audit sidecar."""
    await check_project_access(project_id, user_id, "can_view_results")

    grid = await data_cleaning_service.get_grid_data(project_id, form_id)

    if format == "json":
        export_data = []
        for row in grid:
            export_data.append({
                "document_id": row["document_id"],
                "filename": row["filename"],
                "data_source": row["data_source"],
                "values": row["values"],
                "violations": row["violations"],
            })
        return Response(
            content=json.dumps(export_data, indent=2),
            media_type="application/json",
            headers={"Content-Disposition": "attachment; filename=clean_data_export.json"},
        )
    else:
        # CSV export
        if not grid:
            return Response(content="", media_type="text/csv")

        # Collect all field names
        all_fields = set()
        for row in grid:
            all_fields.update(row.get("values", {}).keys())
        fieldnames = ["document_id", "filename", "data_source"] + sorted(all_fields) + ["violations"]

        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=fieldnames)
        writer.writeheader()
        for row in grid:
            csv_row = {
                "document_id": row["document_id"],
                "filename": row["filename"],
                "data_source": row["data_source"],
            }
            for f in sorted(all_fields):
                csv_row[f] = row.get("values", {}).get(f, "")
            csv_row["violations"] = json.dumps(row.get("violations", []))
            writer.writerow(csv_row)

        return Response(
            content=output.getvalue(),
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=clean_data_export.csv"},
        )
