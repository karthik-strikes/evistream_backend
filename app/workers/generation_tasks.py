"""
Celery tasks for form code generation.
"""

import logging
import json
from supabase import create_client

from app.workers.celery_app import celery_app
from app.workers.log_broadcaster import CeleryLogBroadcaster
from app.config import settings
from app.services.code_generation_service import code_generation_service
from app.models.enums import FormStatus, JobStatus
from app.workers.utils import sync_log_activity, sync_notify

logger = logging.getLogger(__name__)

# Initialize Supabase client
supabase = create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_KEY)


def _form_still_exists(form_id: str) -> bool:
    """Return True if the form row still exists in the database."""
    try:
        result = supabase.table("forms").select("id").eq("id", form_id).execute()
        return bool(result.data)
    except Exception:
        return True  # assume it exists on DB error to avoid false aborts


@celery_app.task(
    bind=True,
    name="generate_form_code",
    max_retries=1,
    default_retry_delay=60,
    autoretry_for=(ConnectionError, TimeoutError),
    retry_backoff=True,
    retry_jitter=True,
)
def generate_form_code(self, form_id: str, job_id: str, enable_review: bool = False):
    """
    Background task to generate DSPy code for a form.

    Args:
        self: Celery task instance (for updating state)
        form_id: UUID of the form
        job_id: UUID of the job record
        enable_review: Enable human review in workflow

    Returns:
        Dictionary with generation results
    """
    try:
        # Initialize log broadcaster for real-time streaming
        broadcaster = CeleryLogBroadcaster(job_id)

        logger.info(f"Starting code generation for form {form_id}")
        broadcaster.info("🚀 Starting code generation workflow...")

        # Update job status to processing
        supabase.table("jobs").update({
            "status": JobStatus.PROCESSING.value,
            "progress": 10
        }).eq("id", job_id).execute()
        broadcaster.progress(10, "Initializing...")

        # Get form from database
        broadcaster.info("📋 Loading form definition from database...")
        form_result = supabase.table("forms")\
            .select("*")\
            .eq("id", form_id)\
            .execute()

        if not form_result.data:
            raise Exception(f"Form {form_id} not found")

        form = form_result.data[0]

        # Parse fields from JSON string
        fields = json.loads(form["fields"]) if isinstance(form.get("fields"), str) else form["fields"]

        # Prepare form data for code generation
        form_data = {
            "form_name": form["form_name"],
            "form_description": form.get("form_description", ""),
            "fields": fields
        }

        logger.info(f"Form data prepared: {form['form_name']}")
        broadcaster.success(f"✓ Form loaded: '{form['form_name']}' with {len(fields)} fields")

        # Update form status
        supabase.table("forms").update({
            "status": FormStatus.GENERATING.value
        }).eq("id", form_id).execute()

        # Update job progress
        supabase.table("jobs").update({
            "progress": 30
        }).eq("id", job_id).execute()
        broadcaster.progress(30, "Form validated, starting AI generation...")

        # Abort early if form was deleted while we were setting up
        if not _form_still_exists(form_id):
            logger.info(f"Form {form_id} was deleted — aborting generation")
            supabase.table("jobs").update({
                "status": JobStatus.CANCELLED.value,
                "error_message": "Form deleted by user"
            }).eq("id", job_id).execute()
            return {"status": "cancelled", "form_id": form_id}

        # Generate code using service with log callback
        broadcaster.stage("code_generation", "🤖 AI is analyzing your form...")

        def log_callback(message: str, level: str = "info"):
            """Callback to stream logs from code generation service"""
            if level == "error":
                broadcaster.error(message)
            elif level == "warning":
                broadcaster.warning(message)
            elif level == "success":
                broadcaster.success(message)
            else:
                broadcaster.info(message)

        result = code_generation_service.generate_extraction_code(
            form_id=form_id,
            form_data=form_data,
            enable_review=enable_review,
            max_attempts=3,
            log_callback=log_callback
        )

        # Check if workflow is paused for human review
        if result.get("status") == "awaiting_human_review" or result.get("paused") == True:
            logger.info(f"Workflow paused for human review - form {form_id}")
            broadcaster.info("⏸️  Workflow paused for human review")
            broadcaster.info("Please review the decomposition in the frontend")

            # Store workflow thread_id and decomposition in form metadata
            decomposition = result.get("decomposition", {})
            logger.info(f"Decomposition keys: {decomposition.keys() if decomposition else 'None'}")
            logger.info(f"Signatures count: {len(decomposition.get('signatures', [])) if decomposition else 0}")
            logger.info(f"Pipeline count: {len(decomposition.get('pipeline', [])) if decomposition else 0}")

            metadata = {
                "thread_id": result.get("thread_id"),
                "task_name": result.get("task_name"),
                "decomposition": decomposition,
                "decomposition_summary": result.get("decomposition_summary", "")
            }

            logger.info(f"Metadata to store: thread_id={metadata['thread_id']}, has_decomposition={bool(metadata['decomposition'])}")

            # Update form status to awaiting_review
            supabase.table("forms").update({
                "status": FormStatus.AWAITING_REVIEW.value,
                "metadata": json.dumps(metadata),
                "error": None
            }).eq("id", form_id).execute()

            # Update job status to completed (paused state)
            supabase.table("jobs").update({
                "status": JobStatus.COMPLETED.value,
                "progress": 50,
                "result_data": {
                    "status": "awaiting_review",
                    "message": "Decomposition ready for review"
                }
            }).eq("id", job_id).execute()
            broadcaster.progress(50, "Awaiting human review")

            return {
                "status": "awaiting_review",
                "form_id": form_id,
                "message": "Workflow paused for human review"
            }

        if result["success"]:
            logger.info(f"Code generation successful for form {form_id}")
            broadcaster.success(f"✅ Code generation completed successfully!")
            broadcaster.data(result.get("statistics", {}), "Generation statistics")

            # Update form with generated code information
            metadata = {
                "thread_id": result.get("thread_id"),
                "task_name": result.get("task_name"),
                "decomposition": result.get("decomposition", {}),
                "decomposition_summary": result.get("decomposition_summary", "")
            }
            update_data = {
                "status": FormStatus.ACTIVE.value,
                "schema_name": result["schema_name"],
                "task_dir": result["task_dir"],
                "statistics": json.dumps(result.get("statistics", {})),
                "metadata": json.dumps(metadata),
                "error": None,
            }
            supabase.table("forms").update(update_data).eq("id", form_id).execute()
            broadcaster.info(f"💾 Saved to: {result['schema_name']}")

            # Update job status to completed
            supabase.table("jobs").update({
                "status": JobStatus.COMPLETED.value,
                "progress": 100,
                "result_data": {
                    "schema_name": result["schema_name"],
                    "task_dir": result["task_dir"],
                    "field_mapping": result.get("field_mapping", {}),
                    "statistics": result.get("statistics", {})
                }
            }).eq("id", job_id).execute()
            broadcaster.progress(100, "Complete! Form ready for extraction.")

            # Notify and log activity on success
            job_record = supabase.table("jobs").select("user_id, project_id").eq("id", job_id).execute()
            if job_record.data:
                _user_id = job_record.data[0]["user_id"]
                _project_id = job_record.data[0].get("project_id")
                sync_notify(user_id=_user_id, job_id=job_id, job_type="form_generation", success=True)
                sync_log_activity(
                    user_id=_user_id,
                    action_type="code_generation",
                    action="Code Generation Completed",
                    description=f"Code generation completed for form: {form['form_name']}",
                    project_id=_project_id,
                    metadata={"form_id": form_id, "schema_name": result["schema_name"]},
                )

            return {
                "status": "success",
                "form_id": form_id,
                "schema_name": result["schema_name"],
                "task_dir": result["task_dir"],
                "statistics": result.get("statistics", {})
            }
        else:
            # Generation failed
            error_msg = result.get("error", "Unknown error")
            logger.error(f"Code generation failed for form {form_id}: {error_msg}")
            broadcaster.error(f"❌ Generation failed: {error_msg}")

            # Update form status to failed
            supabase.table("forms").update({
                "status": FormStatus.FAILED.value,
                "error": error_msg
            }).eq("id", form_id).execute()

            # Update job status to failed
            supabase.table("jobs").update({
                "status": JobStatus.FAILED.value,
                "progress": 0,
                "error_message": error_msg
            }).eq("id", job_id).execute()

            # Notify and log activity on failure
            job_record = supabase.table("jobs").select("user_id, project_id").eq("id", job_id).execute()
            if job_record.data:
                _user_id = job_record.data[0]["user_id"]
                _project_id = job_record.data[0].get("project_id")
                sync_notify(user_id=_user_id, job_id=job_id, job_type="form_generation", success=False, error_message=error_msg)
                sync_log_activity(
                    user_id=_user_id,
                    action_type="code_generation",
                    action="Code Generation Failed",
                    description=f"Code generation failed: {error_msg}",
                    project_id=_project_id,
                    metadata={"form_id": form_id, "error": error_msg},
                    status="failed",
                )

            return {
                "status": "failed",
                "form_id": form_id,
                "error": error_msg
            }

    except Exception as e:
        error_msg = str(e)
        logger.error(f"Error in code generation task: {error_msg}")

        # Broadcast error
        try:
            broadcaster = CeleryLogBroadcaster(job_id)
            broadcaster.error(f"❌ Unexpected error: {error_msg}")
        except Exception:
            logger.debug("Broadcaster failed in generate_form_code error handler", exc_info=True)

        # Update form status to failed
        try:
            supabase.table("forms").update({
                "status": FormStatus.FAILED.value,
                "error": error_msg
            }).eq("id", form_id).execute()

            # Update job status to failed
            supabase.table("jobs").update({
                "status": JobStatus.FAILED.value,
                "progress": 0,
                "error_message": error_msg
            }).eq("id", job_id).execute()

            # Notify on exception
            job_record = supabase.table("jobs").select("user_id, project_id").eq("id", job_id).execute()
            if job_record.data:
                _user_id = job_record.data[0]["user_id"]
                _project_id = job_record.data[0].get("project_id")
                sync_notify(user_id=_user_id, job_id=job_id, job_type="form_generation", success=False, error_message=error_msg)
                sync_log_activity(
                    user_id=_user_id,
                    action_type="code_generation",
                    action="Code Generation Failed",
                    description=f"Code generation failed: {error_msg}",
                    project_id=_project_id,
                    metadata={"form_id": form_id, "error": error_msg},
                    status="failed",
                )
        except Exception as db_error:
            logger.error(f"Failed to update database after error: {db_error}")

        # Re-raise the exception for Celery to handle
        raise


