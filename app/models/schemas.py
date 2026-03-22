"""Pydantic schemas for API request/response validation."""

from pydantic import BaseModel, EmailStr, Field, ConfigDict, field_validator
from typing import Optional, List, Dict, Any
from datetime import datetime
from uuid import UUID
from .enums import (
    JobStatus, JobType, FormStatus, DocumentStatus, UserRole,
    IssueCategory, IssuePriority, IssueStatus,
    ReviewerRole, AssignmentStatus, QAStatus, ValidationRuleType, BlindingMode,
)


# ============================================================================
# Authentication Schemas
# ============================================================================

class UserRegister(BaseModel):
    """User registration request."""
    email: EmailStr
    password: str = Field(..., min_length=8)
    full_name: Optional[str] = None

    @field_validator('password')
    @classmethod
    def validate_password_strength(cls, v: str) -> str:
        if not any(c.isupper() for c in v):
            raise ValueError('Password must contain at least one uppercase letter')
        if not any(c.islower() for c in v):
            raise ValueError('Password must contain at least one lowercase letter')
        if not any(c.isdigit() for c in v):
            raise ValueError('Password must contain at least one digit')
        return v


class UserLogin(BaseModel):
    """User login request."""
    email: EmailStr
    password: str


class Token(BaseModel):
    """JWT token response."""
    access_token: str
    token_type: str = "bearer"
    user_id: UUID


class TokenWithRefresh(BaseModel):
    """JWT token response with refresh token."""
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    user_id: UUID


class UserResponse(BaseModel):
    """User information response."""
    id: UUID
    email: str
    full_name: Optional[str]
    is_active: bool
    role: UserRole = UserRole.USER
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class UserAdminUpdate(BaseModel):
    """Admin update request for a user."""
    is_active: Optional[bool] = None
    role: Optional[UserRole] = None


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
    content_hash: Optional[str] = None
    labels: Optional[List[str]] = None


class PresignedUploadResponse(BaseModel):
    """Response for presigned upload URL request."""
    document_id: UUID
    presigned_url: str
    presigned_fields: dict
    s3_key: str
    confirm_url: str


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
    content_hash: Optional[str] = None
    labels: List[str] = []
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class DocumentLabelsUpdate(BaseModel):
    """Request to update document labels."""
    labels: List[str]


# ============================================================================
# Form Schemas
# ============================================================================

VALID_FIELD_TYPES = {"text", "number", "enum", "boolean", "array", "object"}

FIELD_TYPE_ALIASES = {
    # human-friendly labels
    "text":             "text",
    "number":           "number",
    "multiple choice":  "enum",
    "table / list":     "array",
    "structured object":"object",
    # developer aliases
    "text_long":       "text",
    "long_text":       "text",
    "dropdown":        "enum",
    "multiple_choice": "enum",
    "select":          "enum",
    "list":            "array",
    "table":           "array",
    "integer":         "number",
    "float":           "number",
    "decimal":         "number",
}


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

    @field_validator('field_type', mode='before')
    @classmethod
    def normalize_field_type(cls, v: str) -> str:
        normalized = FIELD_TYPE_ALIASES.get(v.lower().strip(), v.lower().strip())
        if normalized not in VALID_FIELD_TYPES:
            raise ValueError(
                f"Invalid field_type '{v}'. Must be one of: {sorted(VALID_FIELD_TYPES)}"
            )
        return normalized


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
    enable_review: Optional[bool] = None


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
    extraction_id: Optional[UUID] = None
    job_id: Optional[UUID] = None
    extraction_type: str = 'ai'
    project_id: UUID
    form_id: UUID
    document_id: UUID
    extracted_data: Dict[str, Any]
    evaluation_metrics: Optional[Dict[str, Any]]
    extracted_by: Optional[UUID] = None
    reviewer_role: Optional[str] = None
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


# ============================================================================
# Issue Report Schemas
# ============================================================================

class IssueCreate(BaseModel):
    """Issue report creation request."""
    title: str = Field(..., min_length=1, max_length=255)
    description: str = Field(..., min_length=1)
    category: IssueCategory = IssueCategory.BUG
    priority: IssuePriority = IssuePriority.MEDIUM
    page_url: Optional[str] = Field(None, max_length=500)
    browser_info: Optional[str] = Field(None, max_length=500)
    steps_to_reproduce: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class IssueResponse(BaseModel):
    """Issue report response."""
    id: UUID
    user_id: Optional[UUID]
    user_email: Optional[str]
    title: str
    description: str
    category: str
    priority: str
    page_url: Optional[str]
    browser_info: Optional[str]
    steps_to_reproduce: Optional[str]
    status: str
    metadata: Dict[str, Any]
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


