"""
Celery tasks for form code generation.
"""

import logging
import json
from datetime import datetime, timezone
from supabase import create_client

from app.workers.celery_app import celery_app
from app.workers.log_broadcaster import CeleryLogBroadcaster
from app.config import settings
from app.services.code_generation_service import code_generation_service
from app.models.enums import FormStatus, JobStatus
from app.workers.utils import sync_log_activity, sync_notify
from app.services.cache_service import cache_service
from utils.run_context import set_current_job_id

logger = logging.getLogger(__name__)

# Initialize Supabase client
supabase = create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_KEY)


def _make_log_callback(broadcaster: CeleryLogBroadcaster):
    """Create a log_callback that detects structured data and stage transitions."""
    def log_callback(message: str, level: str = "info"):
        # Handle structured JSON messages (field_list, field_done)
        if message.startswith("{"):
            try:
                data = json.loads(message)
                broadcaster.data(data, data.get("_type", ""))
                return
            except (json.JSONDecodeError, TypeError):
                pass
        # Detect stage transitions from workflow messages and update progress
        if "Cognitive Decomposition" in message:
            broadcaster.stage("decomposing", message)
            broadcaster.progress(40, "Grouping related fields...")
        elif "Generating" in message and "signature" in message.lower():
            broadcaster.stage("generating_signatures", message)
            broadcaster.progress(55, "Building extraction rules...")
        elif "Generating extractor modules" in message:
            broadcaster.stage("generating_modules", message)
            broadcaster.progress(70, "Building extraction rules...")
        elif "Assembling final" in message:
            broadcaster.stage("finalizing", message)
            broadcaster.progress(85, "Finalizing...")
        # Always broadcast the log message
        if level == "error":
            broadcaster.error(message)
        elif level == "warning":
            broadcaster.warning(message)
        elif level == "success":
            broadcaster.success(message)
        else:
            broadcaster.info(message)
    return log_callback


def _mirror_enriched_subfields(fields: list, schema_def: dict) -> list:
    """Write enriched subform_fields (hints/rules/examples) from schema_def into fields list.

    After signature generation, schema_def carries LLM-enriched per-column
    hints/rules/examples. This mirrors them back into forms.fields JSONB so
    both sources stay in sync from the very first save (Move 5 of the root-cause fix).
    User structural data (field_name, field_type) is always preserved.
    """
    if not schema_def or not fields:
        return fields

    # Build lookup: field_name → enriched subform_fields list from schema_def
    enriched_sf: dict = {}
    for sig in schema_def.get("signatures", []):
        for of in sig.get("output_fields", []):
            sfs = of.get("subform_fields")
            fname = of.get("name", "")
            if fname and sfs:
                enriched_sf[fname] = sfs

    if not enriched_sf:
        return fields

    result = []
    for field in fields:
        fname = field.get("field_name", "")
        if fname in enriched_sf:
            existing_by_name = {
                sf.get("field_name", ""): sf
                for sf in (field.get("subform_fields") or [])
            }
            merged = []
            for esf in enriched_sf[fname]:
                cname = esf.get("field_name", "")
                base = dict(existing_by_name.get(cname) or {})
                # Overlay enriched prose/hints/rules/examples; never touch field_type
                for key in ("field_description", "hints", "rules", "examples", "options"):
                    val = esf.get(key)
                    if val:  # non-empty string or non-empty list
                        base[key] = val
                if not base:
                    base = esf
                merged.append(base)
            field = {**field, "subform_fields": merged}
        result.append(field)
    return result


def _invalidate_form_caches(project_id: str, form_id: str):
    """Invalidate Redis form caches after a status change in the worker."""
    try:
        if project_id:
            cache_service.delete_pattern(f"forms:project:{project_id}:*")
        cache_service.delete(f"forms:detail:{form_id}")
    except Exception as e:
        logger.warning(f"Cache invalidation failed (non-fatal): {e}")


def _form_still_exists(form_id: str) -> bool:
    """Return True if the form row still exists in the database."""
    try:
        result = supabase.table("forms").select("id").eq("id", form_id).execute()
        return bool(result.data)
    except Exception:
        return True  # assume it exists on DB error to avoid false aborts


REVISION_HISTORY_CAP = 4


def _signatures_differ(a: dict, b: dict) -> bool:
    """Return True if two decompositions have meaningfully different signatures."""
    def fingerprint(d: dict):
        return sorted(
            (s.get("name"), tuple(sorted((s.get("fields") or {}).keys())))
            for s in (d.get("signatures") or [])
        )
    return fingerprint(a or {}) != fingerprint(b or {})


