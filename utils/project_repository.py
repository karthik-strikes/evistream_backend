"""
Repository layer for projects, forms, and document metadata.

This module centralizes all Supabase access for project-related data.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from utils.supabase_client import get_supabase_client


def _get_supabase_table(table_name: str):
    """Return a Supabase table handle or raise error if not available."""
    client = get_supabase_client()
    if client is None or client.client is None:
        raise RuntimeError(
            "Supabase client not configured. Please set SUPABASE_URL and SUPABASE_KEY.")
    return client.client.table(table_name)


# -------------------- Projects -------------------- #

def list_projects() -> List[Dict[str, Any]]:
    """List all projects from Supabase."""
    table = _get_supabase_table("projects")
    result = table.select("*").order("created_at").execute()
    return result.data or []


def get_project(project_id: str) -> Optional[Dict[str, Any]]:
    """Get a single project by ID (without forms/documents)."""
    table = _get_supabase_table("projects")
    result = (
        table.select("*")
        .eq("id", project_id)
        .limit(1)
        .execute()
    )
    rows = result.data or []
    return rows[0] if rows else None


def create_project(name: str, description: str) -> Dict[str, Any]:
    """Create a new project and return the created row."""
    table = _get_supabase_table("projects")
    payload = {
        "name": name,
        "description": description,
    }
    result = table.insert(payload).execute()
    if not result.data:
        raise RuntimeError("Failed to create project in Supabase.")
    return result.data[0]


def project_name_exists(name: str) -> bool:
    """Check if a project with the given name already exists (case-insensitive)."""
    table = _get_supabase_table("projects")

    # Supabase-py supports ilike for case-insensitive matching
    result = (
        table.select("id")
        .ilike("name", name.strip())
        .limit(1)
        .execute()
    )
    rows = result.data or []
    if rows:
        return True

    # Fallback: exact match if ilike is not supported in some environments
    result_eq = (
        table.select("id")
        .eq("name", name.strip())
        .limit(1)
        .execute()
    )
    return bool(result_eq.data)


# -------------------- Forms -------------------- #

def list_forms(project_id: str) -> List[Dict[str, Any]]:
    """List all forms for a project."""
    table = _get_supabase_table("project_forms")

    try:
        # Try to select all columns (works if migration has been run)
        result = (
            table.select("*")
            .eq("project_id", project_id)
            .order("created_at")
            .execute()
        )
    except Exception as e:
        # Fallback: select only core columns if new columns don't exist
        print(f"Warning: Could not select all columns, using fallback: {e}")
        result = (
            table.select(
                "id, name, description, fields, schema_name, task_dir, created_at")
            .eq("project_id", project_id)
            .order("created_at")
            .execute()
        )

    rows = result.data or []
    normalized: List[Dict[str, Any]] = []
    for row in rows:
        normalized.append(
            {
                "id": row.get("id"),
                "form_name": row.get("name"),
                "form_description": row.get("description"),
                "fields": row.get("fields") or [],
                "schema_name": row.get("schema_name"),
                "task_dir": row.get("task_dir"),
                "status": row.get("status", "DRAFT"),
                "decomposition": row.get("decomposition"),
                "validation_results": row.get("validation_results"),
                "review_thread_id": row.get("review_thread_id"),
                "error": row.get("error"),
                "statistics": row.get("statistics"),
            }
        )
    return normalized


def create_form(project_id: str, form_payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Create a form for a project.

    `form_payload` is expected to contain:
      - name or form_name
      - description or form_description
      - fields
      - schema_name
      - task_dir
      - status (optional)
    """
    table = _get_supabase_table("project_forms")
    form_name = form_payload.get("form_name") or form_payload.get("name")
    form_description = form_payload.get("form_description") or form_payload.get(
        "description"
    )

    # Build base payload with core columns
    payload = {
        "project_id": project_id,
        "name": form_name,
        "description": form_description,
        "fields": form_payload.get("fields") or [],
        "schema_name": form_payload.get("schema_name"),
        "task_dir": form_payload.get("task_dir"),
    }

    # Try to add status if column exists
    try:
        payload["status"] = form_payload.get("status", "DRAFT")
        result = table.insert(payload).execute()
    except Exception as e:
        # Fallback: insert without status column if it doesn't exist
        print(f"Warning: Could not insert with status column: {e}")
        payload.pop("status", None)
        result = table.insert(payload).execute()

    if not result.data:
        raise RuntimeError("Failed to create form in Supabase.")
    row = result.data[0]
    return {
        "id": row.get("id"),
        "form_name": row.get("name"),
        "form_description": row.get("description"),
        "fields": row.get("fields") or [],
        "schema_name": row.get("schema_name"),
        "task_dir": row.get("task_dir"),
        "status": row.get("status", "DRAFT"),
    }


