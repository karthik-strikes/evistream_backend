"""
Application configuration management.
Loads settings from environment variables with sensible defaults.
"""

from pydantic_settings import BaseSettings
from pydantic import computed_field
from functools import lru_cache
from pathlib import Path


# Compute project root once at module level
_PROJECT_ROOT = Path(__file__).parent.parent.parent


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # Application
    APP_NAME: str = "eviStream"
    APP_VERSION: str = "1.0.0"
    DEBUG: bool = False
    ENVIRONMENT: str = "development"  # development, staging, production

    # API
    API_V1_PREFIX: str = "/api/v1"
    BACKEND_CORS_ORIGINS: list[str] = ["http://localhost:3000"]

    # Security
    SECRET_KEY: str  # REQUIRED: Generate with: openssl rand -hex 32
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 7  # 7 days

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
    CELERY_RESULT_BACKEND: str = "redis://localhost:6380/0"
    CELERY_TASK_TIME_LIMIT: int = 3600  # 1 hour
    CELERY_TASK_SOFT_TIME_LIMIT: int = 3300  # 55 minutes

    # AWS
    AWS_ACCESS_KEY_ID: str
    AWS_SECRET_ACCESS_KEY: str
    AWS_REGION: str = "us-east-1"
    S3_BUCKET: str = "evistream-production"

    # File Upload
    MAX_UPLOAD_SIZE: int = 100 * 1024 * 1024  # 100 MB
    ALLOWED_EXTENSIONS: set[str] = {"pdf"}

    # Core Logic (from existing config)
    DEFAULT_MODEL: str = "gemini/gemini-3-pro-preview"
    EVALUATION_MODEL: str = "gemini/gemini-2.5-flash"
    MAX_TOKENS: int = 20000
    TEMPERATURE: float = 1.0
    EVALUATION_TEMPERATURE: float = 0.0
    BATCH_CONCURRENCY: int = 5
    EVALUATION_CONCURRENCY: int = 20

    # LLM API Keys (optional)
    GEMINI_API_KEY: str = ""
    OPENAI_API_KEY: str = ""
    ANTHROPIC_API_KEY: str = ""

    # Computed path properties
    @computed_field
    @property
    def PROJECT_ROOT(self) -> Path:
        """Project root directory."""
        return _PROJECT_ROOT

    @computed_field
    @property
    def UPLOAD_DIR(self) -> Path:
        """Upload directory path."""
        return _PROJECT_ROOT / "storage" / "uploads"

    @computed_field
    @property
    def CACHE_DIR(self) -> Path:
        """Cache directory path."""
        return _PROJECT_ROOT / "cache"

    class Config:
        env_file = str(_PROJECT_ROOT / "backend" / ".env")
        case_sensitive = True
        extra = "ignore"  # Ignore extra fields from environment


@lru_cache()
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()


# Global settings instance
settings = get_settings()
