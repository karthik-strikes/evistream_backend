"""Pydantic schemas for API request/response validation."""

from pydantic import BaseModel, EmailStr, Field, ConfigDict, field_validator, model_validator
from typing import Optional, List, Dict, Any, Literal
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
    last_seen_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class ForgotPasswordRequest(BaseModel):
    """Forgot password request."""
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    """Reset password request."""
    token: str
    new_password: str = Field(..., min_length=8)

    @field_validator('new_password')
    @classmethod
    def validate_password_strength(cls, v: str) -> str:
        if not any(c.isupper() for c in v):
            raise ValueError('Password must contain at least one uppercase letter')
        if not any(c.islower() for c in v):
            raise ValueError('Password must contain at least one lowercase letter')
        if not any(c.isdigit() for c in v):
            raise ValueError('Password must contain at least one digit')
        return v


class UserProfileUpdate(BaseModel):
    """Update profile fields for the current user."""
    full_name: Optional[str] = None
    email: Optional[EmailStr] = None


class ChangePasswordRequest(BaseModel):
    """Change password request for the current user."""
    current_password: str
    new_password: str = Field(..., min_length=8)

    @field_validator('new_password')
    @classmethod
    def validate_password_strength(cls, v: str) -> str:
        if not any(c.isupper() for c in v):
            raise ValueError('Password must contain at least one uppercase letter')
        if not any(c.islower() for c in v):
            raise ValueError('Password must contain at least one lowercase letter')
        if not any(c.isdigit() for c in v):
            raise ValueError('Password must contain at least one digit')
        return v


class UserAdminUpdate(BaseModel):
    """Admin update request for a user."""
    is_active: Optional[bool] = None
    role: Optional[UserRole] = None


class UserAdminCreate(BaseModel):
    """Admin creates a new user account."""
    email: EmailStr
    full_name: Optional[str] = None
    password: str = Field(..., min_length=8)
    role: UserRole = UserRole.USER

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

VALID_FIELD_TYPES = {"text", "number", "select", "boolean", "array"}

FIELD_TYPE_ALIASES = {
    # canonical types
    "text":             "text",
    "number":           "number",
    "select":           "select",
    "boolean":          "boolean",
    "array":            "array",
    # human-friendly labels
    "multiple choice":  "select",
    "table / list":     "array",
    # legacy aliases (enum → select)
    "enum":            "select",
    "dropdown":        "select",
    "multiple_choice": "select",
    # other aliases
    "text_long":       "text",
    "long_text":       "text",
    "list":            "array",
    "table":           "array",
    "object":          "array",
    "structured object":"array",
    "integer":         "number",
    "float":           "number",
    "decimal":         "number",
}


class FieldDefinition(BaseModel):
    """Form field definition."""
    field_name: str
    display_name: Optional[str] = None
    field_description: str
    field_type: str  # text, number, select, boolean, array
    field_control_type: Optional[str] = None  # dropdown, checkbox_group_with_text, etc.
    options: Optional[List[str]] = None
    multiple: Optional[bool] = False  # For select fields: allow multiple selections
    example: Optional[str] = None
    extraction_hints: Optional[str] = None
    subform_fields: Optional[List['FieldDefinition']] = None
    # Review-time calibration (set via PATCH /forms/{id}/fields).
    # Spliced directly into signatures.py — what you see is what the LLM uses.
    examples: Optional[List[Dict[str, Any]]] = None  # [{value, source_text?, note?}]
    hints: Optional[List[str]] = None
    rules: Optional[List[str]] = None
    # Table extraction strategy (only meaningful on array fields).
    # Mirrors schema_def.output_fields[].extraction_strategy / .anchor_columns
    # so the UI can roundtrip without going through schema_def.
    extraction_strategy: Optional[str] = None   # 'single_call' | 'row_then_columns'
    anchor_columns: Optional[List[str]] = None

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
    save_as_draft: bool = False


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


class ReviewNote(BaseModel):
    """Structured reviewer note targeting a specific part of the decomposition."""
    target_type: Literal["field", "group", "stage", "pipeline"]
    target_ref: str
    comment: str = Field(..., min_length=3, max_length=2000)


class RejectDecompositionRequest(BaseModel):
    """Reject decomposition request with optional structured notes."""
    feedback: Optional[str] = None
    notes: List[ReviewNote] = []
    accepted_refs: List[str] = []

    @model_validator(mode='after')
    def at_least_one(self) -> 'RejectDecompositionRequest':
        if not self.feedback and not self.notes:
            raise ValueError("At least one of feedback or notes must be provided")
        return self


class FieldEditUpdate(BaseModel):
    """Per-field calibration update from HITL review."""
    field_name: str
    description: Optional[str] = None
    examples: Optional[List[Dict[str, Any]]] = None
    hints: Optional[List[str]] = None
    rules: Optional[List[str]] = None
    options: Optional[List[str]] = None         # select-field answer choices
    extraction_strategy: Optional[str] = None   # 'single_call' | 'row_then_columns'
    anchor_columns: Optional[List[str]] = None  # column names used as row identifiers


class FieldEditsRequest(BaseModel):
    """Bulk field-level review edits (examples/hints/rules)."""
    field_updates: List[FieldEditUpdate] = Field(..., min_length=1)


