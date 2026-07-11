"""Controlled vocabulary endpoints."""

import logging
from fastapi import APIRouter, Depends, HTTPException, status, Query, UploadFile, File
from supabase import create_client
from uuid import UUID
from typing import Optional, List

from app.dependencies import get_current_user
from app.services import vocabulary_service
from app.services.project_access import check_project_access
from app.config import settings
from app.models.schemas import (
    ControlledVocabularyCreate, ControlledVocabularyUpdate,
    ControlledVocabularyResponse, FieldVocabularyMappingCreate,
    FieldVocabularyMappingResponse,
)

logger = logging.getLogger(__name__)
router = APIRouter()

_supabase = create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_KEY)


async def _get_project_id_for_form(form_id: UUID) -> UUID:
    """Resolve form_id to its project_id."""
    result = _supabase.table("forms").select("project_id").eq("id", str(form_id)).execute()
    if not result.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Form not found")
    return UUID(result.data[0]["project_id"])


async def _get_project_id_for_vocabulary(vocabulary_id: UUID) -> Optional[UUID]:
    """Resolve vocabulary_id to its project_id (may be None for global vocabs)."""
    result = _supabase.table("controlled_vocabularies").select("project_id").eq("id", str(vocabulary_id)).execute()
    if not result.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Vocabulary not found")
    pid = result.data[0].get("project_id")
    return UUID(pid) if pid else None


@router.get("", response_model=List[ControlledVocabularyResponse])
async def list_vocabularies(
    project_id: Optional[UUID] = Query(None),
    user_id: UUID = Depends(get_current_user),
):
    """List project + global vocabularies."""
    if project_id:
        await check_project_access(project_id, user_id, "can_view_docs")
    result = await vocabulary_service.list_vocabularies(project_id)
    return [ControlledVocabularyResponse(**v) for v in result]


@router.post("", response_model=ControlledVocabularyResponse, status_code=status.HTTP_201_CREATED)
async def create_vocabulary(
    data: ControlledVocabularyCreate,
    user_id: UUID = Depends(get_current_user),
):
    """Create a new vocabulary."""
    if data.project_id:
        await check_project_access(data.project_id, user_id, "can_create_forms")
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
    if project_id:
        await check_project_access(project_id, user_id, "can_view_docs")
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
    project_id = await _get_project_id_for_form(data.form_id)
    await check_project_access(project_id, user_id, "can_create_forms")
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
    project_id = await _get_project_id_for_form(form_id)
    await check_project_access(project_id, user_id, "can_view_docs")
    result = await vocabulary_service.get_field_mappings(form_id)
    return [FieldVocabularyMappingResponse(**m) for m in result]


@router.put("/{vocabulary_id}", response_model=ControlledVocabularyResponse)
async def update_vocabulary(
    vocabulary_id: UUID,
    data: ControlledVocabularyUpdate,
    user_id: UUID = Depends(get_current_user),
):
    """Update a vocabulary."""
    vocab_project_id = await _get_project_id_for_vocabulary(vocabulary_id)
    if vocab_project_id:
        await check_project_access(vocab_project_id, user_id, "can_create_forms")
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
    vocab_project_id = await _get_project_id_for_vocabulary(vocabulary_id)
    if vocab_project_id:
        await check_project_access(vocab_project_id, user_id, "can_create_forms")
    await vocabulary_service.delete_vocabulary(vocabulary_id)


@router.post("/{vocabulary_id}/import")
async def import_terms(
    vocabulary_id: UUID,
    file: UploadFile = File(...),
    user_id: UUID = Depends(get_current_user),
):
    """Import terms from a CSV file."""
    vocab_project_id = await _get_project_id_for_vocabulary(vocabulary_id)
    if vocab_project_id:
        await check_project_access(vocab_project_id, user_id, "can_create_forms")
    content = await file.read()
    csv_text = content.decode("utf-8")
    try:
        result = await vocabulary_service.import_terms_from_csv(vocabulary_id, csv_text)
        return result
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