@celery_app.task(
    bind=True,
    name="resume_after_approval",
    max_retries=1,
    default_retry_delay=60,
    autoretry_for=(ConnectionError, TimeoutError),
    retry_backoff=True,
    retry_jitter=True,
)
def resume_after_approval(self, form_id: str, job_id: str, thread_id: str, task_name: str):
    """
    Resume workflow after user approves decomposition.

    Args:
        self: Celery task instance
        form_id: UUID of the form
        job_id: UUID of the job record
        thread_id: Workflow thread ID for resumption
        task_name: Task directory name
    """
    try:
        broadcaster = CeleryLogBroadcaster(job_id)
        logger.info(f"Resuming workflow after approval - form: {form_id}, thread: {thread_id}")

        if not _form_still_exists(form_id):
            logger.info(f"Form {form_id} was deleted — aborting approval resume")
            supabase.table("jobs").update({
                "status": JobStatus.CANCELLED.value,
                "error_message": "Form deleted by user"
            }).eq("id", job_id).execute()
            return {"status": "cancelled", "form_id": form_id}

        broadcaster.info("✅ Decomposition approved, resuming generation...")

        # Update form status
        supabase.table("forms").update({
            "status": FormStatus.GENERATING.value
        }).eq("id", form_id).execute()

        # Get form data and the previously approved decomposition from metadata
        form_result = supabase.table("forms").select("*").eq("id", form_id).execute()
        if not form_result.data:
            raise Exception(f"Form {form_id} not found")

        form = form_result.data[0]
        fields = json.loads(form["fields"]) if isinstance(form.get("fields"), str) else form["fields"]

        metadata_str = form.get("metadata")
        if not metadata_str:
            raise Exception("No workflow metadata found — cannot resume without decomposition")
        metadata = json.loads(metadata_str) if isinstance(metadata_str, str) else metadata_str
        decomposition = metadata.get("decomposition")
        if not decomposition:
            raise Exception("No decomposition found in form metadata")

        form_data = {
            "form_name": form["form_name"],
            "form_description": form.get("form_description", ""),
            "fields": fields,
        }

        from core.generators.workflow import WorkflowOrchestrator
        from pathlib import Path
        import ast

        project_root = Path(__file__).parent.parent.parent  # = backend/

        def log_callback(message: str, level: str = "info"):
            if level == "error":
                broadcaster.error(message)
            elif level == "warning":
                broadcaster.warning(message)
            elif level == "success":
                broadcaster.success(message)
            else:
                broadcaster.info(message)

        orchestrator = WorkflowOrchestrator(
            human_review_enabled=False,
            log_callback=log_callback
        )

        broadcaster.info("Generating signatures and modules from approved decomposition...")
        result = orchestrator.generate_from_approved_decomposition(
            form_data=form_data,
            decomposition=decomposition,
            task_name=task_name,
            thread_id=thread_id,
        )

        # Check if success or paused again
        if result.get("status") == "awaiting_human_review":
            # Paused again (unlikely after approval, but possible if re-decomposed)
            metadata = {
                "thread_id": thread_id,
                "task_name": task_name,
                "decomposition": result.get("decomposition", {}),
                "decomposition_summary": result.get("decomposition_summary", "")
            }
            supabase.table("forms").update({
                "status": FormStatus.AWAITING_REVIEW.value,
                "metadata": json.dumps(metadata)
            }).eq("id", form_id).execute()

            supabase.table("jobs").update({
                "status": JobStatus.COMPLETED.value,
                "progress": 50
            }).eq("id", job_id).execute()

            broadcaster.info("⏸️  Workflow paused again for review")
            return {"status": "awaiting_review", "form_id": form_id}

        if result.get("success"):
            logger.info(f"Code generation completed after approval - form: {form_id}")
            broadcaster.success("✅ Code generation completed!")

            # Validate syntax
            for label, code_key in [("signatures.py", "signatures_file"), ("modules.py", "modules_file")]:
                code = result.get(code_key, "")
                try:
                    ast.parse(code)
                except SyntaxError as syn_err:
                    error_msg = f"Generated {label} has invalid syntax: {syn_err}"
                    logger.error(error_msg)
                    broadcaster.error(error_msg)
                    supabase.table("forms").update({
                        "status": FormStatus.FAILED.value,
                        "error": error_msg
                    }).eq("id", form_id).execute()
                    supabase.table("jobs").update({
                        "status": JobStatus.FAILED.value,
                        "error_message": error_msg
                    }).eq("id", job_id).execute()
                    return {"status": "failed", "form_id": form_id, "error": error_msg}

            broadcaster.success("✓ Syntax validation passed")

            # Write files
            broadcaster.info("Writing generated code to disk...")
            task_dir = project_root / "dspy_components" / "tasks" / task_name
            task_dir.mkdir(parents=True, exist_ok=True)

            (task_dir / "signatures.py").write_text(result["signatures_file"], encoding="utf-8")
            (task_dir / "modules.py").write_text(result["modules_file"], encoding="utf-8")
            (task_dir / "__init__.py").write_text(f'"""\nGenerated task: {task_name}\n"""\n', encoding="utf-8")

            # Save metadata files for auto-discovery
            decomposition = result.get("decomposition", {})
            signature_names = [sig["name"] for sig in decomposition.get("signatures", [])]
            pipeline_stages = decomposition.get("pipeline", [])

            field_mapping = {}
            for sig in decomposition.get("signatures", []):
                sig_name = sig["name"]
                for field_name in sig.get("fields", {}).keys():
                    field_mapping[field_name] = sig_name

            (task_dir / "field_mapping.json").write_text(json.dumps(field_mapping, indent=2), encoding="utf-8")
            (task_dir / "pipeline_stages.json").write_text(json.dumps(pipeline_stages, indent=2), encoding="utf-8")

            broadcaster.success(f"✓ Code saved to: {task_dir}")

            # Register schema
            from schemas.config import DynamicSchemaConfig
            from schemas.registry import register_schema

            schema_config = DynamicSchemaConfig(
                schema_name=task_name,
                task_name=task_name,
                module_path=f"dspy_components.tasks.{task_name}",
                signatures_path=f"dspy_components.tasks.{task_name}.signatures",
                signature_class_names=signature_names,
                pipeline_stages=pipeline_stages,
                project_id="",
                form_id=form_id,
                form_name=form["form_name"]
            )
            register_schema(schema_config)
            broadcaster.success(f"✓ Schema registered: {task_name}")

            # Update form to active
            metadata = {
                "thread_id": thread_id,
                "task_name": task_name,
                "decomposition": decomposition,
                "decomposition_summary": result.get("decomposition_summary", "")
            }
            supabase.table("forms").update({
                "status": FormStatus.ACTIVE.value,
                "schema_name": task_name,
                "task_dir": str(task_dir),
                "statistics": json.dumps(result.get("statistics", {})),
                "metadata": json.dumps(metadata),
                "error": None,
            }).eq("id", form_id).execute()

            # Update job to completed
            supabase.table("jobs").update({
                "status": JobStatus.COMPLETED.value,
                "progress": 100,
                "result_data": {
                    "schema_name": task_name,
                    "task_dir": str(task_dir),
                    "statistics": result.get("statistics", {})
                }
            }).eq("id", job_id).execute()

            broadcaster.progress(100, "Complete! Form ready for extraction.")

            # Log activity on success
            job_record = supabase.table("jobs").select("user_id, project_id").eq("id", job_id).execute()
            if job_record.data:
                _user_id = job_record.data[0]["user_id"]
                _project_id = job_record.data[0].get("project_id")
                sync_notify(user_id=_user_id, job_id=job_id, job_type="form_generation", success=True)
                sync_log_activity(
                    user_id=_user_id,
                    action_type="code_generation",
                    action="Code Generation Completed (Post-Approval)",
                    description=f"Code generation completed after approval for form: {form['form_name']}",
                    project_id=_project_id,
                    metadata={"form_id": form_id, "schema_name": task_name},
                )

            return {"status": "success", "form_id": form_id, "schema_name": task_name}

        else:
            # Failed
            error_msg = result.get("error", "Unknown error")
            logger.error(f"Code generation failed after approval: {error_msg}")
            broadcaster.error(f"❌ Generation failed: {error_msg}")

            supabase.table("forms").update({
                "status": FormStatus.FAILED.value,
                "error": error_msg
            }).eq("id", form_id).execute()

            supabase.table("jobs").update({
                "status": JobStatus.FAILED.value,
                "error_message": error_msg
            }).eq("id", job_id).execute()

            # Log activity on failure
            job_record = supabase.table("jobs").select("user_id, project_id").eq("id", job_id).execute()
            if job_record.data:
                _user_id = job_record.data[0]["user_id"]
                _project_id = job_record.data[0].get("project_id")
                sync_notify(user_id=_user_id, job_id=job_id, job_type="form_generation", success=False, error_message=error_msg)
                sync_log_activity(
                    user_id=_user_id,
                    action_type="code_generation",
                    action="Code Generation Failed (Post-Approval)",
                    description=f"Code generation failed after approval: {error_msg}",
                    project_id=_project_id,
                    metadata={"form_id": form_id, "error": error_msg},
                    status="failed",
                )

            return {"status": "failed", "form_id": form_id, "error": error_msg}

    except Exception as e:
        error_msg = str(e)
        logger.error(f"Error in resume_after_approval: {error_msg}", exc_info=True)

        try:
            broadcaster = CeleryLogBroadcaster(job_id)
            broadcaster.error(f"❌ Error: {error_msg}")
        except Exception:
            logger.debug("Broadcaster failed in resume_after_rejection error handler", exc_info=True)

        try:
            supabase.table("forms").update({
                "status": FormStatus.FAILED.value,
                "error": error_msg
            }).eq("id", form_id).execute()

            supabase.table("jobs").update({
                "status": JobStatus.FAILED.value,
                "error_message": error_msg
            }).eq("id", job_id).execute()
        except Exception as db_error:
            logger.error(f"Failed to update database: {db_error}")

        raise