# ============================================================================
# Project Member Schemas
# ============================================================================

class ProjectMemberPermissions(BaseModel):
    """Permission flags for a project member."""
    can_view_docs: bool = True
    can_upload_docs: bool = False
    can_create_forms: bool = False
    can_run_extractions: bool = False
    can_view_results: bool = True
    can_adjudicate: bool = False
    can_qa_review: bool = False
    can_manage_assignments: bool = False


class ProjectMemberResponse(BaseModel):
    """Project member information response."""
    id: UUID
    project_id: UUID
    user_id: UUID
    email: str
    full_name: Optional[str]
    can_view_docs: bool
    can_upload_docs: bool
    can_create_forms: bool
    can_run_extractions: bool
    can_view_results: bool
    can_adjudicate: bool = False
    can_qa_review: bool = False
    can_manage_assignments: bool = False
    invited_by: Optional[UUID] = None
    created_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class ProjectMemberInvite(BaseModel):
    """Request to invite a user to a project."""
    email: EmailStr
    can_view_docs: bool = True
    can_upload_docs: bool = False
    can_create_forms: bool = False
    can_run_extractions: bool = False
    can_view_results: bool = True
    can_adjudicate: bool = False
    can_qa_review: bool = False
    can_manage_assignments: bool = False


class ProjectMemberUpdate(BaseModel):
    """Request to update a member's permissions."""
    can_view_docs: Optional[bool] = None
    can_upload_docs: Optional[bool] = None
    can_create_forms: Optional[bool] = None
    can_run_extractions: Optional[bool] = None
    can_view_results: Optional[bool] = None
    can_adjudicate: Optional[bool] = None
    can_qa_review: Optional[bool] = None
    can_manage_assignments: Optional[bool] = None


class MyPermissionsResponse(BaseModel):
    """Current user's effective permissions for a project."""
    is_owner: bool
    can_view_docs: bool
    can_upload_docs: bool
    can_create_forms: bool
    can_run_extractions: bool
    can_view_results: bool
    can_adjudicate: bool = False
    can_qa_review: bool = False
    can_manage_assignments: bool = False


# ============================================================================
# Consensus Results Schema
# ============================================================================

class ConsensusResultResponse(BaseModel):
    """Response schema for a consensus_results row."""
    id: UUID
    project_id: UUID
    form_id: UUID
    document_id: UUID
    review_mode: str
    field_decisions: Dict[str, Any]
    agreed_count: int
    disputed_count: int
    total_fields: int
    agreement_pct: Optional[int]
    created_by: Optional[UUID]
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


# ============================================================================
# User Settings Schemas
# ============================================================================

class UserSettingsResponse(BaseModel):
    """User settings response."""
    id: UUID
    user_id: UUID
    export_format: str
    export_date_format: str
    export_include_metadata: bool
    export_include_confidence: bool
    notify_email: bool
    notify_browser: bool
    notify_extraction_completed: bool
    notify_extraction_failed: bool
    notify_code_generation: bool
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class UserSettingsUpdate(BaseModel):
    """User settings partial update (PATCH)."""
    export_format: Optional[str] = None
    export_date_format: Optional[str] = None
    export_include_metadata: Optional[bool] = None
    export_include_confidence: Optional[bool] = None
    notify_email: Optional[bool] = None
    notify_browser: Optional[bool] = None
    notify_extraction_completed: Optional[bool] = None
    notify_extraction_failed: Optional[bool] = None
    notify_code_generation: Optional[bool] = None


# ============================================================================
# Manual Extraction Schema (updated for reviewer role)
# ============================================================================

class ManualExtractionCreate(BaseModel):
    """Manual or consensus extraction submission."""
    document_id: UUID
    form_id: UUID
    extracted_data: Dict[str, Any]
    extraction_type: str = "manual"
    reviewer_role: Optional[str] = None


# ============================================================================
# Review Assignment Schemas
# ============================================================================

