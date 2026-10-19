"""
Code generation service - wraps existing core/generators workflow for backend use.
"""

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from pathlib import Path
from typing import Dict, Any, Optional
import logging

# Maximum time (seconds) allowed for a single code generation workflow run
CODEGEN_TIMEOUT_SECONDS = 600  # 10 minutes

project_root = Path(__file__).parent.parent.parent  # = backend/

from core.generators.workflow import WorkflowOrchestrator
from core.generators.task_utils import sanitize_form_name
from config.models import CODEGEN_SIGNATURE_MODEL
from core.generators.validators import validate_form_data
from core.exceptions import GenerationError, ValidationError as CoreValidationError
from core import setup_logging
from schemas.config import DynamicSchemaConfig
from schemas.registry import register_schema


# Setup core module logging
setup_logging(level="INFO", log_dir=Path(__file__).parent.parent.parent.parent / "logs")

logger = logging.getLogger(__name__)


class CodeGenerationService:
    """Service for generating DSPy code from form definitions."""

    def __init__(self, model_name: str = CODEGEN_SIGNATURE_MODEL):
        """Initialize code generation service."""
        self.model_name = model_name
        logger.info(f"Code generation service initialized with model: {model_name}")

    def generate_extraction_code(
        self,
        form_id: str,
        form_data: Dict[str, Any],
        enable_review: bool = False,
        max_attempts: int = 3,
        log_callback: Optional[callable] = None
    ) -> Dict[str, Any]:
        """
        Generate DSPy extraction code from a form definition.

        Args:
            form_id: UUID of the form
            form_data: Form specification containing:
                - form_name: str
                - form_description: str
                - fields: List[Dict] with field definitions
            enable_review: Enable human review in workflow
            max_attempts: Maximum generation attempts if validation fails
            log_callback: Optional callback function for streaming logs (message, level)

        Returns:
            Dictionary containing:
            - success: bool
            - schema_name: str (schema identifier for runtime)
            - field_mapping: dict (field-to-signature mapping)
            - statistics: dict (generation statistics)
            - error: str (if failed)
        """
        try:
            form_name = form_data.get("form_name", "CustomForm")
            logger.info(f"Starting code generation for form: {form_name}", extra={
                "form_id": form_id,
                "enable_review": enable_review
            })

            if log_callback:
                log_callback(f"Initializing code generator for '{form_name}'", "info")

            # Validate form data before processing
            try:
                if log_callback:
                    log_callback("Validating form structure...", "info")
                validated_form = validate_form_data(form_data)
                validated_dict = validated_form.model_dump()
                # Re-inject extra keys stripped by Pydantic (not part of FormDataInput schema)
                for key in ("human_feedback", "previous_decomposition"):
                    if key in form_data:
                        validated_dict[key] = form_data[key]
                form_data = validated_dict
                logger.info(f"Form data validated successfully: {len(form_data['fields'])} fields")
                if log_callback:
                    log_callback(f"✓ Form structure validated ({len(form_data['fields'])} fields)", "success")
            except Exception as e:
                logger.error(f"Form validation failed: {e}", exc_info=True)
                if log_callback:
                    log_callback(f"Form validation failed: {str(e)}", "error")
                raise CoreValidationError(f"Invalid form data: {str(e)}")

            # Initialize workflow orchestrator
            if log_callback:
                log_callback("Initializing AI workflow orchestrator...", "info")
            orchestrator = WorkflowOrchestrator(
                model_name=self.model_name,
                human_review_enabled=enable_review,
                log_callback=log_callback
            )

            # Generate task name for this form
            task_name = f"dynamic_{form_id[:8]}_{sanitize_form_name(form_name)}"
            thread_id = f"form_{form_id}"

            if log_callback:
                log_callback(f"Starting generation workflow (task: {task_name})", "info")

            # Run code generation workflow with timeout protection
            def _run_generation():
                return orchestrator.generate_complete_task(
                    form_data=form_data,
                    task_name=task_name,
                    max_attempts=max_attempts,
                    thread_id=thread_id
                )

            try:
                with ThreadPoolExecutor(max_workers=1) as executor:
                    future = executor.submit(_run_generation)
                    result = future.result(timeout=CODEGEN_TIMEOUT_SECONDS)
            except FuturesTimeoutError:
                error_msg = f"Code generation timed out after {CODEGEN_TIMEOUT_SECONDS}s"
                logger.error(error_msg, extra={"form_id": form_id})
                if log_callback:
                    log_callback(error_msg, "error")
                return {
                    "success": False,
                    "task_dir": None,
                    "schema_name": None,
                    "signatures_file": None,
                    "modules_file": None,
                    "field_mapping": {},
                    "statistics": {},
                    "error": error_msg,
                    "error_type": "timeout_error",
                    "retryable": True,
                }

            # Check if workflow is paused for human review
            if result.get("status") == "awaiting_human_review" or result.get("paused") == True:
                logger.info(f"Workflow paused for human review - form: {form_name}, thread: {thread_id}")
                if log_callback:
                    log_callback("⏸️  Workflow paused for human review", "info")

                return {
                    "success": False,  # Not completed yet
                    "status": "awaiting_review",
                    "paused": True,
                    "thread_id": thread_id,
                    "decomposition_summary": result.get("decomposition_summary", ""),
                    "decomposition": result.get("decomposition", {}),
                    "task_name": task_name,
                    "task_dir": None,
                    "schema_name": None,
                    "error": None,
                    "statistics": {}
                }

            if result.get("success"):
                logger.info(f"Code generation successful for form: {form_name}")
                if log_callback:
                    log_callback("Workflow completed, validating generated code...", "info")

                # --- Gate 2: Verify all expected signatures were generated ---
                if log_callback:
                    log_callback("Checking signature completeness...", "info")
                decomposition = result.get("decomposition", {})
                expected_sigs = {sig["name"] for sig in decomposition.get("signatures", [])}
                stats = result.get("statistics", {})
                generated_count = stats.get("signatures", 0)

                if expected_sigs and generated_count < len(expected_sigs):
                    error_msg = (
                        f"Incomplete code generation: {generated_count}/{len(expected_sigs)} "
                        f"signatures generated. The form cannot be used for extraction until "
                        f"all signatures are generated. Please try regenerating."
                    )
                    logger.error(error_msg)
                    return {
                        "success": False,
                        "task_dir": None,
                        "schema_name": None,
                        "field_mapping": {},
                        "statistics": stats,
                        "error": error_msg,
                        "error_type": "incomplete_generation",
                        "retryable": True,
                    }

                if log_callback:
                    log_callback(f"✓ All {generated_count} signatures generated successfully", "success")

                # --- Gate 3: Validate schema_def builds correctly at runtime ---
                schema_def = result.get("schema_def")
                if not schema_def:
                    error_msg = (
                        "schema_def was not built — generation produced no structured spec. "
                        "Please try regenerating."
                    )
                    logger.error(error_msg)
                    return {
                        "success": False,
                        "task_dir": None,
                        "schema_name": None,
                        "field_mapping": {},
                        "statistics": result.get("statistics", {}),
                        "error": error_msg,
                        "error_type": "schema_def_missing",
                        "retryable": True,
                    }

                if log_callback:
                    log_callback("Validating runtime class construction...", "info")
                try:
                    from dspy_components.runtime_builders import build_schema_classes
                    factories = build_schema_classes(schema_def, task_name)
                    if not factories:
                        raise ValueError("build_schema_classes returned no extractor classes")
                except Exception as rb_err:
                    error_msg = f"Runtime builder validation failed: {rb_err}"
                    logger.error(error_msg)
                    return {
                        "success": False,
                        "task_dir": None,
                        "schema_name": None,
                        "field_mapping": {},
                        "statistics": result.get("statistics", {}),
                        "error": error_msg,
                        "error_type": "runtime_builder_error",
                        "retryable": True,
                    }

                if log_callback:
                    log_callback("✓ Runtime class construction validated", "success")

                signature_names = [s["class_name"] for s in schema_def["signatures"]]
                pipeline_stages = schema_def.get("pipeline_stages", [])

                schema_config = DynamicSchemaConfig(
                    schema_name=task_name,
                    task_name=task_name,
                    module_path=f"dspy_components.tasks.{task_name}",
                    signatures_path=f"dspy_components.tasks.{task_name}.signatures",
                    signature_class_names=signature_names,
                    pipeline_stages=pipeline_stages,
                    project_id="",
                    form_id=form_id,
                    form_name=form_name,
                    schema_def=schema_def,
                )

                try:
                    register_schema(schema_config)
                    logger.info(f"Schema registered: {task_name}")
                    if log_callback:
                        log_callback(f"✓ Schema registered: {task_name}", "success")
                        log_callback("Code generation completed successfully!", "success")
                except Exception as reg_err:
                    error_msg = f"Schema registration failed: {reg_err}"
                    logger.error(error_msg)
                    return {
                        "success": False,
                        "task_dir": None,
                        "schema_name": None,
                        "field_mapping": {},
                        "statistics": result.get("statistics", {}),
                        "error": error_msg,
                        "error_type": "registration_error",
                        "retryable": True,
                    }

                return {
                    "success": True,
                    "task_dir": None,
                    "schema_name": task_name,
                    "task_name": task_name,
                    "thread_id": thread_id,
                    "decomposition": result.get("decomposition", {}),
                    "decomposition_summary": result.get("decomposition_summary", ""),
                    "field_mapping": result.get("field_mapping", {}),
                    "schema_def": schema_def,
                    "statistics": result.get("statistics", {}),
                    "error": None
                }
            else:
                # Generation failed
                error_msg = result.get("error", "Unknown error during code generation")
                logger.error(f"Code generation failed for form {form_name}: {error_msg}")

                return {
                    "success": False,
                    "task_dir": None,
                    "schema_name": None,
                    "signatures_file": None,
                    "modules_file": None,
                    "field_mapping": {},
                    "statistics": {},
                    "error": error_msg
                }

        except CoreValidationError as e:
            # Input validation failed - not retryable
            logger.error(f"Validation error: {e}", exc_info=True)
            return {
                "success": False,
                "task_dir": None,
                "schema_name": None,
                "field_mapping": {},
                "statistics": {},
                "error": f"Invalid form data: {str(e)}",
                "error_type": "validation_error",
                "retryable": False
            }
        except GenerationError as e:
            # Code generation error - potentially retryable
            logger.error(f"Generation error: {e}", exc_info=True)
            return {
                "success": False,
                "task_dir": None,
                "schema_name": None,
                "field_mapping": {},
                "statistics": {},
                "error": str(e),
                "error_type": "generation_error",
                "retryable": True
            }
        except Exception as e:
            # Unexpected error
            logger.error(f"Unexpected error in code generation service: {str(e)}", exc_info=True)
            return {
                "success": False,
                "task_dir": None,
                "schema_name": None,
                "field_mapping": {},
                "statistics": {},
                "error": f"Unexpected error: {str(e)}",
                "error_type": "unexpected_error",
                "retryable": False
            }

    def check_generator_status(self) -> Dict[str, Any]:
        """Check if code generator is available and healthy."""
        try:
            # Try initializing orchestrator to verify dependencies
            orchestrator = WorkflowOrchestrator(model_name=self.model_name)

            return {
                "available": True,
                "model": self.model_name,
                "error": None
            }
        except Exception as e:
            return {
                "available": False,
                "model": None,
                "error": str(e)
            }


# Global service instance
code_generation_service = CodeGenerationService()
