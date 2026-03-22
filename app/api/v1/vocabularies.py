"""Controlled vocabulary endpoints."""

import logging
from fastapi import APIRouter, Depends, HTTPException, status, Query, UploadFile, File
from uuid import UUID
from typing import Optional, List

from app.dependencies import get_current_user
from app.services import vocabulary_service
from app.models.schemas import (
    ControlledVocabularyCreate, ControlledVocabularyUpdate,
    ControlledVocabularyResponse, FieldVocabularyMappingCreate,
    FieldVocabularyMappingResponse,
)

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("", response_model=List[ControlledVocabularyResponse])
async def list_vocabularies(
    project_id: Optional[UUID] = Query(None),
    user_id: UUID = Depends(get_current_user),
):
    """List project + global vocabularies."""
    result = await vocabulary_service.list_vocabularies(project_id)
    return [ControlledVocabularyResponse(**v) for v in result]


@router.post("", response_model=ControlledVocabularyResponse, status_code=status.HTTP_201_CREATED)
async def create_vocabulary(
    data: ControlledVocabularyCreate,
    user_id: UUID = Depends(get_current_user),
):
    """Create a new vocabulary."""
    terms = [t.model_dump() for t in data.terms] if data.terms else []
    result = await vocabulary_service.create_vocabulary(
        name=data.name,
        terms=terms,
        created_by=user_id,
        project_id=data.project_id,
        description=data.description,
        source=data.source,
    )
    if not result:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to create vocabulary")
    return ControlledVocabularyResponse(**result)


@router.get("/search")
async def search_terms(
    query: str = Query("", alias="q"),
    vocabulary_id: Optional[UUID] = Query(None),
    project_id: Optional[UUID] = Query(None),
    limit: int = Query(20, le=100),
    user_id: UUID = Depends(get_current_user),
):
    """Autocomplete term search."""
    return await vocabulary_service.search_terms(
        vocabulary_id=vocabulary_id,
        project_id=project_id,
        query=query,
        limit=limit,
    )


@router.post("/field-mappings", response_model=FieldVocabularyMappingResponse, status_code=status.HTTP_201_CREATED)
async def create_field_mapping(
    data: FieldVocabularyMappingCreate,
    user_id: UUID = Depends(get_current_user),
):
    """Map a vocabulary to a form field."""
    result = await vocabulary_service.create_field_mapping(
        form_id=data.form_id,
        field_name=data.field_name,
        vocabulary_id=data.vocabulary_id,
        validation_mode=data.validation_mode,
    )
    if not result:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to create mapping")
    return FieldVocabularyMappingResponse(**result)


@router.get("/field-mappings/{form_id}", response_model=List[FieldVocabularyMappingResponse])
async def get_field_mappings(
    form_id: UUID,
    user_id: UUID = Depends(get_current_user),
):
    """Get vocabulary mappings for a form."""
    result = await vocabulary_service.get_field_mappings(form_id)
    return [FieldVocabularyMappingResponse(**m) for m in result]


@router.put("/{vocabulary_id}", response_model=ControlledVocabularyResponse)
async def update_vocabulary(
    vocabulary_id: UUID,
    data: ControlledVocabularyUpdate,
    user_id: UUID = Depends(get_current_user),
):
    """Update a vocabulary."""
    updates = data.model_dump(exclude_unset=True)
    if "terms" in updates and updates["terms"] is not None:
        updates["terms"] = [t if isinstance(t, dict) else t.model_dump() for t in data.terms]
    result = await vocabulary_service.update_vocabulary(vocabulary_id, updates)
    if not result:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Vocabulary not found")
    return ControlledVocabularyResponse(**result)


@router.delete("/{vocabulary_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_vocabulary(
    vocabulary_id: UUID,
    user_id: UUID = Depends(get_current_user),
):
    """Delete a vocabulary."""
    await vocabulary_service.delete_vocabulary(vocabulary_id)


@router.post("/{vocabulary_id}/import")
async def import_terms(
    vocabulary_id: UUID,
    file: UploadFile = File(...),
    user_id: UUID = Depends(get_current_user),
):
    """Import terms from a CSV file."""
    content = await file.read()
    csv_text = content.decode("utf-8")
    try:
        result = await vocabulary_service.import_terms_from_csv(vocabulary_id, csv_text)
        return result
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