class ReviewAssignmentCreate(BaseModel):
    """Single review assignment creation."""
    document_id: UUID
    reviewer_user_id: UUID
    reviewer_role: ReviewerRole

class BulkAssignmentCreate(BaseModel):
    """Bulk assignment creation request."""
    project_id: UUID
    assignments: List[ReviewAssignmentCreate]

class AutoAssignRequest(BaseModel):
    """Auto-assign request."""
    project_id: UUID
    reviewer_1_id: UUID
    reviewer_2_id: UUID
    adjudicator_id: UUID
    document_ids: Optional[List[UUID]] = None

class AssignmentStatusUpdate(BaseModel):
    """Assignment status update."""
    status: AssignmentStatus

class ReviewAssignmentResponse(BaseModel):
    """Review assignment response."""
    id: UUID
    project_id: UUID
    document_id: UUID
    reviewer_user_id: UUID
    reviewer_role: str
    status: str
    assigned_by: Optional[UUID]
    assigned_at: datetime
    started_at: Optional[datetime]
    completed_at: Optional[datetime]
    is_training: bool = False
    gold_standard_result_id: Optional[UUID] = None
    document_filename: Optional[str] = None
    reviewer_name: Optional[str] = None
    forms_completed: int = 0
    forms_total: int = 0
    form_details: Optional[List[dict]] = None

    model_config = ConfigDict(from_attributes=True)


# ============================================================================
# Adjudication Schemas
# ============================================================================

class FieldResolution(BaseModel):
    """Resolution for a single field during adjudication."""
    reviewer_1_value: Optional[Any] = None
    reviewer_2_value: Optional[Any] = None
    agreed: bool = False
    final_value: Optional[Any] = None
    resolution_source: str = "agreed"  # reviewer_1|reviewer_2|custom|agreed
    adjudicator_note: Optional[str] = None

class AdjudicationResolveRequest(BaseModel):
    """Request to save adjudication decisions."""
    project_id: UUID
    form_id: UUID
    document_id: UUID
    reviewer_1_result_id: Optional[UUID] = None
    reviewer_2_result_id: Optional[UUID] = None
    field_resolutions: Dict[str, Any]
    status: str = "in_progress"

class AdjudicationResultResponse(BaseModel):
    """Adjudication result response."""
    id: UUID
    project_id: UUID
    form_id: UUID
    document_id: UUID
    adjudicator_id: UUID
    reviewer_1_result_id: Optional[UUID] = None
    reviewer_2_result_id: Optional[UUID] = None
    field_resolutions: Dict[str, Any]
    agreed_count: int = 0
    disagreed_count: int = 0
    total_fields: int = 0
    agreement_pct: Optional[float] = None
    status: str
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


# ============================================================================
# QA Review Schemas
# ============================================================================

class QASampleRequest(BaseModel):
    """Request to generate QA sample."""
    project_id: UUID
    form_id: UUID
    sample_percentage: int = Field(default=20, ge=1, le=100)

class QAReviewSaveRequest(BaseModel):
    """Save QA review request."""
    project_id: UUID
    form_id: UUID
    document_id: UUID
    source_result_id: Optional[UUID] = None
    source_adjudication_id: Optional[UUID] = None
    status: str = "in_progress"
    field_comments: Dict[str, Any] = Field(default_factory=dict)
    overall_comment: Optional[str] = None

class QAFlagResolveRequest(BaseModel):
    """Resolve a QA flag."""
    field_name: str
    resolved_by: UUID

class QAReviewResponse(BaseModel):
    """QA review response."""
    id: UUID
    project_id: UUID
    form_id: UUID
    document_id: UUID
    qa_reviewer_id: UUID
    source_result_id: Optional[UUID] = None
    source_adjudication_id: Optional[UUID] = None
    status: str
    field_comments: Dict[str, Any]
    overall_comment: Optional[str]
    flagged_field_count: int = 0
    total_fields_reviewed: int = 0
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


# ============================================================================
# Controlled Vocabulary Schemas
# ============================================================================

class VocabularyTermSchema(BaseModel):
    """A single vocabulary term."""
    term: str
    synonyms: Optional[List[str]] = None
    code: Optional[str] = None

class ControlledVocabularyCreate(BaseModel):
    """Create vocabulary request."""
    project_id: Optional[UUID] = None
    name: str = Field(..., min_length=1, max_length=255)
    description: Optional[str] = None
    terms: List[VocabularyTermSchema] = Field(default_factory=list)
    source: str = "custom"

