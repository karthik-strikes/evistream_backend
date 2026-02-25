"""Notifications API endpoints."""

from fastapi import APIRouter, Depends, Query, HTTPException, status
from supabase import create_client
from typing import List
from datetime import datetime
from uuid import UUID

from app.dependencies import get_current_user, get_optional_user
from app.config import settings
from app.models.schemas import NotificationResponse, NotificationCreate

router = APIRouter()

# Initialize Supabase client
supabase = create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)


@router.get("", response_model=List[NotificationResponse])
async def get_notifications(
    unread_only: bool = Query(False, description="Return only unread notifications"),
    limit: int = Query(50, ge=1, le=200, description="Number of notifications to return"),
    offset: int = Query(0, ge=0, description="Number of notifications to skip"),
    user_id: UUID = Depends(get_optional_user),
):
    """Get user's notifications."""
    try:
        # Use placeholder user ID if not authenticated (dev mode)
        effective_user_id = user_id or UUID("00000000-0000-0000-0000-000000000001")

        # Build query
        query = supabase.table("notifications")\
            .select("*")\
            .eq("user_id", str(effective_user_id))

        if unread_only:
            query = query.eq("read", False)

        # Add ordering and pagination
        query = query.order("created_at", desc=True)\
            .range(offset, offset + limit - 1)

        result = query.execute()

        return [NotificationResponse(**notification) for notification in result.data]

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error fetching notifications: {str(e)}"
        )


@router.get("/unread-count", response_model=dict)
async def get_unread_count(
    user_id: UUID = Depends(get_optional_user),
):
    """Get count of unread notifications."""
    try:
        # Use placeholder user ID if not authenticated (dev mode)
        effective_user_id = user_id or UUID("00000000-0000-0000-0000-000000000001")

        result = supabase.table("notifications")\
            .select("id", count="exact")\
            .eq("user_id", str(effective_user_id))\
            .eq("read", False)\
            .execute()

        return {"count": result.count or 0}

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error fetching unread count: {str(e)}"
        )


@router.get("/{notification_id}", response_model=NotificationResponse)
async def get_notification(
    notification_id: UUID,
    user_id: UUID = Depends(get_optional_user),
):
    """Get a specific notification by ID."""
    try:
        # Use placeholder user ID if not authenticated (dev mode)
        effective_user_id = user_id or UUID("00000000-0000-0000-0000-000000000001")

        result = supabase.table("notifications")\
            .select("*")\
            .eq("id", str(notification_id))\
            .eq("user_id", str(effective_user_id))\
            .execute()

        if not result.data:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Notification not found"
            )

        return NotificationResponse(**result.data[0])

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error fetching notification: {str(e)}"
        )


@router.post("", response_model=NotificationResponse, status_code=status.HTTP_201_CREATED)
async def create_notification(
    notification: NotificationCreate,
    user_id: UUID = Depends(get_optional_user),
):
    """Create a new notification (for testing/admin purposes)."""
    try:
        # Use placeholder user ID if not authenticated (dev mode)
        effective_user_id = user_id or UUID("00000000-0000-0000-0000-000000000001")

        result = supabase.table("notifications").insert({
            "user_id": str(effective_user_id),
            "type": notification.type,
            "title": notification.title,
            "message": notification.message,
            "action_label": notification.action_label,
            "action_url": notification.action_url,
            "related_entity_type": notification.related_entity_type,
            "related_entity_id": str(notification.related_entity_id) if notification.related_entity_id else None,
        }).execute()

        if not result.data:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to create notification"
            )

        return NotificationResponse(**result.data[0])

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error creating notification: {str(e)}"
        )


@router.patch("/{notification_id}/read", response_model=NotificationResponse)
async def mark_as_read(
    notification_id: UUID,
    user_id: UUID = Depends(get_optional_user),
):
    """Mark a notification as read."""
    try:
        # Use placeholder user ID if not authenticated (dev mode)
        effective_user_id = user_id or UUID("00000000-0000-0000-0000-000000000001")

        result = supabase.table("notifications")\
            .update({
                "read": True,
                "read_at": datetime.utcnow().isoformat()
            })\
            .eq("id", str(notification_id))\
            .eq("user_id", str(effective_user_id))\
            .execute()

        if not result.data:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Notification not found"
            )

        return NotificationResponse(**result.data[0])

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error marking notification as read: {str(e)}"
        )


@router.post("/mark-all-read", response_model=dict)
async def mark_all_as_read(
    user_id: UUID = Depends(get_optional_user),
):
    """Mark all notifications as read for the current user."""
    try:
        # Use placeholder user ID if not authenticated (dev mode)
        effective_user_id = user_id or UUID("00000000-0000-0000-0000-000000000001")

        result = supabase.table("notifications")\
            .update({
                "read": True,
                "read_at": datetime.utcnow().isoformat()
            })\
            .eq("user_id", str(effective_user_id))\
            .eq("read", False)\
            .execute()

        return {
            "message": "All notifications marked as read",
            "count": len(result.data) if result.data else 0
        }

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error marking all as read: {str(e)}"
        )


@router.delete("/{notification_id}", response_model=dict)
async def delete_notification(
    notification_id: UUID,
    user_id: UUID = Depends(get_optional_user),
):
    """Delete a notification."""
    try:
        # Use placeholder user ID if not authenticated (dev mode)
        effective_user_id = user_id or UUID("00000000-0000-0000-0000-000000000001")

        result = supabase.table("notifications")\
            .delete()\
            .eq("id", str(notification_id))\
            .eq("user_id", str(effective_user_id))\
            .execute()

        if not result.data:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Notification not found"
            )

        return {"message": "Notification deleted successfully"}

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error deleting notification: {str(e)}"
        )


@router.delete("", response_model=dict)
async def delete_all_notifications(
    user_id: UUID = Depends(get_optional_user),
):
    """Delete all notifications for the current user."""
    try:
        # Use placeholder user ID if not authenticated (dev mode)
        effective_user_id = user_id or UUID("00000000-0000-0000-0000-000000000001")

        result = supabase.table("notifications")\
            .delete()\
            .eq("user_id", str(effective_user_id))\
            .execute()

        return {
            "message": "All notifications deleted",
            "count": len(result.data) if result.data else 0
        }

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error deleting all notifications: {str(e)}"
        )