def _read_existing_metadata(form_id: str) -> dict:
    """Fetch the form's current metadata as a dict, or {}."""
    try:
        row = supabase.table("forms").select("metadata").eq("id", form_id).execute()
        if not row.data:
            return {}
        raw = row.data[0].get("metadata")
        if not raw:
            return {}
        return json.loads(raw) if isinstance(raw, str) else (raw or {})
    except Exception as e:
        logger.warning(f"Failed to read existing metadata for {form_id}: {e}")
        return {}


def _build_completion_metadata(form_id: str, result: dict) -> dict:
    """
    Build a fresh `metadata` dict for a form update, while:
      - appending the previous decomposition to revision_history (Phase 2 B6)
      - preserving review_notes, current_job_id from existing metadata
    """
    existing = _read_existing_metadata(form_id)
    new_decomp = result.get("decomposition", {}) or {}

    history = list(existing.get("revision_history") or [])
    prev_decomp = existing.get("decomposition")
    if prev_decomp and _signatures_differ(prev_decomp, new_decomp):
        history.append({
            "decomposition": prev_decomp,
            "validation_results": existing.get("validation_results"),
            "review_notes": existing.get("review_notes", []),
            "archived_at": datetime.now(timezone.utc).isoformat(),
        })
    history = history[-REVISION_HISTORY_CAP:]

    meta = {
        "thread_id": result.get("thread_id"),
        "task_name": result.get("task_name"),
        "decomposition": new_decomp,
        "decomposition_summary": result.get("decomposition_summary", ""),
        "validation_results": result.get("validation_results")
            or new_decomp.get("validation_results", {}),
        "revision_history": history,
    }
    # Carried forward, not regenerated: these are user choices, not codegen
    # output. table_extraction_mode in particular would otherwise be silently
    # reset to standard by every regenerate.
    for k in ("review_notes", "current_job_id", "table_extraction_mode"):
        if k in existing:
            meta[k] = existing[k]
    return meta