class ControlledVocabularyUpdate(BaseModel):
    """Update vocabulary request."""
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    description: Optional[str] = None
    terms: Optional[List[VocabularyTermSchema]] = None

class ControlledVocabularyResponse(BaseModel):
    """Vocabulary response."""
    id: UUID
    project_id: Optional[UUID]
    name: str
    description: Optional[str]
    terms: List[Any]
    source: str
    created_by: Optional[UUID]
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)

class FieldVocabularyMappingCreate(BaseModel):
    """Map vocabulary to form field."""
    form_id: UUID
    field_name: str
    vocabulary_id: UUID
    validation_mode: str = "suggest"

class FieldVocabularyMappingResponse(BaseModel):
    """Field vocabulary mapping response."""
    id: UUID
    form_id: UUID
    field_name: str
    vocabulary_id: UUID
    validation_mode: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


# ============================================================================
# Validation Rule Schemas
# ============================================================================

class ValidationRuleCreate(BaseModel):
    """Create validation rule request."""
    form_id: UUID
    field_name: str
    rule_type: ValidationRuleType
    rule_config: Dict[str, Any]
    severity: str = "warning"
    message: str

class ValidationRuleUpdate(BaseModel):
    """Update validation rule request."""
    rule_config: Optional[Dict[str, Any]] = None
    severity: Optional[str] = None
    message: Optional[str] = None
    is_active: Optional[bool] = None

class ValidationRuleResponse(BaseModel):
    """Validation rule response."""
    id: UUID
    form_id: UUID
    field_name: str
    rule_type: str
    rule_config: Dict[str, Any]
    severity: str
    message: str
    is_active: bool
    created_by: Optional[UUID]
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


# ============================================================================
# Data Cleaning Schemas
# ============================================================================

class BulkEditCell(BaseModel):
    """Single cell edit in bulk edit operation."""
    document_id: UUID
    field_name: str
    old_value: Optional[Any] = None
    new_value: Any

class BulkEditRequest(BaseModel):
    """Bulk edit request."""
    project_id: UUID
    form_id: UUID
    edits: List[BulkEditCell]

class DataViolation(BaseModel):
    """A data validation violation."""
    field_name: str
    rule_id: str
    severity: str
    message: str

class DataCleaningRow(BaseModel):
    """A single row in the data cleaning grid."""
    document_id: UUID
    filename: str
    data_source: str
    values: Dict[str, Any]
    violations: List[DataViolation] = Field(default_factory=list)


# ============================================================================
# Source Linking Schemas
# ============================================================================

class SourceLocationSchema(BaseModel):
    """Location of a source text snippet in the original document."""
    page: int
    start_char: int
    end_char: int
    matched_text: Optional[str] = None
    confidence: float

class SourceIndexEntry(BaseModel):
    """A single entry in the source index."""
    field: str
    start_char: int
    end_char: int

class SourceIndexResponse(BaseModel):
    """Inverted index mapping pages to extracted fields."""
    page_index: Dict[str, List[Dict[str, Any]]]

class PageMapResponse(BaseModel):
    """Page boundary map for a document's markdown."""
    pages: List[Dict[str, Any]]


# ============================================================================
# Audit Trail Schemas
# ============================================================================

class AuditTrailEntryResponse(BaseModel):
    """Audit trail entry response."""
    id: UUID
    user_id: UUID
    project_id: Optional[UUID]
    entity_type: str
    entity_id: UUID
    action: str
    field_name: Optional[str]
    old_value: Optional[Any]
    new_value: Optional[Any]
    metadata: Optional[Dict[str, Any]]
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


# ============================================================================
# IRR Metrics Schemas
# ============================================================================

class IRRMetricResponse(BaseModel):
    """Inter-rater reliability metric response."""
    id: UUID
    project_id: UUID
    form_id: UUID
    metric_type: str
    scope: str
    scope_key: Optional[str]
    value: Optional[float]
    confidence_interval: Optional[Dict[str, Any]]
    sample_size: Optional[int]
    computed_at: datetime
    metadata: Optional[Dict[str, Any]]

    model_config = ConfigDict(from_attributes=True)


# Resolve forward references
FieldDefinition.model_rebuild()
