"""
Main API router that combines all endpoint routers.
"""

from fastapi import APIRouter

from .auth import router as auth_router
from .projects import router as projects_router
from .documents import router as documents_router
from .forms import router as forms_router
from .extractions import router as extractions_router
from .results import router as results_router
from .jobs import router as jobs_router
from .websocket import router as websocket_router
from .activities import router as activities_router
from .notifications import router as notifications_router


api_router = APIRouter()


@api_router.get("/health")
async def health():
    """Health check endpoint accessible via API proxy."""
    return {"status": "healthy"}


# Include all routers
api_router.include_router(auth_router, prefix="/auth", tags=["Authentication"])
api_router.include_router(projects_router, prefix="/projects", tags=["Projects"])
api_router.include_router(documents_router, prefix="/documents", tags=["Documents"])
api_router.include_router(forms_router, prefix="/forms", tags=["Forms"])
api_router.include_router(extractions_router, prefix="/extractions", tags=["Extractions"])
api_router.include_router(results_router, prefix="/results", tags=["Results"])
api_router.include_router(jobs_router, prefix="/jobs", tags=["Jobs"])
api_router.include_router(websocket_router, prefix="/ws", tags=["WebSocket"])
api_router.include_router(activities_router, prefix="/activities", tags=["Activities"])
api_router.include_router(notifications_router, prefix="/notifications", tags=["Notifications"])
