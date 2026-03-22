"""
Application configuration management.
Loads settings from environment variables with sensible defaults.
"""

from pydantic_settings import BaseSettings
from pydantic import computed_field, model_validator
from functools import lru_cache
from pathlib import Path

import config.models as _m


# Compute project root once at module level
_PROJECT_ROOT = Path(__file__).parent.parent  # = backend/


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # Application
    APP_NAME: str = "eviStreams"
    APP_VERSION: str = "1.0.0"
    DEBUG: bool = False
    ENVIRONMENT: str = "development"  # development, staging, production
    LOG_LEVEL: str = "INFO"   # DEBUG, INFO, WARNING, ERROR, CRITICAL

    # API
    API_V1_PREFIX: str = "/api/v1"
    FRONTEND_URL: str = ""  # Override for production (e.g., "https://app.evistream.com")
    BACKEND_CORS_ORIGINS: list[str] = ["http://localhost:3000", "http://localhost:3001"]

    # Security
    SECRET_KEY: str  # REQUIRED: Generate with: openssl rand -hex 32
    REFRESH_SECRET_KEY: str = ""  # Separate secret for refresh tokens; falls back to SECRET_KEY
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60  # 60 minutes
    REFRESH_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 7  # 7 days

    # Database (Supabase)
    SUPABASE_URL: str
    SUPABASE_KEY: str
    SUPABASE_SERVICE_KEY: str  # For admin operations
    DATABASE_URL: str = ""  # Optional: Direct PostgreSQL connection for LangGraph checkpoints

    # Redis
    REDIS_URL: str = "redis://localhost:6380/0"
    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = 6380
    REDIS_DB: int = 0
    REDIS_CACHE_DB: int = 1
    REDIS_SESSION_DB: int = 2

    # Celery
    CELERY_BROKER_URL: str = "redis://localhost:6380/0"
    CELERY_RESULT_BACKEND: str = "redis://localhost:6380/1"
    CELERY_TASK_TIME_LIMIT: int = 3600  # 1 hour
    CELERY_TASK_SOFT_TIME_LIMIT: int = 3300  # 55 minutes

    # AWS
    AWS_ACCESS_KEY_ID: str
    AWS_SECRET_ACCESS_KEY: str
    AWS_REGION: str = "us-east-1"
    S3_BUCKET: str = "evistream-production"

    # CloudWatch Logging
    CLOUDWATCH_ENABLED: bool = False
    CLOUDWATCH_LOG_GROUP: str = "/evistream/development"
    CLOUDWATCH_SEND_INTERVAL: int = 10  # seconds; watchtower batches in-memory, flushes async

    # File Upload
    MAX_UPLOAD_SIZE: int = 100 * 1024 * 1024  # 100 MB
    ALLOWED_EXTENSIONS: set[str] = {"pdf"}

    # Core Logic — defaults sourced from config.models (single manifest)
    DEFAULT_MODEL: str = _m.EXTRACTION_PRIMARY_MODEL
    EVALUATION_MODEL: str = _m.EVALUATION_PRIMARY_MODEL
    MAX_TOKENS: int = _m.EXTRACTION_MAX_TOKENS
    TEMPERATURE: float = _m.EXTRACTION_TEMPERATURE
    EVALUATION_TEMPERATURE: float = _m.EVALUATION_TEMPERATURE
    BATCH_CONCURRENCY: int = _m.EXTRACTION_BATCH_CONCURRENCY
    EVALUATION_CONCURRENCY: int = _m.EVALUATION_CONCURRENCY
    EXTRACTION_BATCH_CONCURRENCY: int = _m.EXTRACTION_BATCH_CONCURRENCY
    MAX_CONCURRENT_EXTRACTIONS_PER_USER: int = _m.EXTRACTION_MAX_JOBS_PER_USER
    MAX_DOCUMENTS_PER_EXTRACTION_JOB: int = _m.EXTRACTION_MAX_DOCS_PER_JOB
    EXTRACTION_TASK_CONCURRENCY: int = _m.EXTRACTION_TASK_CONCURRENCY

    # LLM API Keys (optional)
    GEMINI_API_KEY: str = ""
    OPENAI_API_KEY: str = ""
    ANTHROPIC_API_KEY: str = ""

    @model_validator(mode="after")
    def _validate_secret_key(self) -> "Settings":
        if not self.SECRET_KEY or not self.SECRET_KEY.strip():
            raise ValueError(
                "SECRET_KEY is required and must not be empty. "
                "Generate one with: openssl rand -hex 32"
            )
        return self

    @model_validator(mode="after")
    def _merge_frontend_url_into_cors(self) -> "Settings":
        if self.FRONTEND_URL and self.FRONTEND_URL not in self.BACKEND_CORS_ORIGINS:
            self.BACKEND_CORS_ORIGINS = [*self.BACKEND_CORS_ORIGINS, self.FRONTEND_URL]
        return self

    # Computed path properties
    @computed_field
    @property
    def PROJECT_ROOT(self) -> Path:
        """Project root directory."""
        return _PROJECT_ROOT

    @computed_field
    @property
    def CACHE_DIR(self) -> Path:
        """Cache directory path."""
        return _PROJECT_ROOT / "cache"

    class Config:
        env_file = str(_PROJECT_ROOT / ".env")
        case_sensitive = True
        extra = "ignore"  # Ignore extra fields from environment


@lru_cache()
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()


# Global settings instance
settings = get_settings()
