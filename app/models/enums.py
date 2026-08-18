"""Enums for database models."""

from enum import Enum


class UserRole(str, Enum):
    """User roles for access control."""
    ADMIN = "admin"
    USER = "user"


class ProjectRole(str, Enum):
    """Project-level roles for members."""
    OWNER = "owner"
    MANAGER = "manager"
    MEMBER = "member"
    VIEWER = "viewer"


class JobType(str, Enum):
    """Types of background jobs."""
    PDF_PROCESSING = "pdf_processing"
    FORM_GENERATION = "form_generation"
    EXTRACTION = "extraction"
    IMPORT = "import"


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
    # Imported reference (e.g. from EndNote) that has metadata but no attached
    # PDF yet — a reviewer can attach one via POST /documents/{id}/attach-pdf.
    NEEDS_PDF = "needs_pdf"
    # Readable, but the evidence is THIN: an abstract-only PubMed record, or a
    # registration-only trial with no posted results. Held out of extraction
    # until a reviewer accepts it (metadata_extraction_approved), because a run
    # otherwise reads 250 words of abstract as if it were a full paper and every
    # full-text-only field comes back NR indistinguishably from a real miss.
    METADATA_ONLY = "metadata_only"


class BlocksStatus(str, Enum):
    """Datalab blocks (json/bbox) sidecar call status, tracked independently
    of the overall document processing status."""
    PENDING = "pending"
    COMPLETED = "completed"
    FAILED = "failed"


class DoiSource(str, Enum):
    """How (if at all) a document's DOI was resolved — see doi_service.py.
    Also doubles as the "already attempted" marker so the backfill batch
    endpoint doesn't keep retrying documents that genuinely have no
    discoverable DOI."""
    METADATA = "metadata"
    TEXT = "text"
    CROSSREF = "crossref"
    NONE = "none"


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
