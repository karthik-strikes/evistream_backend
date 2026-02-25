"""
Extraction results endpoints - View and export extraction results.
"""

from fastapi import APIRouter, Depends, HTTPException, status, Query, Response
from fastapi.responses import StreamingResponse
from supabase import create_client
from uuid import UUID
from typing import List, Optional
import json
import csv
import io

from app.dependencies import get_current_user
from app.config import settings
from app.models.schemas import ExtractionResultResponse
from pydantic import BaseModel
from typing import Dict, Any


router = APIRouter()

# Initialize Supabase client
supabase = create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)


@router.get("", response_model=List[ExtractionResultResponse])
async def list_results(
    extraction_id: Optional[UUID] = Query(None),
    project_id: Optional[UUID] = Query(None),
    form_id: Optional[UUID] = Query(None),
    document_id: Optional[UUID] = Query(None),
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

            result = query.order("created_at", desc=True).execute()

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

            result = query.order("created_at", desc=True).execute()

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

            result = query.order("created_at", desc=True).execute()

        results = result.data or []
        return [ExtractionResultResponse(**r) for r in results]

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error listing results: {str(e)}"
        )


class ManualExtractionCreate(BaseModel):
    """Manual extraction submission."""
    document_id: UUID
    form_id: UUID
    extracted_data: Dict[str, Any]
    extraction_type: str = "manual"


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

        # Find or create a manual extraction record
        extraction_result = supabase.table("extractions")\
            .select("id")\
            .eq("project_id", project_id)\
            .eq("form_id", str(data.form_id))\
            .eq("status", "manual")\
            .limit(1)\
            .execute()

        if extraction_result.data:
            extraction_id = extraction_result.data[0]["id"]
        else:
            new_extraction = supabase.table("extractions").insert({
                "project_id": project_id,
                "form_id": str(data.form_id),
                "status": "manual"
            }).execute()

            if not new_extraction.data:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Failed to create extraction record"
                )
            extraction_id = new_extraction.data[0]["id"]

        # Insert extraction result
        result_data = {
            "extraction_id": extraction_id,
            "project_id": project_id,
            "form_id": str(data.form_id),
            "document_id": str(data.document_id),
            "extracted_data": {
                **data.extracted_data,
                "_extraction_type": data.extraction_type
            }
        }

        result = supabase.table("extraction_results").insert(result_data).execute()

        if not result.data:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to save manual extraction"
            )

        return ExtractionResultResponse(**result.data[0])

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error saving manual extraction: {str(e)}"
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
            extraction_type = extracted.get("_extraction_type", "ai")

            if extraction_type == "manual":
                if not manual_data:  # Use most recent
                    manual_data = {k: v for k, v in extracted.items() if not k.startswith("_")}
            else:
                if not ai_data:  # Use most recent
                    ai_data = {k: v for k, v in extracted.items() if not k.startswith("_")}

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
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error comparing results: {str(e)}"
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
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error getting result: {str(e)}"
        )


@router.get("/{result_id}/export")
async def export_result(
    result_id: UUID,
    format: str = Query("json", pattern="^(json|csv)$"),
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

        # Get extracted data
        extracted_data = extraction_result.get("extracted_data", {})

        if format == "json":
            # Return as JSON
            return Response(
                content=json.dumps(extracted_data, indent=2),
                media_type="application/json",
                headers={
                    "Content-Disposition": f"attachment; filename=result_{result_id}.json"
                }
            )

        elif format == "csv":
            # Flatten nested data and convert to CSV
            def flatten_dict(d, parent_key='', sep='_'):
                """Flatten nested dictionary."""
                items = []
                for k, v in d.items():
                    new_key = f"{parent_key}{sep}{k}" if parent_key else k
                    if isinstance(v, dict):
                        items.extend(flatten_dict(v, new_key, sep=sep).items())
                    elif isinstance(v, list):
                        # Convert list to comma-separated string
                        items.append((new_key, ', '.join(map(str, v))))
                    else:
                        items.append((new_key, v))
                return dict(items)

            # If extracted_data is a list, flatten each item
            if isinstance(extracted_data, list):
                flattened_data = [flatten_dict(item) for item in extracted_data]
            else:
                flattened_data = [flatten_dict(extracted_data)]

            # Create CSV
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
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error exporting result: {str(e)}"
        )


@router.get("/extraction/{extraction_id}/export")
async def export_extraction_results(
    extraction_id: UUID,
    format: str = Query("json", pattern="^(json|csv)$"),
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

        # Extract all extracted_data
        all_data = [r.get("extracted_data", {}) for r in results.data]

        if format == "json":
            # Return as JSON array
            return Response(
                content=json.dumps(all_data, indent=2),
                media_type="application/json",
                headers={
                    "Content-Disposition": f"attachment; filename=extraction_{extraction_id}_results.json"
                }
            )

        elif format == "csv":
            # Flatten all results and combine into one CSV
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

            # Flatten all items
            flattened_data = []
            for item in all_data:
                if isinstance(item, list):
                    flattened_data.extend([flatten_dict(sub_item) for sub_item in item])
                else:
                    flattened_data.append(flatten_dict(item))

            # Create CSV
            output = io.StringIO()
            if flattened_data:
                # Get all unique fieldnames
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
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error exporting extraction results: {str(e)}"
        )
