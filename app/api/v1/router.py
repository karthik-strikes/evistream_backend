"""
Main API router that combines all endpoint routers.
"""

from fastapi import APIRouter

from .auth import router as auth_router
from .admin import router as admin_router
from .projects import router as projects_router
from .documents import router as documents_router
from .forms import router as forms_router
from .extractions import router as extractions_router
from .results import router as results_router
from .jobs import router as jobs_router
from .websocket import router as websocket_router
from .activities import router as activities_router
from .notifications import router as notifications_router
from .issues import router as issues_router
from .dashboard import router as dashboard_router
from .project_members import router as project_members_router
from .settings import router as settings_router
from .assignments import router as assignments_router
from .adjudication import router as adjudication_router
from .qa import router as qa_router
from .vocabularies import router as vocabularies_router
from .data_cleaning import router as data_cleaning_router
from .audit import router as audit_router
from .client_logs import router as client_logs_router
from .pilot import router as pilot_router


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
api_router.include_router(issues_router, prefix="/issues", tags=["Issues"])
api_router.include_router(dashboard_router, prefix="/dashboard", tags=["Dashboard"])
api_router.include_router(admin_router, prefix="/admin", tags=["Admin"])
api_router.include_router(project_members_router, prefix="/projects", tags=["Project Members"])
api_router.include_router(settings_router, prefix="/settings", tags=["Settings"])
api_router.include_router(assignments_router, prefix="/assignments", tags=["Assignments"])
api_router.include_router(adjudication_router, prefix="/adjudication", tags=["Adjudication"])
api_router.include_router(qa_router, prefix="/qa", tags=["QA Reviews"])
api_router.include_router(vocabularies_router, prefix="/vocabularies", tags=["Vocabularies"])
api_router.include_router(data_cleaning_router, prefix="/data-cleaning", tags=["Data Cleaning"])
api_router.include_router(audit_router, prefix="/audit", tags=["Audit Trail"])
api_router.include_router(client_logs_router, prefix="/logs", tags=["Client Logs"])
api_router.include_router(pilot_router, prefix="/forms", tags=["Pilot"])
