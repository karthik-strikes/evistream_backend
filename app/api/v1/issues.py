"""Issue Reports API endpoints."""

import logging
from fastapi import APIRouter, Depends, HTTPException, status
from supabase import create_client
from uuid import UUID

from app.dependencies import get_current_user
from app.config import settings
from app.models.schemas import IssueCreate, IssueResponse

logger = logging.getLogger(__name__)

router = APIRouter()

# Use service role key — required to bypass RLS on issue_reports
supabase = create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_KEY)


@router.post("", response_model=IssueResponse, status_code=status.HTTP_201_CREATED)
async def create_issue(
    data: IssueCreate,
    user_id: UUID = Depends(get_current_user),
):
    """Submit a new issue report."""
    try:
        # Fetch user email (non-fatal if missing)
        try:
            user_result = supabase.table("users").select("email").eq("id", str(user_id)).execute()
            user_email = user_result.data[0].get("email") if user_result.data else None
        except Exception:
            user_email = None

        result = supabase.table("issue_reports").insert({
            "user_id": str(user_id),
            "user_email": user_email,
            "title": data.title,
            "description": data.description,
            "category": data.category.value,
            "priority": data.priority.value,
            "page_url": data.page_url,
            "browser_info": data.browser_info,
            "steps_to_reproduce": data.steps_to_reproduce,
            "metadata": data.metadata,
        }).execute()

        if not result.data:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to create issue report"
            )

        return IssueResponse(**result.data[0])

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Error creating issue report")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred"
        )
