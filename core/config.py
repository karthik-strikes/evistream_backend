"""
Production-ready configuration for eviStreams core module.

Uses Pydantic settings for type-safe configuration with validation,
environment variable support, and environment-specific requirements.
"""

import os
from pathlib import Path
from typing import Literal, Optional, List
from pydantic import Field, field_validator, computed_field
from pydantic_settings import BaseSettings
from dotenv import load_dotenv

import config.models as _m


# Resolve important project directories
_THIS_FILE = Path(__file__).resolve()
_PROJECT_ROOT = _THIS_FILE.parents[1]  # eviStreams project root (backend/)

# Pre-compute paths that need to be used in Field defaults
_DEFAULT_CSV_DIR = os.environ.get("CSV_OUTPUT_DIR", str(_PROJECT_ROOT / "outputs" / "eval_csvs"))
_DEFAULT_JSON_DIR = os.environ.get("JSON_OUTPUT_DIR", str(_PROJECT_ROOT / "outputs" / "eval_jsons"))
_DEFAULT_HISTORY_CSV = _PROJECT_ROOT / "outputs" / "logs" / "dspy_history.csv"

# Load environment variables
load_dotenv()


class CoreSettings(BaseSettings):
    """
    Production-ready configuration settings for core module.

    Validates all settings at startup and enforces environment-specific requirements.
    """

    # ============================================================================
    # Environment Configuration
    # ============================================================================

    ENVIRONMENT: Literal["development", "staging", "production"] = Field(
        default="development",
        description="Application environment"
    )

    DEBUG: bool = Field(
        default=False,
        description="Enable debug mode"
    )

    # ============================================================================
    # LLM Configuration
    # ============================================================================

    DEFAULT_MODEL: str = Field(
        default=_m.EXTRACTION_PRIMARY_MODEL,
        description="Default LLM model for extraction"
    )

    EVALUATION_MODEL: str = Field(
        default=_m.EVALUATION_PRIMARY_MODEL,
        description="LLM model for evaluation (faster model recommended)"
    )

    FALLBACK_MODELS: list = Field(
        default=_m.EXTRACTION_FALLBACK_MODELS,
        description="Fallback models to try if primary model fails (in order)"
    )

    EVALUATION_FALLBACK_MODELS: list = Field(
        default=_m.EVALUATION_FALLBACK_MODELS,
        description="Fallback models for evaluation if primary model fails"
    )

    ENABLE_MODEL_FALLBACK: bool = Field(
        default=True,
        description="Enable automatic fallback to alternative models on failure"
    )

    # ============================================================
    # Circuit Breaker Configuration
    # ============================================================

    CB_ENABLED: bool = Field(
        default=_m.CB_ENABLED,
        description=(
            "Enable circuit breaker for LLM model routing. "
            "Set to False to disable (useful for debugging)."
        )
    )

    CB_FAILURE_THRESHOLD: int = Field(
        default=_m.CB_FAILURE_THRESHOLD,
        ge=1,
        le=20,
        description=(
            "Number of rate limit (429) errors on a model before the circuit "
            "breaker trips to OPEN state. Default=3 means: 3 strikes and you're out."
        )
    )

    CB_RECOVERY_TIMEOUT: int = Field(
        default=_m.CB_RECOVERY_TIMEOUT,
        ge=10,
        le=600,
        description=(
            "Seconds to wait in OPEN state before attempting recovery (HALF_OPEN). "
            "Default=60 matches Gemini's RPM quota reset window."
        )
    )

    CB_HALF_OPEN_SUCCESSES: int = Field(
        default=_m.CB_HALF_OPEN_SUCCESSES,
        ge=1,
        le=10,
        description=(
            "Number of successful probe requests in HALF_OPEN state before "
            "transitioning back to CLOSED (fully recovered). Default=2."
        )
    )

    MAX_TOKENS: int = Field(
        default=_m.EXTRACTION_MAX_TOKENS,
        ge=1000,
        le=100000,
        description="Maximum tokens for LLM responses"
    )

    TEMPERATURE: float = Field(
        default=_m.EXTRACTION_TEMPERATURE,
        ge=0.0,
        le=2.0,
        description="LLM sampling temperature"
    )

    EVALUATION_TEMPERATURE: float = Field(
        default=_m.EVALUATION_TEMPERATURE,
        ge=0.0,
        le=2.0,
        description="Temperature for evaluation (0.0 for deterministic)"
    )

    # ============================================================================
    # API Keys (Environment-specific validation)
    # ============================================================================

    GEMINI_API_KEY: str = Field(
        default="",
        description="Google Gemini API key"
    )

    OPENAI_API_KEY: str = Field(
        default="",
        description="OpenAI API key"
    )

    ANTHROPIC_API_KEY: str = Field(
        default="",
        description="Anthropic Claude API key"
    )

    DATALAB_API_KEY: str = Field(
        default="",
        description="DataLab API key for PDF processing"
    )

    @field_validator("GEMINI_API_KEY", "DATALAB_API_KEY")
    @classmethod
    def validate_required_api_keys(cls, v: str, info) -> str:
        """Validate that required API keys are present in production."""
        # Access values dict through info.data
        env = info.data.get("ENVIRONMENT", "development")
        if env == "production" and not v:
            raise ValueError(
                f"{info.field_name} is required in production environment"
            )
        return v

    # ============================================================================
    # Concurrency Settings
    # ============================================================================

    BATCH_CONCURRENCY: int = Field(
        default=_m.EXTRACTION_BATCH_CONCURRENCY,
        ge=1,
        le=50,
        description="Number of papers to process in parallel"
    )

    EVALUATION_CONCURRENCY: int = Field(
        default=_m.EVALUATION_CONCURRENCY,
        ge=1,
        le=100,
        description="Number of concurrent semantic matching calls"
    )

    # ============================================================================
    # Path Configuration
    # ============================================================================

    # Project root (computed from module location)
    PROJECT_ROOT: Path = Field(
        default=_PROJECT_ROOT,
        description="eviStreams project root directory"
    )

    # Data directories
    DEFAULT_CSV_DIR: str = Field(
        default=_DEFAULT_CSV_DIR,
        description="Default directory for CSV outputs"
    )

    DEFAULT_JSON_DIR: str = Field(
        default=_DEFAULT_JSON_DIR,
        description="Default directory for JSON outputs"
    )

    # DSPy History Logging
    DEFAULT_HISTORY_CSV: Path = Field(
        default=_DEFAULT_HISTORY_CSV,
        description="Path to DSPy history CSV file"
    )

    INCLUDE_FULL_PROMPTS_IN_HISTORY: bool = Field(
        default=False,
        description="Include full prompts in history (increases CSV size)"
    )

    # Cache directories
    CACHE_DIRS: List[str] = Field(
        default=[".semantic_cache", ".evaluation_cache"],
        description="List of cache directory names"
    )

    # ============================================================================
    # Database Configuration (Supabase)
    # ============================================================================

    SUPABASE_URL: str = Field(
        default="",
        description="Supabase project URL"
    )

    SUPABASE_KEY: str = Field(
        default="",
        description="Supabase anon/service key"
    )

    # ============================================================================
    # Code Generation Configuration
    # ============================================================================

    MAX_DECOMPOSITION_TOKENS: int = Field(
        default=_m.CODEGEN_MAX_DECOMPOSITION_TOKENS,
        ge=10000,
        le=100000,
        description="Maximum tokens for form decomposition"
    )

    LLM_TIMEOUT_SECONDS: int = Field(
        default=_m.LLM_TIMEOUT_SECONDS,
        ge=30,
        le=3600,
        description="Timeout for LLM API calls in seconds"
    )

    MAX_GENERATION_ATTEMPTS: int = Field(
        default=_m.CODEGEN_MAX_ATTEMPTS,
        ge=1,
        le=10,
        description="Maximum attempts for code generation on failure"
    )

    # ============================================================================
    # Pydantic Configuration
    # ============================================================================

    model_config = {
        "env_file": ".env",
        "extra": "ignore",  # Ignore extra environment variables
        "validate_assignment": True,  # Validate on assignment
        "case_sensitive": True,  # Environment variables are case-sensitive
    }