def update_form(project_id: str, form_id: str, update_data: dict):
    """
    Update form with new data.

    Args:
        project_id: Project ID
        form_id: Form ID
        update_data: Dictionary of fields to update
    """
    table = _get_supabase_table("project_forms")

    try:
        result = table.update(
            update_data
        ).eq("id", form_id).eq("project_id", project_id).execute()

        if not result.data:
            raise RuntimeError(f"Failed to update form {form_id}")

        return result.data[0]
    except Exception as e:
        # If update fails due to missing columns, try with only core columns
        print(f"Warning: Update failed, possibly due to missing columns: {e}")

        # Filter to only core columns that should exist
        core_columns = ["name", "description",
                        "fields", "schema_name", "task_dir"]
        filtered_data = {k: v for k, v in update_data.items()
                         if k in core_columns}

        if filtered_data:
            result = table.update(
                filtered_data
            ).eq("id", form_id).eq("project_id", project_id).execute()

            if not result.data:
                raise RuntimeError(f"Failed to update form {form_id}")

            return result.data[0]
        else:
            # If no core columns to update, just log and continue
            print(
                f"Warning: No core columns to update for form {form_id}, skipping update")
            return None


def get_form(project_id: str, form_id: str) -> Optional[dict]:
    """
    Get a single form by ID.

    Args:
        project_id: Project ID
        form_id: Form ID

    Returns:
        Form data dictionary or None if not found
    """
    table = _get_supabase_table("project_forms")

    try:
        result = table.select("*").eq(
            "id", form_id
        ).eq("project_id", project_id).limit(1).execute()
    except Exception as e:
        # Fallback: select only core columns if new columns don't exist
        print(
            f"Warning: Could not select all columns in get_form, using fallback: {e}")
        result = table.select("id, name, description, fields, schema_name, task_dir, created_at").eq(
            "id", form_id
        ).eq("project_id", project_id).limit(1).execute()

    rows = result.data or []
    if not rows:
        return None

    row = rows[0]
    return {
        "id": row.get("id"),
        "form_name": row.get("name"),
        "form_description": row.get("description"),
        "fields": row.get("fields") or [],
        "schema_name": row.get("schema_name"),
        "task_dir": row.get("task_dir"),
        "status": row.get("status", "DRAFT"),
        "decomposition": row.get("decomposition"),
        "validation_results": row.get("validation_results"),
        "review_thread_id": row.get("review_thread_id"),
        "reviewed_at": row.get("reviewed_at"),
        "error": row.get("error"),
        "signatures_code": row.get("signatures_code"),
        "modules_code": row.get("modules_code"),
        "field_mapping": row.get("field_mapping"),
        "statistics": row.get("statistics"),
    }


# -------------------- Documents -------------------- #