def _carry_extraction_mode(schema_def: dict, metadata: dict) -> dict:
    """Re-apply the per-form table extraction mode onto a freshly built schema_def.

    Codegen rebuilds schema_def from scratch, and schema_def — not metadata — is
    what the runtime reads (build_schema_classes). Without this, every regenerate
    silently reverts an agentic form to standard while the UI still shows agentic.
    """
    mode = (metadata or {}).get("table_extraction_mode")
    if mode and isinstance(schema_def, dict):
        schema_def["table_extraction_mode"] = mode
        logger.info("Carried table_extraction_mode=%s into regenerated schema_def", mode)
    return schema_def


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
        set_current_job_id(job_id)  # stamp codegen LLM calls with this job for cost attribution

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
        project_id = form.get("project_id")

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
        broadcaster.stage("initializing", "Analyzing your form...")
        log_callback = _make_log_callback(broadcaster)

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
            broadcaster.stage("awaiting_review", "Waiting for your review")
            broadcaster.info("⏸️  Workflow paused for human review")
            broadcaster.info("Please review the decomposition in the frontend")

            # Store workflow thread_id and decomposition in form metadata
            decomposition = result.get("decomposition", {})
            logger.info(f"Decomposition keys: {decomposition.keys() if decomposition else 'None'}")
            logger.info(f"Signatures count: {len(decomposition.get('signatures', [])) if decomposition else 0}")
            logger.info(f"Pipeline count: {len(decomposition.get('pipeline', [])) if decomposition else 0}")

            metadata = _build_completion_metadata(form_id, result)

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

            # Invalidate cache so frontend sees awaiting_review immediately
            _invalidate_form_caches(project_id, form_id)

            # Broadcast complete event so frontend can react in real-time
            broadcaster._broadcast_message({
                "type": "complete",
                "job_id": str(job_id),
                "status": "awaiting_review",
            })

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
            metadata = _build_completion_metadata(form_id, result)
            update_data = {
                "status": FormStatus.ACTIVE.value,
                "schema_name": result["schema_name"],
                "task_dir": result["task_dir"],
                "statistics": json.dumps(result.get("statistics", {})),
                "metadata": json.dumps(metadata),
                "error": None,
            }
            if result.get("schema_def"):
                update_data["schema_def"] = _carry_extraction_mode(
                    result["schema_def"], metadata
                )
                # Mirror enriched subform_fields into forms.fields (Move 5)
                enriched_fields = _mirror_enriched_subfields(fields, result["schema_def"])
                if enriched_fields is not fields:
                    update_data["fields"] = enriched_fields
            supabase.table("forms").update(update_data).eq("id", form_id).execute()
            _invalidate_form_caches(project_id, form_id)
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
            broadcaster._broadcast_message({
                "type": "complete",
                "job_id": str(job_id),
                "status": "completed",
            })

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
            broadcaster._broadcast_message({
                "type": "complete",
                "job_id": str(job_id),
                "status": "failed",
            })

            # Update form status to failed
            supabase.table("forms").update({
                "status": FormStatus.FAILED.value,
                "error": error_msg
            }).eq("id", form_id).execute()
            _invalidate_form_caches(project_id, form_id)

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
        set_current_job_id(job_id)  # stamp codegen LLM calls with this job for cost attribution
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

        log_callback = _make_log_callback(broadcaster)

        orchestrator = WorkflowOrchestrator(
            human_review_enabled=False,
            log_callback=log_callback
        )

        broadcaster.stage("generating_signatures", "Building extraction rules from approved plan...")
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
            metadata = _build_completion_metadata(form_id, result)
            supabase.table("forms").update({
                "status": FormStatus.AWAITING_REVIEW.value,
                "metadata": json.dumps(metadata)
            }).eq("id", form_id).execute()
            _invalidate_form_caches(form.get("project_id"), form_id)

            supabase.table("jobs").update({
                "status": JobStatus.COMPLETED.value,
                "progress": 50
            }).eq("id", job_id).execute()

            broadcaster.info("⏸️  Workflow paused again for review")
            return {"status": "awaiting_review", "form_id": form_id}

        if result.get("success"):
            logger.info(f"Code generation completed after approval - form: {form_id}")
            broadcaster.success("✅ Code generation completed!")

            schema_def = result.get("schema_def")
            if not schema_def:
                error_msg = "schema_def not built after approval — cannot activate form"
                logger.error(error_msg)
                broadcaster.error(error_msg)
                supabase.table("forms").update({
                    "status": FormStatus.FAILED.value,
                    "error": error_msg
                }).eq("id", form_id).execute()
                _invalidate_form_caches(form.get("project_id"), form_id)
                supabase.table("jobs").update({
                    "status": JobStatus.FAILED.value,
                    "error_message": error_msg
                }).eq("id", job_id).execute()
                return {"status": "failed", "form_id": form_id, "error": error_msg}

            # Register schema from schema_def (no disk writes)
            from schemas.config import DynamicSchemaConfig
            from schemas.registry import register_schema
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
                form_name=form["form_name"],
                schema_def=schema_def,
            )
            register_schema(schema_config)
            broadcaster.success(f"✓ Schema registered: {task_name}")

            # Update form to active
            decomposition = result.get("decomposition", {})
            metadata = _build_completion_metadata(form_id, {
                "thread_id": thread_id,
                "task_name": task_name,
                "decomposition": decomposition,
                "decomposition_summary": result.get("decomposition_summary", ""),
            })
            form_update = {
                "status": FormStatus.ACTIVE.value,
                "schema_name": task_name,
                "task_dir": None,
                "statistics": json.dumps(result.get("statistics", {})),
                "metadata": json.dumps(metadata),
                "error": None,
                "schema_def": _carry_extraction_mode(schema_def, metadata),
            }
            # Mirror enriched subform_fields into forms.fields (Move 5)
            enriched_fields = _mirror_enriched_subfields(fields, schema_def)
            if enriched_fields is not fields:
                form_update["fields"] = enriched_fields
            supabase.table("forms").update(form_update).eq("id", form_id).execute()
            _invalidate_form_caches(form.get("project_id"), form_id)

            # Update job to completed
            supabase.table("jobs").update({
                "status": JobStatus.COMPLETED.value,
                "progress": 100,
                "result_data": {
                    "schema_name": task_name,
                    "task_dir": None,
                    "statistics": result.get("statistics", {})
                }
            }).eq("id", job_id).execute()

            broadcaster.progress(100, "Complete! Form ready for extraction.")
            broadcaster._broadcast_message({
                "type": "complete",
                "job_id": str(job_id),
                "status": "completed",
            })

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
            _invalidate_form_caches(form.get("project_id"), form_id)

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
def resume_after_rejection(self, form_id: str, job_id: str, thread_id: str, task_name: str, feedback: str, accepted_refs: list = None):
    """
    Resume workflow after user rejects decomposition with feedback.

    Args:
        self: Celery task instance
        form_id: UUID of the form
        job_id: UUID of the job record
        thread_id: Workflow thread ID for resumption
        task_name: Task directory name
        feedback: User feedback for regeneration
        accepted_refs: Signature names the reviewer locked (Phase 2 B5)
    """
    try:
        broadcaster = CeleryLogBroadcaster(job_id)
        set_current_job_id(job_id)  # stamp codegen LLM calls with this job for cost attribution
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

        log_callback = _make_log_callback(broadcaster)

        orchestrator = WorkflowOrchestrator(
            human_review_enabled=True,
            log_callback=log_callback
        )

        broadcaster.stage("decomposing", "Regrouping fields with your feedback...")
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

        # Phase 2 B5: build locked_signatures list from accepted_refs
        locked_signatures = []
        if accepted_refs and previous_decomposition:
            accepted_set = set(accepted_refs)
            locked_signatures = [
                sig for sig in (previous_decomposition.get("signatures") or [])
                if sig.get("name") in accepted_set
            ]
        # Also stash in form_data["_locked_signatures"] for the validator
        form_data = {
            "form_name": form["form_name"],
            "form_description": form.get("form_description", ""),
            "fields": fields,
            "human_feedback": feedback,
            "previous_decomposition": previous_decomposition,
            "locked_signatures": locked_signatures,
            "_locked_signatures": locked_signatures,
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
            metadata = _build_completion_metadata(form_id, {
                "thread_id": new_thread_id,
                "task_name": task_name,
                "decomposition": result.get("decomposition", {}),
                "decomposition_summary": result.get("decomposition_summary", ""),
                "validation_results": result.get("validation_results"),
            })
            supabase.table("forms").update({
                "status": FormStatus.AWAITING_REVIEW.value,
                "metadata": json.dumps(metadata)
            }).eq("id", form_id).execute()
            _invalidate_form_caches(form.get("project_id"), form_id)
            supabase.table("jobs").update({
                "status": JobStatus.COMPLETED.value,
                "progress": 50
            }).eq("id", job_id).execute()
            return {"status": "awaiting_review", "form_id": form_id}

        if result.get("success"):
            logger.info(f"Regeneration after rejection succeeded - form: {form_id}")
            broadcaster.success("✅ Code generation completed!")

            # Update form with generated code information
            metadata = _build_completion_metadata(form_id, result)
            supabase.table("forms").update({
                "status": FormStatus.ACTIVE.value,
                "schema_name": result["schema_name"],
                "task_dir": result["task_dir"],
                "statistics": json.dumps(result.get("statistics", {})),
                "metadata": json.dumps(metadata),
                "error": None,
            }).eq("id", form_id).execute()
            _invalidate_form_caches(form.get("project_id"), form_id)

            supabase.table("jobs").update({
                "status": JobStatus.COMPLETED.value,
                "progress": 100,
                "result_data": {
                    "schema_name": result["schema_name"],
                    "task_dir": result["task_dir"],
                    "statistics": result.get("statistics", {})
                }
            }).eq("id", job_id).execute()

            broadcaster.info(f"💾 Saved to: {result['schema_name']}")
            broadcaster._broadcast_message({
                "type": "complete",
                "job_id": str(job_id),
                "status": "completed",
            })

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

            return {
                "status": "success",
                "form_id": form_id,
                "schema_name": result["schema_name"],
                "task_dir": result["task_dir"],
            }
        else:
            error_msg = result.get("error", "Unknown error during regeneration after rejection")
            logger.error(f"Regeneration after rejection failed for form {form_id}: {error_msg}")
            broadcaster.error(f"❌ Generation failed: {error_msg}")
            broadcaster._broadcast_message({
                "type": "complete",
                "job_id": str(job_id),
                "status": "failed",
            })
            supabase.table("forms").update({
                "status": FormStatus.FAILED.value,
                "error": error_msg
            }).eq("id", form_id).execute()
            _invalidate_form_caches(form.get("project_id"), form_id)
            supabase.table("jobs").update({
                "status": JobStatus.FAILED.value,
                "error_message": error_msg
            }).eq("id", job_id).execute()
            return {"status": "failed", "form_id": form_id, "error": error_msg}

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
