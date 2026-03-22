"""Service for controlled vocabularies."""

import logging
import csv
import io
from supabase import create_client, Client
from typing import Optional, Dict, Any, List
from uuid import UUID
from datetime import datetime, timezone

from app.config import settings

logger = logging.getLogger(__name__)


def get_supabase() -> Client:
    return create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_KEY)


async def list_vocabularies(
    project_id: Optional[UUID] = None,
) -> List[Dict[str, Any]]:
    """List vocabularies (project-specific + global)."""
    supabase = get_supabase()

    # Get global vocabularies (project_id is null)
    global_result = supabase.table("controlled_vocabularies")\
        .select("*")\
        .is_("project_id", "null")\
        .execute()
    vocabs = global_result.data or []

    # Get project-specific if provided
    if project_id:
        project_result = supabase.table("controlled_vocabularies")\
            .select("*")\
            .eq("project_id", str(project_id))\
            .execute()
        vocabs.extend(project_result.data or [])

    return vocabs


async def create_vocabulary(
    name: str,
    terms: List[Dict[str, Any]],
    created_by: UUID,
    project_id: Optional[UUID] = None,
    description: Optional[str] = None,
    source: str = "custom",
) -> Dict[str, Any]:
    """Create a new vocabulary."""
    supabase = get_supabase()
    result = supabase.table("controlled_vocabularies").insert({
        "project_id": str(project_id) if project_id else None,
        "name": name,
        "description": description,
        "terms": terms,
        "source": source,
        "created_by": str(created_by),
    }).execute()
    return result.data[0] if result.data else {}


async def update_vocabulary(
    vocabulary_id: UUID,
    updates: Dict[str, Any],
) -> Dict[str, Any]:
    """Update a vocabulary."""
    supabase = get_supabase()
    updates["updated_at"] = datetime.now(timezone.utc).isoformat()
    result = supabase.table("controlled_vocabularies")\
        .update(updates)\
        .eq("id", str(vocabulary_id))\
        .execute()
    return result.data[0] if result.data else {}


async def delete_vocabulary(vocabulary_id: UUID) -> bool:
    """Delete a vocabulary."""
    supabase = get_supabase()
    supabase.table("controlled_vocabularies")\
        .delete()\
        .eq("id", str(vocabulary_id))\
        .execute()
    return True


async def import_terms_from_csv(
    vocabulary_id: UUID,
    csv_content: str,
) -> Dict[str, Any]:
    """Import terms from CSV content into a vocabulary."""
    supabase = get_supabase()

    # Get existing vocabulary
    vocab = supabase.table("controlled_vocabularies")\
        .select("terms")\
        .eq("id", str(vocabulary_id))\
        .limit(1)\
        .execute()

    if not vocab.data:
        raise ValueError("Vocabulary not found")

    existing_terms = vocab.data[0].get("terms", [])

    # Parse CSV
    reader = csv.DictReader(io.StringIO(csv_content))
    new_terms = []
    for row in reader:
        term = row.get("term", "").strip()
        if not term:
            continue
        synonyms_raw = row.get("synonyms", "")
        synonyms = [s.strip() for s in synonyms_raw.split(";") if s.strip()] if synonyms_raw else []
        code = row.get("code", "").strip() or None
        new_terms.append({
            "term": term,
            "synonyms": synonyms,
            "code": code,
        })

    # Merge with existing (avoid duplicates by term name)
    existing_term_names = {t["term"].lower() for t in existing_terms}
    for t in new_terms:
        if t["term"].lower() not in existing_term_names:
            existing_terms.append(t)
            existing_term_names.add(t["term"].lower())

    result = supabase.table("controlled_vocabularies")\
        .update({
            "terms": existing_terms,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        })\
        .eq("id", str(vocabulary_id))\
        .execute()

    return {
        "imported": len(new_terms),
        "total_terms": len(existing_terms),
        "vocabulary": result.data[0] if result.data else {},
    }


async def search_terms(
    vocabulary_id: Optional[UUID] = None,
    project_id: Optional[UUID] = None,
    query: str = "",
    limit: int = 20,
) -> List[Dict[str, Any]]:
    """Search/autocomplete terms across vocabularies."""
    supabase = get_supabase()

    if vocabulary_id:
        vocab_result = supabase.table("controlled_vocabularies")\
            .select("id, name, terms")\
            .eq("id", str(vocabulary_id))\
            .execute()
    elif project_id:
        vocab_result = supabase.table("controlled_vocabularies")\
            .select("id, name, terms")\
            .eq("project_id", str(project_id))\
            .execute()
    else:
        vocab_result = supabase.table("controlled_vocabularies")\
            .select("id, name, terms")\
            .execute()

    matches = []
    query_lower = query.lower()

    for vocab in (vocab_result.data or []):
        for term_obj in (vocab.get("terms") or []):
            term = term_obj.get("term", "")
            synonyms = term_obj.get("synonyms", [])

            if query_lower in term.lower():
                matches.append({
                    "vocabulary_id": vocab["id"],
                    "vocabulary_name": vocab["name"],
                    "term": term,
                    "synonyms": synonyms,
                    "code": term_obj.get("code"),
                })
            elif any(query_lower in s.lower() for s in synonyms):
                matches.append({
                    "vocabulary_id": vocab["id"],
                    "vocabulary_name": vocab["name"],
                    "term": term,
                    "synonyms": synonyms,
                    "code": term_obj.get("code"),
                })

            if len(matches) >= limit:
                break
        if len(matches) >= limit:
            break

    return matches


async def create_field_mapping(
    form_id: UUID,
    field_name: str,
    vocabulary_id: UUID,
    validation_mode: str = "suggest",
) -> Dict[str, Any]:
    """Map a vocabulary to a form field."""
    supabase = get_supabase()
    result = supabase.table("field_vocabulary_mappings").upsert({
        "form_id": str(form_id),
        "field_name": field_name,
        "vocabulary_id": str(vocabulary_id),
        "validation_mode": validation_mode,
    }, on_conflict="form_id,field_name,vocabulary_id").execute()
    return result.data[0] if result.data else {}


async def get_field_mappings(form_id: UUID) -> List[Dict[str, Any]]:
    """Get all vocabulary mappings for a form."""
    supabase = get_supabase()
    result = supabase.table("field_vocabulary_mappings")\
        .select("*")\
        .eq("form_id", str(form_id))\
        .execute()
    return result.data or []