def list_documents(project_id: str) -> List[Dict[str, Any]]:
    """List document metadata for a project."""
    table = _get_supabase_table("project_documents")

    result = (
        table.select("*")
        .eq("project_id", project_id)
        .order("created_at")
        .execute()
    )
    rows = result.data or []
    normalized: List[Dict[str, Any]] = []

    for row in rows:
        unique_name = row.get("unique_filename")
        markdown_path = row.get("markdown_path")
        markdown_content: Optional[str] = None

        # Prefer explicit markdown_path if present
        if markdown_path:
            try:
                path_obj = Path(markdown_path)
                if path_obj.exists():
                    data = json.loads(path_obj.read_text())
                    markdown_content = data.get("marker", {}).get("markdown")
            except Exception as e:
                print(
                    f"Warning: Failed to load markdown from {markdown_path}: {e}")
                markdown_content = None

        # Fallback 1: Try Notebooks directory (standard location)
        if markdown_content is None and unique_name:
            _notebook_dir = os.environ.get(
                "NOTEBOOK_DIR",
                str(Path(__file__).resolve().parents[1] / "storage" / "notebooks")
            )
            storage_path = Path(_notebook_dir) / f"{unique_name}.json"
            try:
                if storage_path.exists():
                    data = json.loads(storage_path.read_text())
                    markdown_content = data.get("marker", {}).get("markdown")
            except Exception as e:
                print(
                    f"Warning: Failed to load markdown from storage path: {e}")
                markdown_content = None

        # Fallback 2: Try output/extracted_pdfs (legacy location)
        if markdown_content is None and unique_name:
            output_path = (
                Path(__file__).parent.parent
                / "output"
                / "extracted_pdfs"
                / unique_name
                / f"{unique_name}.json"
            )
            try:
                if output_path.exists():
                    data = json.loads(output_path.read_text())
                    markdown_content = data.get("marker", {}).get("markdown")
            except Exception as e:
                print(
                    f"Warning: Failed to load markdown from output path: {e}")
                markdown_content = None

        # Final warning if still not found
        if markdown_content is None and unique_name:
            print(
                f"⚠️  Markdown content not found for document: {row.get('original_filename')} (unique: {unique_name})")
            print(
                f"   Tried: {markdown_path if markdown_path else 'N/A'}, storage/processed/, output/")

        normalized.append(
            {
                "id": row.get("id"),
                "filename": row.get("original_filename"),
                "unique_filename": unique_name,
                "markdown_path": markdown_path,
                "pdf_storage_path": row.get("pdf_storage_path"),
                # For backward compatibility, expose temp_path pointing to pdf_storage_path
                "temp_path": row.get("pdf_storage_path"),
                # Make extraction tab work the same in Supabase mode
                "markdown_content": markdown_content,
            }
        )

    return normalized


def add_document(project_id: str, metadata: Dict[str, Any]) -> Dict[str, Any]:
    """
    Add document metadata for a project.

    Expected metadata keys:
      - filename (original filename)
      - unique_filename
      - pdf_storage_path
      - markdown_path
    """
    table = _get_supabase_table("project_documents")

    payload = {
        "project_id": project_id,
        "original_filename": metadata.get("filename"),
        "unique_filename": metadata.get("unique_filename"),
        "pdf_storage_path": metadata.get("pdf_storage_path"),
        "markdown_path": metadata.get("markdown_path"),
    }
    result = table.insert(payload).execute()
    if not result.data:
        raise RuntimeError("Failed to add document metadata in Supabase.")
    row = result.data[0]
    return {
        "id": row.get("id"),
        "filename": row.get("original_filename"),
        "unique_filename": row.get("unique_filename"),
        "markdown_path": row.get("markdown_path"),
        "pdf_storage_path": row.get("pdf_storage_path"),
        "temp_path": row.get("pdf_storage_path"),
    }


# -------------------- Combined helper -------------------- #

def get_full_project(project_id: str) -> Optional[Dict[str, Any]]:
    """
    Convenience helper to return a project with its forms and documents.
    """
    proj = get_project(project_id)
    if proj is None:
        return None
    proj["forms"] = list_forms(project_id)
    proj["pdfs"] = list_documents(project_id)
    return proj
