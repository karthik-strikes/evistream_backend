# core/generators/form_review_bridge.py
"""
Form Review Bridge Module

Connects the form creation UI with the decomposition workflow orchestrator
and human review handler. Manages the complete lifecycle of form review:
- Triggering decomposition
- Presenting review UI to users
- Handling approval/rejection
- Managing form states
"""

import json
import uuid
from typing import Dict, Any, Optional, Tuple
from pathlib import Path
import logging

from .workflow import WorkflowOrchestrator
from .task_utils import create_task_name_from_ids, register_dynamic_schema
from utils import project_repository as proj_repo
from pathlib import Path
from config.models import CODEGEN_REVIEW_MODEL

logger = logging.getLogger(__name__)


class FormDecompositionService:
    """
    Backend service for managing form decomposition workflow.

    Handles:
    - Starting decomposition workflow
    - Checking decomposition status
    - Approving/rejecting decompositions
    - Saving generated code
    """

    def __init__(self, model_name: str = CODEGEN_REVIEW_MODEL):
        """Initialize with workflow orchestrator"""
        self.orchestrator = WorkflowOrchestrator(
            model_name=model_name,
            human_review_enabled=False  # Disabled for now
        )

    def start_decomposition(
        self,
        project_id: str,
        form_id: str,
        form_data: Dict[str, Any],
        enable_review: bool = True
    ) -> Dict[str, Any]:
        """
        Start form decomposition workflow.

        Args:
            project_id: Project ID
            form_id: Form ID
            form_data: Form specification
            enable_review: Whether to pause for human review (default: True)

        Returns:
            dict with status, thread_id, and decomposition data
        """
        logger.info(
            f"Starting decomposition for form {form_id} (review_enabled={enable_review})")

        try:
            # Generate unique thread ID for this workflow
            thread_id = f"form_{form_id}_{uuid.uuid4().hex[:8]}"

            # Derive task name from project_id and form_id using hash
            task_name = create_task_name_from_ids(project_id, form_id)

            # Create orchestrator with appropriate review setting
            orchestrator = WorkflowOrchestrator(
                model_name=self.orchestrator.model_name,
                human_review_enabled=enable_review
            )

            # Start workflow
            result = orchestrator.generate_complete_task(
                form_data=form_data,
                task_name=task_name,
                max_attempts=3,
                thread_id=thread_id
            )

            # Check if paused for review
            if result.get("status") == "awaiting_human_review":
                logger.info(f"Workflow paused for review: {thread_id}")

                # Update form in database
                self._update_form_status(
                    project_id,
                    form_id,
                    "AWAITING_REVIEW",
                    review_thread_id=thread_id,
                    decomposition=result.get("decomposition"),
                    validation_results=result.get("validation_results")
                )

                return {
                    "success": True,
                    "status": "awaiting_review",
                    "thread_id": thread_id,
                    "message": "Decomposition ready for review"
                }

            elif result.get("success"):
                # Completed without review (happens when enable_review=False)
                logger.info(
                    "Workflow completed without review (as configured)")

                # Save generated code and register schema
                self._save_generated_code(
                    project_id, form_id, result, form_data)

                # Update form status to active
                self._update_form_status(project_id, form_id, "ACTIVE")

                return {
                    "success": True,
                    "status": "completed",
                    "result": result
                }

            else:
                # Generation failed
                logger.error(f"Decomposition failed: {result.get('error')}")

                self._update_form_status(
                    project_id,
                    form_id,
                    "FAILED",
                    error=result.get("error")
                )

                return {
                    "success": False,
                    "status": "failed",
                    "error": result.get("error"),
                    "errors": result.get("errors", [])
                }

        except Exception as e:
            logger.exception(f"Exception starting decomposition: {e}")

            self._update_form_status(
                project_id, form_id, "FAILED", error=str(e))

            return {
                "success": False,
                "status": "failed",
                "error": str(e)
            }

    def get_review_data(
        self,
        project_id: str,
        form_id: str
    ) -> Optional[Dict[str, Any]]:
        """
        Get decomposition and validation data for review.

        Args:
            project_id: Project ID
            form_id: Form ID

        Returns:
            dict with decomposition, validation, and thread_id
        """
        try:
            form = proj_repo.get_form(project_id, form_id)

            if not form:
                return None

            return {
                "form": form,
                "decomposition": form.get("decomposition"),
                "validation_results": form.get("validation_results"),
                "thread_id": form.get("review_thread_id"),
                "status": form.get("status", "UNKNOWN")
            }

        except Exception as e:
            logger.exception(f"Error getting review data: {e}")
            return None

    def approve_decomposition(
        self,
        project_id: str,
        form_id: str,
        thread_id: str
    ) -> Dict[str, Any]:
        """
        Approve decomposition and continue workflow.

        Args:
            project_id: Project ID
            form_id: Form ID
            thread_id: LangGraph thread ID

        Returns:
            dict with success status and result
        """
        logger.info(f"Approving decomposition for form {form_id}")

        try:
            # Update form status to generating
            self._update_form_status(project_id, form_id, "GENERATING")

            # Resume workflow with approval
            result = self.orchestrator.approve_decomposition(thread_id)

            if result.get("success"):
                logger.info(f"Code generation completed for form {form_id}")

                # Get form_data from database for registration
                form = proj_repo.get_form(project_id, form_id)
                form_data = None
                if form:
                    form_data = {
                        "form_name": form.get("form_name") or form.get("name"),
                        "description": form.get("form_description") or form.get("description"),
                        "fields": form.get("fields", [])
                    }

                # Save generated code and register schema
                self._save_generated_code(
                    project_id, form_id, result, form_data)

                # Update form status to active
                self._update_form_status(project_id, form_id, "ACTIVE")

                return {
                    "success": True,
                    "message": "Form approved and code generated successfully",
                    "result": result
                }

            else:
                logger.error(f"Code generation failed: {result.get('error')}")

                self._update_form_status(
                    project_id,
                    form_id,
                    "FAILED",
                    error=result.get("error")
                )

                return {
                    "success": False,
                    "error": result.get("error")
                }

        except Exception as e:
            logger.exception(f"Exception approving decomposition: {e}")

            self._update_form_status(
                project_id, form_id, "FAILED", error=str(e))

            return {
                "success": False,
                "error": str(e)
            }

    def reject_decomposition(
        self,
        project_id: str,
        form_id: str,
        thread_id: str,
        feedback: str
    ) -> Dict[str, Any]:
        """
        Reject decomposition with feedback for regeneration.

        Args:
            project_id: Project ID
            form_id: Form ID
            thread_id: LangGraph thread ID
            feedback: Human feedback

        Returns:
            dict with success status
        """
        logger.info(f"Rejecting decomposition for form {form_id}")
        logger.info(f"Feedback: {feedback}")

        try:
            # Update form status to regenerating
            self._update_form_status(project_id, form_id, "REGENERATING")

            # Resume workflow with rejection and feedback
            result = self.orchestrator.reject_decomposition(
                thread_id, feedback)

            # Check if paused again for another review
            if result.get("status") == "awaiting_human_review":
                logger.info(
                    f"Workflow regenerated, awaiting review again: {thread_id}")

                # Update with new decomposition from regeneration
                self._update_form_status(
                    project_id,
                    form_id,
                    "AWAITING_REVIEW",
                    review_thread_id=thread_id,
                    decomposition=result.get("decomposition"),
                    validation_results=result.get("validation_results")
                )

                return {
                    "success": True,
                    "status": "awaiting_review",
                    "message": "Decomposition regenerated, ready for review"
                }

            elif result.get("success"):
                # Completed without another review
                # Get form_data from database for registration
                form = proj_repo.get_form(project_id, form_id)
                form_data = None
                if form:
                    form_data = {
                        "form_name": form.get("form_name") or form.get("name"),
                        "description": form.get("form_description") or form.get("description"),
                        "fields": form.get("fields", [])
                    }

                self._save_generated_code(
                    project_id, form_id, result, form_data)
                self._update_form_status(project_id, form_id, "ACTIVE")

                return {
                    "success": True,
                    "status": "completed",
                    "result": result
                }

            else:
                self._update_form_status(
                    project_id,
                    form_id,
                    "FAILED",
                    error=result.get("error")
                )

                return {
                    "success": False,
                    "error": result.get("error")
                }

        except Exception as e:
            logger.exception(f"Exception rejecting decomposition: {e}")

            self._update_form_status(
                project_id, form_id, "FAILED", error=str(e))

            return {
                "success": False,
                "error": str(e)
            }

    def _update_form_status(
        self,
        project_id: str,
        form_id: str,
        status: str,
        **kwargs
    ):
        """Update form status in database"""
        try:
            update_data = {"status": status}

            # Convert decomposition Pydantic objects to plain dicts before storing
            if "decomposition" in kwargs and kwargs["decomposition"]:
                decomp = kwargs["decomposition"]

                # Convert Pydantic Signature objects to dicts
                decomposition_dict = {
                    "reasoning_trace": decomp.get("reasoning_trace", ""),
                    "signatures": [],
                    "pipeline": decomp.get("pipeline", []),
                    "field_coverage": decomp.get("field_coverage", {})
                }

                # Convert each signature (handles both Pydantic objects and dicts)
                for sig in decomp.get("signatures", []):
                    if hasattr(sig, 'dict'):
                        # Pydantic object - convert to dict
                        decomposition_dict["signatures"].append(sig.dict())
                    elif isinstance(sig, dict):
                        # Already a dict - use as is
                        decomposition_dict["signatures"].append(sig)
                    else:
                        # Unknown type - try to convert
                        logger.warning(f"Unknown signature type: {type(sig)}")
                        decomposition_dict["signatures"].append(str(sig))

                kwargs["decomposition"] = decomposition_dict

            update_data.update(kwargs)
            proj_repo.update_form(project_id, form_id, update_data)

        except Exception as e:
            logger.exception(f"Error updating form status: {e}")

    def _save_generated_code(
        self,
        project_id: str,
        form_id: str,
        result: Dict[str, Any],
        form_data: Optional[Dict[str, Any]] = None
    ):
        """
        Save generated signatures and modules code to disk and register schema.

        Args:
            project_id: Project identifier
            form_id: Form identifier
            result: Generation result with signatures_file, modules_file, task_name
            form_data: Form specification (needed for registration)
        """
        try:
            # Extract code files
            signatures_code = result.get("signatures_file", "")
            modules_code = result.get("modules_file", "")
            task_name = result.get("task_name", "")

            if not task_name:
                raise ValueError(
                    "task_name is required but missing from result")

            # Write files to disk
            task_dir = Path(__file__).parent.parent.parent / \
                "dspy_components" / "tasks" / task_name

            task_dir.mkdir(parents=True, exist_ok=True)

            signature_file = task_dir / "signatures.py"
            signature_file.write_text(signatures_code)

            module_file = task_dir / "modules.py"
            module_file.write_text(modules_code)

            init_file = task_dir / "__init__.py"
            if not init_file.exists():
                init_file.write_text("")

            # Write metadata.json with decomposition and other info
            metadata = {
                "project_id": project_id,
                "form_id": form_id,
                "form_data": form_data,
                "field_mapping": result.get("field_mapping", {}),
                "decomposition": result.get("decomposition", {}),
                "generation_stats": result.get("statistics", {}),
            }
            metadata_file = task_dir / "metadata.json"
            metadata_file.write_text(json.dumps(metadata, indent=2))
            logger.info(f"Wrote metadata.json to {metadata_file}")

            logger.info(f"Wrote files to {task_dir}")

            # Register schema if form_data is provided
            schema_name = task_name  # Default to task_name
            if form_data:
                try:
                    # Get decomposition from result
                    decomposition = result.get("decomposition", {})
                    schema_name = register_dynamic_schema(
                        project_id, form_id, form_data, decomposition=decomposition)

                except Exception as reg_error:
                    logger.error(
                        f"Failed to register schema: {reg_error}")
                    logger.error(
                        f"form_data keys: {list(form_data.keys()) if form_data else 'None'}")
                    import traceback
                    logger.error(traceback.format_exc())
                    # Still use task_name as fallback, but log the error
                    schema_name = task_name
            else:
                logger.warning(
                    f"form_data is None - cannot register schema. Using task_name: {task_name}")

            # Save to form record
            proj_repo.update_form(project_id, form_id, {
                "schema_name": schema_name,
                "task_dir": f"dspy_components/tasks/{task_name}",
                "signatures_code": signatures_code,
                "modules_code": modules_code,
                "field_mapping": result.get("field_mapping", {}),
                "statistics": result.get("statistics", {})
            })

            logger.info(
                f"Saved generated code for form {form_id} (schema: {schema_name})")

        except Exception as e:
            logger.exception(f"Error saving generated code: {e}")
            raise


# Singleton instance
_service = None


def get_decomposition_service() -> FormDecompositionService:
    """Get singleton decomposition service instance"""
    global _service
    if _service is None:
        _service = FormDecompositionService()
    return _service


__all__ = ["FormDecompositionService", "get_decomposition_service"]
