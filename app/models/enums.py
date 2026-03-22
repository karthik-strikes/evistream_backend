"""Enums for database models."""

from enum import Enum


class UserRole(str, Enum):
    """User roles for access control."""
    ADMIN = "admin"
    USER = "user"


class JobType(str, Enum):
    """Types of background jobs."""
    PDF_PROCESSING = "pdf_processing"
    FORM_GENERATION = "form_generation"
    EXTRACTION = "extraction"


class JobStatus(str, Enum):
    """Job execution status."""
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class FormStatus(str, Enum):
    """Form generation status."""
    DRAFT = "draft"
    GENERATING = "generating"
    AWAITING_REVIEW = "awaiting_review"
    REGENERATING = "regenerating"
    ACTIVE = "active"
    FAILED = "failed"


class DocumentStatus(str, Enum):
    """Document processing status."""
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class IssueCategory(str, Enum):
    """Issue report category."""
    BUG = "bug"
    UI_ISSUE = "ui_issue"
    FEATURE_REQUEST = "feature_request"
    PERFORMANCE = "performance"
    OTHER = "other"


class IssuePriority(str, Enum):
    """Issue report priority."""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class IssueStatus(str, Enum):
    """Issue report status."""
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    RESOLVED = "resolved"
    CLOSED = "closed"
    WONT_FIX = "wont_fix"


class ReviewerRole(str, Enum):
    """Reviewer roles for extraction assignments."""
    REVIEWER_1 = "reviewer_1"
    REVIEWER_2 = "reviewer_2"
    ADJUDICATOR = "adjudicator"
    QA_REVIEWER = "qa_reviewer"


class AssignmentStatus(str, Enum):
    """Review assignment status."""
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    SKIPPED = "skipped"


class QAStatus(str, Enum):
    """QA review status."""
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    PASSED = "passed"
    FLAGGED = "flagged"


class ValidationRuleType(str, Enum):
    """Validation rule types."""
    RANGE = "range"
    FORMAT = "format"
    REQUIRED = "required"
    CROSS_FIELD = "cross_field"
    REGEX = "regex"


class BlindingMode(str, Enum):
    """Blinding modes for review."""
    FULL = "full"
    PARTIAL = "partial"
    NONE = "none"
