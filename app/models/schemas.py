"""Pydantic schemas for API request/response validation."""

from pydantic import BaseModel, EmailStr, Field, ConfigDict, field_validator
from typing import Optional, List, Dict, Any
from datetime import datetime
from uuid import UUID
from .enums import JobStatus, JobType, FormStatus, DocumentStatus


# ============================================================================
# Authentication Schemas
# ============================================================================

class UserRegister(BaseModel):
    """User registration request."""
    email: EmailStr
    password: str = Field(..., min_length=8)
    full_name: Optional[str] = None


class UserLogin(BaseModel):
    """User login request."""
    email: EmailStr
    password: str


class Token(BaseModel):
    """JWT token response."""
    access_token: str
    token_type: str = "bearer"
    user_id: UUID


class UserResponse(BaseModel):
    """User information response."""
    id: UUID
    email: str
    full_name: Optional[str]
    is_active: bool
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


# ============================================================================
# Project Schemas
# ============================================================================

class ProjectCreate(BaseModel):
    """Project creation request."""
    name: str = Field(..., min_length=1, max_length=255)
    description: Optional[str] = None


class ProjectUpdate(BaseModel):
    """Project update request."""
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    description: Optional[str] = None


class ProjectResponse(BaseModel):
    """Project response."""
    id: UUID
    user_id: UUID
    name: str
    description: Optional[str]
    created_at: datetime
    updated_at: datetime

    # Counts
    forms_count: int = 0
    documents_count: int = 0

    model_config = ConfigDict(from_attributes=True)


# ============================================================================
# Document Schemas
# ============================================================================

class DocumentUploadResponse(BaseModel):
    """Document upload response."""
    id: UUID
    filename: str
    unique_filename: str
    project_id: UUID
    job_id: UUID
    status: DocumentStatus


class DocumentResponse(BaseModel):
    """Document information response."""
    id: UUID
    project_id: UUID
    filename: str
    unique_filename: Optional[str]
    s3_pdf_path: Optional[str]
    s3_markdown_path: Optional[str]
    processing_status: DocumentStatus
    processing_error: Optional[str]
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


# ============================================================================
# Form Schemas
# ============================================================================

class FieldDefinition(BaseModel):
    """Form field definition."""
    field_name: str
    field_description: str
    field_type: str  # text, number, enum, object, array
    field_control_type: Optional[str] = None  # dropdown, checkbox_group_with_text, etc.
    options: Optional[List[str]] = None
    example: Optional[str] = None
    extraction_hints: Optional[str] = None
    subform_fields: Optional[List['FieldDefinition']] = None


class FormCreate(BaseModel):
    """Form creation request."""
    project_id: UUID
    form_name: str = Field(..., min_length=1, max_length=255)
    form_description: str
    fields: List[FieldDefinition]
    enable_review: bool = False


class FormUpdate(BaseModel):
    """Form update request."""
    form_name: Optional[str] = None
    form_description: Optional[str] = None
    fields: Optional[List[FieldDefinition]] = None


class FormResponse(BaseModel):
    """Form response."""
    id: UUID
    project_id: UUID
    form_name: str
    form_description: Optional[str]
    fields: List[Dict[str, Any]]
    status: FormStatus
    schema_name: Optional[str]
    task_dir: Optional[str]
    statistics: Optional[Dict[str, Any]]
    error: Optional[str]
    metadata: Optional[Dict[str, Any]] = None  # Workflow state for human review
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)

    @field_validator('metadata', mode='before')
    @classmethod
    def parse_metadata(cls, v):
        """Parse metadata from JSON string if needed."""
        if v is None:
            return None
        if isinstance(v, str):
            import json
            try:
                return json.loads(v)
            except json.JSONDecodeError:
                return None
        return v


# ============================================================================
# Extraction Schemas
# ============================================================================

class ExtractionCreate(BaseModel):
    """Extraction job creation request."""
    project_id: UUID
    form_id: UUID
    document_ids: Optional[List[UUID]] = None
    max_documents: Optional[int] = None


class ExtractionResponse(BaseModel):
    """Extraction job response."""
    id: UUID
    project_id: UUID
    form_id: UUID
    status: str
    job_id: Optional[UUID] = None
    created_at: datetime


class ExtractionResultResponse(BaseModel):
    """Extraction result response."""
    id: UUID
    job_id: Optional[UUID] = None
    project_id: UUID
    form_id: UUID
    document_id: UUID
    extracted_data: Dict[str, Any]
    evaluation_metrics: Optional[Dict[str, Any]]
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


# ============================================================================
# Job Schemas
# ============================================================================

class JobResponse(BaseModel):
    """Background job response."""
    id: UUID
    user_id: UUID
    project_id: Optional[UUID]
    job_type: JobType
    status: JobStatus
    progress: int
    celery_task_id: Optional[str]
    input_data: Optional[Dict[str, Any]]
    result_data: Optional[Dict[str, Any]]
    error_message: Optional[str]
    started_at: Optional[datetime]
    completed_at: Optional[datetime]
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


# ============================================================================
# WebSocket Messages
# ============================================================================

class WSMessage(BaseModel):
    """WebSocket message format."""
    event: str  # job.started, job.progress, job.completed, job.failed
    job_id: UUID
    data: Dict[str, Any]


# ============================================================================
# Activity Feed Schemas
# ============================================================================

class ActivityResponse(BaseModel):
    """Activity feed item response."""
    id: UUID
    user_id: UUID
    project_id: Optional[UUID]
    project_name: Optional[str] = None  # Populated via join
    action_type: str  # upload, extraction, export, code_generation, form_create, project_create
    action: str
    description: str
    metadata: Optional[Dict[str, Any]]
    status: Optional[str]  # success, failed, pending
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


# ============================================================================
# Notifications Schemas
# ============================================================================

class NotificationResponse(BaseModel):
    """Notification response."""
    id: UUID
    user_id: UUID
    type: str  # success, error, info, warning
    title: str
    message: str
    read: bool
    action_label: Optional[str]
    action_url: Optional[str]
    related_entity_type: Optional[str]
    related_entity_id: Optional[UUID]
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class NotificationCreate(BaseModel):
    """Notification creation request."""
    type: str = Field(..., pattern='^(success|error|info|warning)$')
    title: str = Field(..., min_length=1, max_length=255)
    message: str = Field(..., min_length=1)
    action_label: Optional[str] = Field(None, max_length=100)
    action_url: Optional[str] = Field(None, max_length=500)
    related_entity_type: Optional[str] = None
    related_entity_id: Optional[UUID] = None


# Resolve forward references
FieldDefinition.model_rebuild()