class AddFieldRequest(BaseModel):
    """Request to add a new field to an active form's signature."""
    field_name: str
    field_type: str  # text | number | boolean | select | array
    display_name: Optional[str] = None
    options: Optional[List[str]] = None
    multiple: Optional[bool] = False
    target_signature_class: str
    description: str = Field(..., min_length=1)  # required — user's authoritative description
    examples: Optional[List[Dict[str, Any]]] = None  # [{value, source_text?}], user-supplied anchor cases


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
    model_name: Optional[str] = None  # LLM used for AI extraction, derived from jobs.input_data.model
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
    can_run_manual_extractions: bool = False
    can_view_results: bool = True
    can_adjudicate: bool = False
    can_qa_review: bool = False
    can_manage_assignments: bool = False
    can_manage_members: bool = False


class ProjectMemberResponse(BaseModel):
    """Project member information response."""
    id: UUID
    project_id: UUID
    user_id: UUID
    email: str
    full_name: Optional[str]
    role: str = "member"
    can_view_docs: bool
    can_upload_docs: bool
    can_create_forms: bool
    can_run_extractions: bool
    can_run_manual_extractions: bool = False
    can_view_results: bool
    can_adjudicate: bool = False
    can_qa_review: bool = False
    can_manage_assignments: bool = False
    can_manage_members: bool = False
    invited_by: Optional[UUID] = None
    created_at: Optional[datetime] = None
    last_seen_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class ProjectMemberInvite(BaseModel):
    """Request to invite a user to a project."""
    email: EmailStr
    role: str = "member"
    # Individual flags — only used when role='member'
    can_view_docs: bool = True
    can_upload_docs: bool = False
    can_create_forms: bool = False
    can_run_extractions: bool = False
    can_run_manual_extractions: bool = False
    can_view_results: bool = True
    can_adjudicate: bool = False
    can_qa_review: bool = False
    can_manage_assignments: bool = False
    can_manage_members: bool = False


class ProjectMemberUpdate(BaseModel):
    """Request to update a member's role and/or permissions."""
    role: Optional[str] = None
    # Individual flags — only apply when role='member'
    can_view_docs: Optional[bool] = None
    can_upload_docs: Optional[bool] = None
    can_create_forms: Optional[bool] = None
    can_run_extractions: Optional[bool] = None
    can_run_manual_extractions: Optional[bool] = None
    can_view_results: Optional[bool] = None
    can_adjudicate: Optional[bool] = None
    can_qa_review: Optional[bool] = None
    can_manage_assignments: Optional[bool] = None
    can_manage_members: Optional[bool] = None


class ProjectInvitationCreate(BaseModel):
    """Request to create a project invitation."""
    email: EmailStr
    role: str = "member"
    can_view_docs: bool = True
    can_upload_docs: bool = False
    can_create_forms: bool = False
    can_run_extractions: bool = False
    can_run_manual_extractions: bool = False
    can_view_results: bool = True
    can_adjudicate: bool = False
    can_qa_review: bool = False
    can_manage_assignments: bool = False
    can_manage_members: bool = False


class ProjectInvitationResponse(BaseModel):
    """Project invitation response."""
    id: UUID
    project_id: UUID
    email: str
    role: str
    can_view_docs: bool
    can_upload_docs: bool
    can_create_forms: bool
    can_run_extractions: bool
    can_run_manual_extractions: bool = False
    can_view_results: bool
    can_adjudicate: bool
    can_qa_review: bool
    can_manage_assignments: bool
    can_manage_members: bool
    invited_by: Optional[UUID] = None
    invited_by_name: Optional[str] = None
    expires_at: datetime
    accepted_at: Optional[datetime] = None
    revoked_at: Optional[datetime] = None
    created_at: datetime
    accept_url: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class InvitationPreview(BaseModel):
    """Public invitation preview for the accept page."""
    project_id: UUID
    project_name: str
    role: str
    invited_by_name: Optional[str] = None
    expires_at: datetime


class AcceptInvitationRequest(BaseModel):
    """Request body to accept an invitation."""
    token: str


class MyPermissionsResponse(BaseModel):
    """Current user's effective permissions for a project."""
    is_owner: bool
    is_admin: bool = False
    role: str = "member"
    can_view_docs: bool
    can_upload_docs: bool
    can_create_forms: bool
    can_run_extractions: bool
    can_run_manual_extractions: bool = False
    can_view_results: bool
    can_adjudicate: bool = False
    can_qa_review: bool = False
    can_manage_assignments: bool = False
    can_manage_members: bool = False


class OwnershipTransferRequest(BaseModel):
    """Request to transfer project ownership."""
    new_owner_id: UUID
    previous_owner_role: Literal["manager", "member", "viewer", "none"] = "manager"


class PermissionAuditLogResponse(BaseModel):
    """Permission change audit log entry."""
    id: UUID
    project_id: UUID
    actor_id: UUID
    target_user_id: Optional[UUID] = None
    action: str
    old_values: Optional[dict] = None
    new_values: Optional[dict] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class AdminAuditLogEntry(BaseModel):
    """Enriched audit log entry for admin global view."""
    id: UUID
    project_id: Optional[UUID] = None
    actor_id: Optional[UUID] = None
    target_user_id: Optional[UUID] = None
    action: str
    old_values: Optional[dict] = None
    new_values: Optional[dict] = None
    created_at: datetime
    actor_name: Optional[str] = None
    actor_email: Optional[str] = None
    target_name: Optional[str] = None
    target_email: Optional[str] = None
    project_name: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class AdminAuditLogResponse(BaseModel):
    """Paginated response for admin global audit log."""
    entries: List[AdminAuditLogEntry]
    total: int
    page: int
    page_size: int


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
    extraction_model: Optional[str] = None
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
    extraction_model: Optional[str] = None


class AvailableModel(BaseModel):
    """One row in the user-facing model picker."""
    id: str
    label: str
    provider: str


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