# Global settings instance
settings = CoreSettings()


# Backward compatibility: Export individual constants for existing code
PROJECT_ROOT = settings.PROJECT_ROOT
DEFAULT_MODEL = settings.DEFAULT_MODEL
EVALUATION_MODEL = settings.EVALUATION_MODEL
FALLBACK_MODELS = settings.FALLBACK_MODELS
EVALUATION_FALLBACK_MODELS = settings.EVALUATION_FALLBACK_MODELS
ENABLE_MODEL_FALLBACK = settings.ENABLE_MODEL_FALLBACK
CB_ENABLED = settings.CB_ENABLED
CB_FAILURE_THRESHOLD = settings.CB_FAILURE_THRESHOLD
CB_RECOVERY_TIMEOUT = settings.CB_RECOVERY_TIMEOUT
CB_HALF_OPEN_SUCCESSES = settings.CB_HALF_OPEN_SUCCESSES
MAX_TOKENS = settings.MAX_TOKENS
TEMPERATURE = settings.TEMPERATURE
EVALUATION_TEMPERATURE = settings.EVALUATION_TEMPERATURE
BATCH_CONCURRENCY = settings.BATCH_CONCURRENCY
EVALUATION_CONCURRENCY = settings.EVALUATION_CONCURRENCY
DEFAULT_HISTORY_CSV = str(settings.DEFAULT_HISTORY_CSV)
INCLUDE_FULL_PROMPTS_IN_HISTORY = settings.INCLUDE_FULL_PROMPTS_IN_HISTORY
CACHE_DIRS = settings.CACHE_DIRS
SUPABASE_URL = settings.SUPABASE_URL
SUPABASE_KEY = settings.SUPABASE_KEY
DEFAULT_CSV_DIR = settings.DEFAULT_CSV_DIR
DEFAULT_JSON_DIR = settings.DEFAULT_JSON_DIR


__all__ = [
    "settings",
    "CoreSettings",
    "PROJECT_ROOT",
    # Backward compatibility exports
    "DEFAULT_MODEL",
    "EVALUATION_MODEL",
    "FALLBACK_MODELS",
    "EVALUATION_FALLBACK_MODELS",
    "ENABLE_MODEL_FALLBACK",
    "CB_ENABLED",
    "CB_FAILURE_THRESHOLD",
    "CB_RECOVERY_TIMEOUT",
    "CB_HALF_OPEN_SUCCESSES",
    "MAX_TOKENS",
    "TEMPERATURE",
    "EVALUATION_TEMPERATURE",
    "BATCH_CONCURRENCY",
    "EVALUATION_CONCURRENCY",
    "DEFAULT_HISTORY_CSV",
    "INCLUDE_FULL_PROMPTS_IN_HISTORY",
    "CACHE_DIRS",
    "SUPABASE_URL",
    "SUPABASE_KEY",
    "DEFAULT_CSV_DIR",
    "DEFAULT_JSON_DIR",
]
