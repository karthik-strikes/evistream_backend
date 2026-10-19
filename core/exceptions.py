"""
Custom exceptions for eviStreams core module.

Provides a hierarchical exception system for better error handling and debugging.
All exceptions inherit from CoreException for easy catching of all core errors.
"""


class CoreException(Exception):
    """
    Base exception for all core errors.

    All custom exceptions in the core module inherit from this class,
    allowing callers to catch all core-related errors with a single except clause.
    """
    pass


class ConfigurationError(CoreException):
    """
    Configuration or environment setup error.

    Raised when:
    - Required environment variables are missing
    - Configuration validation fails
    - Invalid configuration values are provided
    """
    pass


class ValidationError(CoreException):
    """
    Input validation failed.

    Raised when:
    - Form data doesn't match expected schema
    - Field names are invalid or duplicate
    - Required fields are missing
    - Field values don't match type constraints
    """
    pass


class GenerationError(CoreException):
    """
    Code generation failed.

    Base class for all code generation errors.
    Raised when DSPy code generation process fails for any reason.
    """
    pass


class DecompositionError(GenerationError):
    """
    Form decomposition failed.

    Raised when:
    - LLM fails to decompose form into signatures
    - Decomposition validation fails
    - Field coverage is incomplete
    - Dependency graph has cycles
    """
    pass


class SignatureGenerationError(GenerationError):
    """
    Signature generation failed.

    Raised when:
    - LLM fails to generate signature code
    - Generated signature code is invalid
    - Signature validation fails
    """
    pass


class ModuleGenerationError(GenerationError):
    """
    Module generation failed.

    Raised when:
    - LLM fails to generate module code
    - Generated module code is invalid
    - Module validation fails
    """
    pass


class LLMError(CoreException):
    """
    LLM API call failed.

    Raised when:
    - LLM API returns an error
    - API key is invalid or missing
    - Rate limit is exceeded
    - Network error occurs
    - LLM response is malformed

    This is typically a retryable error.
    """
    pass


class TimeoutError(CoreException):
    """
    Operation timed out.

    Raised when:
    - LLM API call exceeds timeout
    - Code generation takes too long
    - Workflow execution exceeds time limit

    This is typically a retryable error.
    """
    pass


class FileSystemError(CoreException):
    """
    File system operation failed.

    Raised when:
    - Cannot create task directory
    - Cannot write generated code files
    - Cannot read prompt templates
    - File permissions are insufficient
    """
    pass


class WorkflowError(CoreException):
    """
    Workflow orchestration failed.

    Raised when:
    - Workflow state is invalid
    - Workflow exceeds max attempts
    - Workflow enters unrecoverable state
    """
    pass


__all__ = [
    "CoreException",
    "ConfigurationError",
    "ValidationError",
    "GenerationError",
    "DecompositionError",
    "SignatureGenerationError",
    "ModuleGenerationError",
    "LLMError",
    "TimeoutError",
    "FileSystemError",
    "WorkflowError",
]