@celery_app.task(
    bind=True,
    name="resume_after_rejection",
    max_retries=1,
    default_retry_delay=60,
    autoretry_for=(ConnectionError, TimeoutError),
    retry_backoff=True,
    retry_jitter=True,
)
def resume_after_rejection(self, form_id: str, job_id: str, thread_id: str, task_name: str, feedback: str):
    """
    Resume workflow after user rejects decomposition with feedback.

    Args:
        self: Celery task instance
        form_id: UUID of the form
        job_id: UUID of the job record
        thread_id: Workflow thread ID for resumption
        task_name: Task directory name
        feedback: User feedback for regeneration
    """
    try:
        broadcaster = CeleryLogBroadcaster(job_id)
        logger.info(f"Resuming workflow after rejection - form: {form_id}, thread: {thread_id}")

        if not _form_still_exists(form_id):
            logger.info(f"Form {form_id} was deleted — aborting rejection resume")
            supabase.table("jobs").update({
                "status": JobStatus.CANCELLED.value,
                "error_message": "Form deleted by user"
            }).eq("id", job_id).execute()
            return {"status": "cancelled", "form_id": form_id}

        broadcaster.info("🔄 Processing feedback and regenerating...")

        # Update form status
        supabase.table("forms").update({
            "status": FormStatus.REGENERATING.value
        }).eq("id", form_id).execute()

        # Initialize orchestrator and resume with feedback
        from core.generators.workflow import WorkflowOrchestrator

        def log_callback(message: str, level: str = "info"):
            if level == "error":
                broadcaster.error(message)
            elif level == "warning":
                broadcaster.warning(message)
            elif level == "success":
                broadcaster.success(message)
            else:
                broadcaster.info(message)

        orchestrator = WorkflowOrchestrator(
            human_review_enabled=True,
            log_callback=log_callback
        )

        broadcaster.info(f"Regenerating with feedback: {feedback[:100]}...")

        # MemorySaver is in-process only — state is lost across worker invocations.
        # Re-run a fresh generation from DB with feedback injected into the prompt.
        form_result = supabase.table("forms").select("*").eq("id", form_id).execute()
        if not form_result.data:
            raise Exception(f"Form {form_id} not found")
        form = form_result.data[0]
        fields = json.loads(form["fields"]) if isinstance(form.get("fields"), str) else form["fields"]

        # Load the previous decomposition from form metadata so the LLM knows what it generated before
        previous_decomposition = None
        metadata_str = form.get("metadata")
        if metadata_str:
            try:
                metadata = json.loads(metadata_str) if isinstance(metadata_str, str) else metadata_str
                previous_decomposition = metadata.get("decomposition")
            except Exception:
                pass

        form_data = {
            "form_name": form["form_name"],
            "form_description": form.get("form_description", ""),
            "fields": fields,
            "human_feedback": feedback,
            "previous_decomposition": previous_decomposition,
        }

        from app.services.code_generation_service import code_generation_service
        result = code_generation_service.generate_extraction_code(
            form_id=form_id,
            form_data=form_data,
            enable_review=True,
            max_attempts=3,
            log_callback=log_callback
        )

        # Paused again for another review round
        if result.get("status") == "awaiting_human_review" or result.get("paused"):
            logger.info("Workflow paused again for review after regeneration")
            broadcaster.info("⏸️  New decomposition ready for review")
            new_thread_id = result.get("thread_id", thread_id)
            metadata = {
                "thread_id": new_thread_id,
                "task_name": task_name,
                "decomposition": result.get("decomposition", {}),
                "decomposition_summary": result.get("decomposition_summary", "")
            }
            supabase.table("forms").update({
                "status": FormStatus.AWAITING_REVIEW.value,
                "metadata": json.dumps(metadata)
            }).eq("id", form_id).execute()
            supabase.table("jobs").update({
                "status": JobStatus.COMPLETED.value,
                "progress": 50
            }).eq("id", job_id).execute()
            return {"status": "awaiting_review", "form_id": form_id}

        broadcaster.info("Regeneration completed")
        supabase.table("forms").update({
            "status": FormStatus.GENERATING.value
        }).eq("id", form_id).execute()
        supabase.table("jobs").update({
            "status": JobStatus.COMPLETED.value,
            "progress": 75
        }).eq("id", job_id).execute()

        # Log activity on rejection-resume completion
        job_record = supabase.table("jobs").select("user_id, project_id").eq("id", job_id).execute()
        if job_record.data:
            _user_id = job_record.data[0]["user_id"]
            _project_id = job_record.data[0].get("project_id")
            sync_log_activity(
                user_id=_user_id,
                action_type="code_generation",
                action="Regeneration After Rejection Completed",
                description=f"Regeneration completed after rejection for form: {form['form_name']}",
                project_id=_project_id,
                metadata={"form_id": form_id, "feedback": feedback[:200]},
            )

        return {"status": "generating", "form_id": form_id}

    except Exception as e:
        error_msg = str(e)
        logger.error(f"Error in resume_after_rejection: {error_msg}", exc_info=True)

        try:
            broadcaster = CeleryLogBroadcaster(job_id)
            broadcaster.error(f"❌ Error: {error_msg}")
        except Exception:
            logger.debug("Broadcaster failed in resume_after_approval error handler", exc_info=True)

        try:
            supabase.table("forms").update({
                "status": FormStatus.FAILED.value,
                "error": error_msg
            }).eq("id", form_id).execute()

            supabase.table("jobs").update({
                "status": JobStatus.FAILED.value,
                "error_message": error_msg
            }).eq("id", job_id).execute()
        except Exception as db_error:
            logger.error(f"Failed to update database: {db_error}")

        raise


@celery_app.task(name="check_code_generator_health")
def check_code_generator_health():
    """
    Health check task for code generator.

    Returns:
        Dictionary with generator status
    """
    return code_generation_service.check_generator_status()
