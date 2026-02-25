"""Activity Feed API endpoints."""

from fastapi import APIRouter, Depends, Query, HTTPException, status
from supabase import create_client
from typing import List, Optional
from datetime import datetime, timedelta
from uuid import UUID

from app.dependencies import get_current_user, get_optional_user
from app.config import settings
from app.models.schemas import ActivityResponse

router = APIRouter()

# Initialize Supabase client
supabase = create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)


@router.get("", response_model=List[ActivityResponse])
async def get_activities(
    project_id: Optional[str] = Query(None, description="Filter by project ID"),
    action_type: Optional[str] = Query(None, description="Filter by action type"),
    date_range: Optional[str] = Query("week", description="Date range: today, week, month, all"),
    limit: int = Query(100, ge=1, le=500, description="Number of activities to return"),
    offset: int = Query(0, ge=0, description="Number of activities to skip"),
    user_id: UUID = Depends(get_optional_user),
):
    """Get user's activity feed with optional filters."""
    try:
        # Use placeholder user ID if not authenticated (dev mode)
        effective_user_id = user_id or UUID("00000000-0000-0000-0000-000000000001")

        # Build query
        query = supabase.table("activities")\
            .select("*, projects(name)")\
            .eq("user_id", str(effective_user_id))

        # Add project filter
        if project_id:
            query = query.eq("project_id", project_id)

        # Add action type filter
        if action_type:
            query = query.eq("action_type", action_type)

        # Add date range filter
        if date_range != "all":
            now = datetime.utcnow()
            if date_range == "today":
                start_date = now.replace(hour=0, minute=0, second=0, microsecond=0)
            elif date_range == "week":
                start_date = now - timedelta(days=7)
            elif date_range == "month":
                start_date = now - timedelta(days=30)
            else:
                start_date = now - timedelta(days=7)  # Default to week

            query = query.gte("created_at", start_date.isoformat())

        # Add ordering and pagination
        query = query.order("created_at", desc=True)\
            .range(offset, offset + limit - 1)

        # Execute query
        result = query.execute()

        # Transform data to include project_name
        activities = []
        for activity in result.data:
            project_name = None
            if activity.get("projects") and isinstance(activity["projects"], dict):
                project_name = activity["projects"].get("name")

            activities.append(ActivityResponse(
                id=activity["id"],
                user_id=activity["user_id"],
                project_id=activity.get("project_id"),
                project_name=project_name,
                action_type=activity["action_type"],
                action=activity["action"],
                description=activity["description"],
                metadata=activity.get("metadata"),
                status=activity.get("status"),
                created_at=activity["created_at"],
            ))

        return activities

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error fetching activities: {str(e)}"
        )


@router.get("/{activity_id}", response_model=ActivityResponse)
async def get_activity(
    activity_id: UUID,
    user_id: UUID = Depends(get_optional_user),
):
    """Get a specific activity by ID."""
    try:
        # Use placeholder user ID if not authenticated (dev mode)
        effective_user_id = user_id or UUID("00000000-0000-0000-0000-000000000001")

        result = supabase.table("activities")\
            .select("*, projects(name)")\
            .eq("id", str(activity_id))\
            .eq("user_id", str(effective_user_id))\
            .execute()

        if not result.data:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Activity not found"
            )

        activity = result.data[0]
        project_name = None
        if activity.get("projects") and isinstance(activity["projects"], dict):
            project_name = activity["projects"].get("name")

        return ActivityResponse(
            id=activity["id"],
            user_id=activity["user_id"],
            project_id=activity.get("project_id"),
            project_name=project_name,
            action_type=activity["action_type"],
            action=activity["action"],
            description=activity["description"],
            metadata=activity.get("metadata"),
            status=activity.get("status"),
            created_at=activity["created_at"],
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error fetching activity: {str(e)}"
        )
