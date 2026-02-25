"""
Project management endpoints - Full CRUD operations.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from supabase import create_client
from uuid import UUID
from typing import List

from app.dependencies import get_current_user, get_optional_user
from app.config import settings
from app.models.schemas import ProjectCreate, ProjectUpdate, ProjectResponse


router = APIRouter()

# Initialize Supabase client
supabase = create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)


@router.post("", response_model=ProjectResponse, status_code=status.HTTP_201_CREATED)
async def create_project(
    project_data: ProjectCreate,
    user_id: UUID = Depends(get_optional_user)
):
    """
    Create a new project.

    - **name**: Project name (required, 1-255 characters)
    - **description**: Optional project description
    """
    try:
        # Use placeholder user ID if not authenticated (dev mode)
        effective_user_id = user_id or UUID("00000000-0000-0000-0000-000000000001")

        # Create project in database
        result = supabase.table("projects").insert({
            "user_id": str(effective_user_id),
            "name": project_data.name,
            "description": project_data.description
        }).execute()

        if not result.data:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to create project"
            )

        project = result.data[0]

        # Add counts (new project has no forms or documents)
        project["forms_count"] = 0
        project["documents_count"] = 0

        return ProjectResponse(**project)

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error creating project: {str(e)}"
        )


@router.get("", response_model=List[ProjectResponse])
async def list_projects(user_id: UUID = Depends(get_optional_user)):
    """
    List all projects for the current user.

    Returns projects sorted by creation date (newest first).
    Includes counts of forms and documents in each project.
    """
    try:
        # Use placeholder user ID if not authenticated (dev mode)
        effective_user_id = user_id or UUID("00000000-0000-0000-0000-000000000001")

        # Get all user's projects
        projects_result = supabase.table("projects")\
            .select("*")\
            .eq("user_id", str(effective_user_id))\
            .order("created_at", desc=True)\
            .execute()

        projects = projects_result.data or []

        # Get counts for each project
        for project in projects:
            project_id = project["id"]

            # Count forms
            forms_count = supabase.table("forms")\
                .select("id", count="exact")\
                .eq("project_id", project_id)\
                .execute()
            project["forms_count"] = forms_count.count or 0

            # Count documents
            docs_count = supabase.table("documents")\
                .select("id", count="exact")\
                .eq("project_id", project_id)\
                .execute()
            project["documents_count"] = docs_count.count or 0

        return [ProjectResponse(**p) for p in projects]

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error listing projects: {str(e)}"
        )


@router.get("/{project_id}", response_model=ProjectResponse)
async def get_project(
    project_id: UUID,
    user_id: UUID = Depends(get_optional_user)
):
    """
    Get a specific project by ID.

    Returns 404 if project doesn't exist or doesn't belong to the user.
    """
    try:
        # Use placeholder user ID if not authenticated (dev mode)
        effective_user_id = user_id or UUID("00000000-0000-0000-0000-000000000001")

        # Get project
        result = supabase.table("projects")\
            .select("*")\
            .eq("id", str(project_id))\
            .eq("user_id", str(effective_user_id))\
            .execute()

        if not result.data:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Project not found"
            )

        project = result.data[0]

        # Get counts
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
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error getting project: {str(e)}"
        )


@router.put("/{project_id}", response_model=ProjectResponse)
async def update_project(
    project_id: UUID,
    project_data: ProjectUpdate,
    user_id: UUID = Depends(get_optional_user)
):
    """
    Update a project.

    Can update name and/or description.
    Only the project owner can update it.
    """
    try:
        # Use placeholder user ID if not authenticated (dev mode)
        effective_user_id = user_id or UUID("00000000-0000-0000-0000-000000000001")

        # Verify project exists and belongs to user
        existing = supabase.table("projects")\
            .select("id")\
            .eq("id", str(project_id))\
            .eq("user_id", str(effective_user_id))\
            .execute()

        if not existing.data:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Project not found"
            )

        # Build update data (only include fields that are set)
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

        # Update project
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

        # Get counts
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
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error updating project: {str(e)}"
        )


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(
    project_id: UUID,
    user_id: UUID = Depends(get_optional_user)
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
        # Use placeholder user ID if not authenticated (dev mode)
        effective_user_id = user_id or UUID("00000000-0000-0000-0000-000000000001")

        # Verify project exists and belongs to user
        existing = supabase.table("projects")\
            .select("id")\
            .eq("id", str(project_id))\
            .eq("user_id", str(effective_user_id))\
            .execute()

        if not existing.data:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Project not found"
            )

        # Delete project (CASCADE will delete related records)
        supabase.table("projects")\
            .delete()\
            .eq("id", str(project_id))\
            .execute()

        return None  # 204 No Content

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error deleting project: {str(e)}"
        )
