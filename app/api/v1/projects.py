"""
Project management endpoints - Full CRUD operations.
"""

import logging
from collections import Counter
from fastapi import APIRouter, Depends, HTTPException, status
from supabase import create_client
from uuid import UUID
from typing import List

from app.dependencies import get_current_user
from app.config import settings
from app.models.schemas import ProjectCreate, ProjectUpdate, ProjectResponse, MyPermissionsResponse
from app.services.project_access import check_project_access

logger = logging.getLogger(__name__)

router = APIRouter()

# Initialize Supabase client
supabase = create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_KEY)


@router.post("", response_model=ProjectResponse, status_code=status.HTTP_201_CREATED)
async def create_project(
    project_data: ProjectCreate,
    user_id: UUID = Depends(get_current_user)
):
    """
    Create a new project.

    - **name**: Project name (required, 1-255 characters)
    - **description**: Optional project description
    """
    try:
        result = supabase.table("projects").insert({
            "user_id": str(user_id),
            "name": project_data.name,
            "description": project_data.description
        }).execute()

        if not result.data:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to create project"
            )

        project = result.data[0]
        project["forms_count"] = 0
        project["documents_count"] = 0

        return ProjectResponse(**project)

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Error creating project")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred"
        )


@router.get("", response_model=List[ProjectResponse])
async def list_projects(user_id: UUID = Depends(get_current_user)):
    """
    List all projects for the current user.

    Returns projects sorted by creation date (newest first).
    Includes counts of forms and documents in each project.
    """
    try:
        # Get owned projects
        owned_result = supabase.table("projects")\
            .select("*")\
            .eq("user_id", str(user_id))\
            .order("created_at", desc=True)\
            .execute()
        owned_projects = owned_result.data or []

        # Get member projects
        member_result = supabase.table("project_members")\
            .select("project_id")\
            .eq("user_id", str(user_id))\
            .execute()
        member_project_ids = [r["project_id"] for r in (member_result.data or [])]

        member_projects = []
        if member_project_ids:
            mp_result = supabase.table("projects")\
                .select("*")\
                .in_("id", member_project_ids)\
                .order("created_at", desc=True)\
                .execute()
            member_projects = mp_result.data or []

        # Merge, deduplicate by id
        seen = set()
        projects = []
        for p in owned_projects + member_projects:
            if p["id"] not in seen:
                seen.add(p["id"])
                projects.append(p)

        # Batch-fetch counts for all projects in 2 queries (not 2N)
        project_ids = [p["id"] for p in projects]
        if project_ids:
            all_forms = supabase.table("forms")\
                .select("project_id")\
                .in_("project_id", project_ids)\
                .execute()
            forms_count_map = Counter(r["project_id"] for r in (all_forms.data or []))

            all_docs = supabase.table("documents")\
                .select("project_id")\
                .in_("project_id", project_ids)\
                .execute()
            docs_count_map = Counter(r["project_id"] for r in (all_docs.data or []))
        else:
            forms_count_map = {}
            docs_count_map = {}

        for project in projects:
            project["forms_count"] = forms_count_map.get(project["id"], 0)
            project["documents_count"] = docs_count_map.get(project["id"], 0)

        return [ProjectResponse(**p) for p in projects]

    except Exception as e:
        logger.exception("Error listing projects")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred"
        )


@router.get("/{project_id}", response_model=ProjectResponse)
async def get_project(
    project_id: UUID,
    user_id: UUID = Depends(get_current_user)
):
    """
    Get a specific project by ID.

    Returns 404 if project doesn't exist or doesn't belong to the user.
    """
    try:
        await check_project_access(project_id, user_id)

        result = supabase.table("projects")\
            .select("*")\
            .eq("id", str(project_id))\
            .execute()

        if not result.data:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")

        project = result.data[0]

        forms_count = supabase.table("forms")\
            .select("id", count="exact")\
            .eq("project_id", str(project_id))\
            .execute()
        project["forms_count"] = forms_count.count or 0

        docs_count = supabase.table("documents")\
            .select("id", count="exact")\
            .eq("project_id", str(project_id))\
            .execute()
        project["documents_count"] = docs_count.count or 0

        return ProjectResponse(**project)

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Error getting project")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="An unexpected error occurred")


@router.put("/{project_id}", response_model=ProjectResponse)
async def update_project(
    project_id: UUID,
    project_data: ProjectUpdate,
    user_id: UUID = Depends(get_current_user)
):
    """
    Update a project.

    Can update name and/or description.
    Only the project owner can update it.
    """
    try:
        existing = supabase.table("projects")\
            .select("id")\
            .eq("id", str(project_id))\
            .eq("user_id", str(user_id))\
            .execute()

        if not existing.data:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Project not found"
            )

        update_data = {}
        if project_data.name is not None:
            update_data["name"] = project_data.name
        if project_data.description is not None:
            update_data["description"] = project_data.description

        if not update_data:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No fields to update"
            )

        result = supabase.table("projects")\
            .update(update_data)\
            .eq("id", str(project_id))\
            .execute()

        if not result.data:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to update project"
            )

        project = result.data[0]

        forms_count = supabase.table("forms")\
            .select("id", count="exact")\
            .eq("project_id", str(project_id))\
            .execute()
        project["forms_count"] = forms_count.count or 0

        docs_count = supabase.table("documents")\
            .select("id", count="exact")\
            .eq("project_id", str(project_id))\
            .execute()
        project["documents_count"] = docs_count.count or 0

        return ProjectResponse(**project)

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Error updating project")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred"
        )


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(
    project_id: UUID,
    user_id: UUID = Depends(get_current_user)
):
    """
    Delete a project.

    This will CASCADE delete all related:
    - Documents
    - Forms
    - Jobs
    - Extraction results

    Only the project owner can delete it.
    """
    try:
        existing = supabase.table("projects")\
            .select("id")\
            .eq("id", str(project_id))\
            .eq("user_id", str(user_id))\
            .execute()

        if not existing.data:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Project not found"
            )

        supabase.table("projects")\
            .delete()\
            .eq("id", str(project_id))\
            .execute()

        return None  # 204 No Content

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Error deleting project")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred"
        )


@router.get("/{project_id}/my-permissions", response_model=MyPermissionsResponse)
async def get_my_permissions(
    project_id: UUID,
    user_id: UUID = Depends(get_current_user)
):
    """Get the current user's effective permissions for a project."""
    try:
        permissions = await check_project_access(project_id, user_id)
        return MyPermissionsResponse(**permissions)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Error getting permissions")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred"
        )
