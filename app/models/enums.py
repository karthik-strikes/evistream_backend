"""Enums for database models."""

from enum import Enum


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
