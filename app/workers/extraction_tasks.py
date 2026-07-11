"""
Celery tasks for extraction jobs.
"""

import logging
import json
import tempfile
import asyncio
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional
from supabase import create_client

from app.workers.celery_app import celery_app
from app.config import settings
from app.services.extraction_service import extraction_service
from app.services.storage_service import storage_service
from app.models.enums import JobStatus
from app.workers.utils import sync_log_activity, sync_notify

logger = logging.getLogger(__name__)

# Initialize Supabase client
supabase = create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_KEY)


@celery_app.task(
    bind=True,
    name="run_extraction",
    max_retries=3,
    default_retry_delay=30,
    autoretry_for=(ConnectionError, TimeoutError),
    retry_backoff=True,
    retry_backoff_max=300,
    retry_jitter=True,
)
def run_extraction(
    self,
    extraction_id: str,
    job_id: str,
    document_ids: Optional[List[str]] = None,
    max_documents: Optional[int] = None,
    model: Optional[str] = None,
):
    """
    Background task to run extraction on documents.

    Args:
        self: Celery task instance (for updating state)
        extraction_id: UUID of the extraction job
        job_id: UUID of the job record
        document_ids: Optional list of specific document IDs to extract
        max_documents: Optional limit on number of documents to process
        model: Optional per-job LLM id ("anthropic/claude-sonnet-4-6", etc).
               Resolved upstream from the user's Settings → AI Model (Beta)
               selection. Falls back to DEFAULT_MODEL when None or unknown.

    Returns:
        Dictionary with extraction results
    """
    # Validate / normalize the model id here too — defence-in-depth in case
    # an older job row was queued before the allowlist tightened.
    from config.models import AVAILABLE_MODEL_IDS, DEFAULT_MODEL
    if model not in AVAILABLE_MODEL_IDS:
        model = DEFAULT_MODEL
    try:
        logger.info(f"Starting extraction job {extraction_id}")

        from app.workers.log_broadcaster import CeleryLogBroadcaster
        broadcaster = CeleryLogBroadcaster(str(job_id))

        # Capture job start time for post-run llm_history cost rollup.
        _job_started_at = datetime.now(timezone.utc).isoformat()

        # Stamp this job onto every llm_history row flushed during the run so
        # per-run cost/token attribution is exact. asyncio.run() copies this
        # context into the extraction coroutine. See utils/run_context.py.
        from utils.run_context import set_current_job_id
        set_current_job_id(job_id)

        # Update job status to processing (record wall-clock start for duration).
        supabase.table("jobs").update({
            "status": JobStatus.PROCESSING.value,
            "progress": 10,
            "started_at": _job_started_at,
        }).eq("id", job_id).execute()

        # Get extraction configuration from database
        extraction_result = supabase.table("extractions")\
            .select("*, forms(schema_name, task_dir, metadata, fields, schema_def)")\
            .eq("id", extraction_id)\
            .execute()

        if not extraction_result.data:
            raise Exception(f"Extraction {extraction_id} not found")

        extraction = extraction_result.data[0]
        form = extraction.get("forms")

        if not form:
            raise Exception(f"Form not found for extraction {extraction_id}")

        schema_name = form.get("schema_name")
        if not schema_name:
            raise Exception(f"Schema name not found for extraction {extraction_id}")

        # Evict this schema from the worker's local L1 cache so the next
        # get_schema() call falls through to Redis/Supabase and picks up any
        # field-prompt edits made since the worker process started.
        try:
            from schemas.registry import _SCHEMA_REGISTRY
            from dspy_components.runtime_builders import clear_class_cache
            _SCHEMA_REGISTRY.pop(schema_name, None)
            clear_class_cache()
        except Exception:
            pass

        logger.info(f"Using schema: {schema_name}")

        # LOG SIGNATURES — remove this block once reviewed
        try:
            from schemas.registry import get_schema as _get_schema
            _sig_log = Path("/home/ubuntu/evistream/logs/signatures.log")
            _config = _get_schema(schema_name)
            if _config.schema_def:
                _sdef = _config.schema_def
                _sig_map = {s["class_name"]: s for s in _sdef.get("signatures", [])}
                _pipeline_stages = _sdef.get("pipeline_stages", [])
                _lines = [f"\n{'='*60}", f"Schema: {schema_name}", f"Job: {job_id}", f"{'='*60}"]
                for _st in _pipeline_stages:
                    _snum = _st["stage"]
                    _exec = _st["execution"].upper()
                    _requires = _st.get("requires_fields") or []
                    _lines.append(f"\n{'─'*60}")
                    _lines.append(f"STAGE {_snum} [{_exec}]" + (f"  depends on: {_requires}" if _requires else ""))
                    _lines.append(f"{'─'*60}")
                    for _cname in _st.get("signatures", []):
                        _sig = _sig_map.get(_cname, {})
                        _lines.append(f"\n  Signature: {_cname}")
                        _lines.append(f"  Docstring: {_sig.get('docstring','')}")
                        _lines.append(f"  Inputs:    {[f['name'] for f in _sig.get('input_fields', [])]}")
                        _lines.append(f"  Outputs:   {[f['name'] for f in _sig.get('output_fields', [])]}")
                        for _f in _sig.get("output_fields", []):
                            _lines.append(f"    [{_f['name']}]")
                            _lines.append(f"      desc:     {_f.get('description','')}")
                            _lines.append(f"      hints:    {_f.get('hints', [])}")
                            _lines.append(f"      rules:    {_f.get('rules', [])}")
                            if _f.get("options"):
                                _lines.append(f"      options:  {_f['options']}")
                            if _f.get("examples"):
                                _lines.append(f"      examples: {[e.get('value') for e in _f['examples']]}")

                            # Reflect runtime 2-stage wrap (row_then_columns)
                            if _f.get("extraction_strategy") == "row_then_columns" and _f.get("anchor_columns"):
                                _anchors = list(_f.get("anchor_columns") or [])
                                _all_subs = [_sf.get("field_name") for _sf in (_f.get("subform_fields") or [])]
                                _value_cols = [_c for _c in _all_subs if _c not in set(_anchors)]
                                _lines.append(f"      ↳ RUNTIME 2-STAGE WRAP for '{_f['name']}'")
                                _lines.append(f"        ├─ S1 (1 call):  DiscoverRows  anchors={_anchors}")
                                _lines.append(f"        └─ S2 (1 call per discovered row, parallel — each row fills all {len(_value_cols)} value cols):")
                                for _vc in _value_cols:
                                    _lines.append(f"             • {_vc}")
                with open(_sig_log, "a") as _fh:
                    _fh.write("\n".join(_lines) + "\n")
        except Exception as _e:
            logger.debug(f"signature logger failed: {_e}")
        # END LOG SIGNATURES

        # Load pilot calibration feedback if available
        pilot_feedback = None
        form_metadata = form.get("metadata") or {}
        if isinstance(form_metadata, str):
            try:
                form_metadata = json.loads(form_metadata)
            except (json.JSONDecodeError, TypeError):
                form_metadata = {}
        pilot_data = form_metadata.get("pilot") or {}
        if pilot_data.get("field_examples") or pilot_data.get("field_instructions"):
            pilot_feedback = {
                "field_examples": pilot_data.get("field_examples", {}),
                "field_instructions": pilot_data.get("field_instructions", {}),
            }
            logger.info(
                f"Loaded pilot feedback: {len(pilot_feedback['field_examples'])} fields with examples, "
                f"{len(pilot_feedback['field_instructions'])} fields with instructions"
            )

        # Note: review-time field edits (hints/rules/examples/description) are spliced
        # directly into signatures.py at save time — no runtime loading needed here.

        # Note: results are upserted below on (extraction_id, document_id) to ensure idempotency

        # Get documents for this project
        project_id = extraction.get("project_id")
        documents_query = supabase.table("documents")\
            .select("id, s3_markdown_path, s3_blocks_path, processing_status")\
            .eq("project_id", project_id)\
            .eq("processing_status", "completed")

        # Filter by specific document IDs if provided
        if document_ids:
            documents_query = documents_query.in_("id", document_ids)

        documents_result = documents_query.execute()

        if not documents_result.data:
            raise Exception(f"No processed documents found for project {project_id}")

        documents = documents_result.data
        logger.info(f"Found {len(documents)} processed documents")

        # Update job progress
        supabase.table("jobs").update({
            "progress": 20
        }).eq("id", job_id).execute()

        # Initialize tracking variables
        failed_doc_ids: list = []
        paper_results: dict = {}  # doc_id → normalized extracted_data

        # Always use the staged pipeline path (run_files_extraction → run_batch)
        # for consistent stage logging and a single asyncio.run() call.
        s3_docs = {doc["s3_markdown_path"]: doc["id"] for doc in documents if doc.get("s3_markdown_path")}
        # Parallel map of s3_markdown_path → s3_blocks_path (when available).
        # Documents parsed before the Datalab blocks pipeline existed have no
        # sidecar and will simply skip bbox enrichment (graceful degrade).
        s3_blocks_by_md = {
            doc["s3_markdown_path"]: doc["s3_blocks_path"]
            for doc in documents
            if doc.get("s3_markdown_path") and doc.get("s3_blocks_path")
        }

        if not s3_docs:
            raise Exception("No markdown files found for selected documents")

        logger.info(f"Running extraction on {len(s3_docs)} document(s) via staged pipeline")

        tmp_dir = tempfile.mkdtemp(prefix="evistream_extraction_")
        valid_path_to_doc_id = {}
        valid_path_to_blocks_path = {}
        try:
            for s3_key, doc_id in s3_docs.items():
                try:
                    response = storage_service.s3_client.get_object(
                        Bucket=settings.S3_BUCKET,
                        Key=s3_key
                    )
                    local_path = os.path.join(tmp_dir, Path(s3_key).name)
                    with open(local_path, "wb") as f:
                        f.write(response["Body"].read())
                    valid_path_to_doc_id[local_path] = doc_id

                    # Best-effort download of the blocks sidecar alongside the
                    # markdown. Used downstream by enrich_extraction_results to
                    # attach deterministic PDF bboxes to each source_location.
                    blocks_s3_key = s3_blocks_by_md.get(s3_key)
                    if blocks_s3_key:
                        try:
                            blocks_resp = storage_service.s3_client.get_object(
                                Bucket=settings.S3_BUCKET,
                                Key=blocks_s3_key
                            )
                            blocks_local_path = os.path.join(
                                tmp_dir, Path(blocks_s3_key).name
                            )
                            with open(blocks_local_path, "wb") as bf:
                                bf.write(blocks_resp["Body"].read())
                            valid_path_to_blocks_path[local_path] = blocks_local_path
                        except Exception as be:
                            logger.warning(
                                f"Could not download blocks sidecar {blocks_s3_key}: {be}; "
                                f"continuing without bbox enrichment for this doc"
                            )
                except Exception as e:
                    logger.warning(f"Could not download {s3_key} from S3: {e}, skipping")

            missing = len(s3_docs) - len(valid_path_to_doc_id)
            if missing:
                logger.warning(f"{missing} markdown file(s) could not be downloaded, skipping")

            if not valid_path_to_doc_id:
                raise Exception(f"All {len(s3_docs)} markdown file(s) failed to download from S3 — cannot extract")

            total_papers = len(valid_path_to_doc_id)
            completed_papers = 0

            # Write the denominator early so the coverage endpoint's
            # active_jobs.papers_total is correct on first paint / page refresh
            # mid-run (before the first paper_done WS event arrives). The final
            # completion write (below) overwrites this with the real counts.
            try:
                supabase.table("jobs").update({
                    "result_data": {
                        "total_documents": total_papers,
                        "successful_extractions": 0,
                        "failed_extractions": 0,
                    }
                }).eq("id", job_id).execute()
            except Exception as e:
                logger.warning(f"Early total_documents write failed: {e}")

            async def on_paper_done(doc_id: str, paper_result: dict):
                nonlocal completed_papers, failed_doc_ids
                completed_papers += 1

                is_success = bool(paper_result.get("success")) and not isinstance(paper_result, Exception)
                if not is_success:
                    failed_doc_ids.append(doc_id)

                # Collect in-memory — batch INSERT happens after asyncio.run() returns
                if paper_result.get("success") and paper_result.get("results"):
                    for r in paper_result["results"]:
                        if not isinstance(r, dict):
                            continue
                        data = dict(r)
                        data.pop("document_id", None)
                        data.pop("source_file", None)
                        extracted = data.get("results", data)
                        if isinstance(extracted, list) and len(extracted) == 1:
                            extracted = extracted[0]
                        paper_results[doc_id] = extracted if isinstance(extracted, dict) else {"data": extracted}

                # Progress: throttle to 10% boundaries (~7 updates for 30 papers), offload to thread
                pct = 20 + int((completed_papers / total_papers) * 70)
                prev_pct = 20 + int(((completed_papers - 1) / total_papers) * 70)
                if pct // 10 != prev_pct // 10:
                    try:
                        loop = asyncio.get_running_loop()
                        await loop.run_in_executor(
                            None,
                            lambda: supabase.table("jobs").update({"progress": pct}).eq("id", job_id).execute()
                        )
                        broadcaster.progress(pct, f"Extracted {completed_papers}/{total_papers} papers")
                    except Exception as e:
                        logger.warning(f"Progress update failed: {e}")

                # Broadcast paper_done WS event for real-time progress
                try:
                    broadcaster._broadcast_message({
                        "type": "paper_done",
                        "job_id": str(job_id),
                        "document_id": doc_id,
                        "success": is_success,
                        "papers_done": completed_papers,
                        "papers_total": total_papers,
                        "progress": pct,
                    })
                except Exception as e:
                    logger.warning(f"paper_done broadcast failed: {e}")

            result = extraction_service.run_files_extraction(
                path_to_doc_id=valid_path_to_doc_id,
                schema_name=schema_name,
                on_paper_done=on_paper_done,
                pilot_feedback=pilot_feedback,
                path_to_blocks_path=valid_path_to_blocks_path or None,
                model_name=model,
            )

            # Broadcast a cost summary for rows logged since job start.
            try:
                from config.pricing import compute_cost
                cost_rows = (
                    supabase.table("llm_history")
                    .select("model,cost,prompt_tokens,completion_tokens,total_tokens,cache_hit")
                    .gte("created_at", _job_started_at)
                    .limit(5000)
                    .execute()
                ).data or []
                if cost_rows:
                    total_tokens = sum(int(r.get("total_tokens") or 0) for r in cost_rows)
                    cache_hits = sum(1 for r in cost_rows if r.get("cache_hit"))
                    total_cost = 0.0
                    for r in cost_rows:
                        stored = float(r.get("cost") or 0)
                        if stored > 0:
                            total_cost += stored
                        else:
                            total_cost += compute_cost(
                                r.get("model") or "",
                                int(r.get("prompt_tokens") or 0),
                                int(r.get("completion_tokens") or 0),
                            )
                    broadcaster._broadcast_message({
                        "type": "cost",
                        "total_cost_usd": round(total_cost, 4),
                        "total_tokens": total_tokens,
                        "calls": len(cost_rows),
                        "cache_hits": cache_hits,
                        "model": model,
                    })
            except Exception as e:
                logger.warning(f"Cost broadcast failed (non-fatal): {e}")
        finally:
            import shutil
            shutil.rmtree(tmp_dir, ignore_errors=True)

        # Update job progress
        supabase.table("jobs").update({
            "progress": 90
        }).eq("id", job_id).execute()

        if result.get("success"):
            logger.info(f"Extraction successful for job {extraction_id}")

            # LOG STAGE-1 ROWS to signatures.log for 2-stage fields
            try:
                _sig_log = os.path.join(os.path.dirname(__file__), "../../../logs/signatures.log")
                _sig_log = os.path.normpath(_sig_log)
                _sd = form.get("schema_def") or {}
                if isinstance(_sd, str):
                    import json as _json
                    _sd = _json.loads(_sd)
                # Build map: field_name → anchor_columns for 2-stage fields
                _two_stage_fields: dict = {}
                for _sig in (_sd.get("signatures") or []):
                    for _of in (_sig.get("output_fields") or []):
                        if (
                            _of.get("extraction_strategy") == "row_then_columns"
                            and _of.get("anchor_columns")
                        ):
                            _two_stage_fields[_of["name"]] = _of["anchor_columns"]

                if _two_stage_fields and paper_results:
                    _s1_lines = [f"\n{'='*60}", f"STAGE 1 RESULTS — job {extraction_id}"]
                    for _doc_id, _extracted in paper_results.items():
                        _s1_lines.append(f"  doc: {_doc_id}")
                        for _fname, _anchors in _two_stage_fields.items():
                            _rows = _extracted.get(_fname) or []
                            if isinstance(_rows, dict):
                                # Envelope shape: rows live under "value".
                                _inner = _rows.get("value")
                                _rows = _inner if isinstance(_inner, list) else [_rows]
                            _s1_lines.append(f"  [{_fname}]  {len(_rows)} row(s) discovered:")
                            for _ri, _row in enumerate(_rows):
                                _anchor_vals = []
                                for _ac in _anchors:
                                    _v = _row.get(_ac)
                                    if isinstance(_v, dict):
                                        _v = _v.get("value", _v)
                                    _anchor_vals.append(f"{_ac}={str(_v)[:40]!r}")
                                _s1_lines.append(f"    row[{_ri}]: {',  '.join(_anchor_vals)}")
                    _s1_lines.append("")
                    with open(_sig_log, "a") as _fh:
                        _fh.write("\n".join(_s1_lines) + "\n")
            except Exception as _e:
                logger.debug(f"Stage-1 row logger failed: {_e}")
            # END LOG STAGE-1 ROWS

            # Save extraction results
            extraction_results = result.get("results", [])

            # Build a lookup from markdown path to document ID
            doc_path_to_id = {d["s3_markdown_path"]: d["id"] for d in documents}

            # Store results — always via paper_results collected by on_paper_done callback.
            # Uses the replace_ai_extraction_results RPC (phase4_001 migration) so the
            # replace happens in one transaction; a mid-call failure can no longer leave
            # the table empty for these documents.
            stored_count = 0
            if paper_results:
                records = [
                    {
                        "extraction_id": extraction_id,
                        "job_id": str(job_id),
                        "project_id": extraction.get("project_id"),
                        "form_id": extraction.get("form_id"),
                        "document_id": doc_id,
                        "extracted_data": extracted_data,
                        "extraction_type": "ai",
                        "model": model,
                    }
                    for doc_id, extracted_data in paper_results.items()
                ]
                try:
                    supabase.rpc(
                        "replace_ai_extraction_results",
                        {"p_records": records},
                    ).execute()
                except Exception as e:
                    logger.error(f"Atomic upsert of AI results failed: {e}")
                    raise
            stored_count = len(paper_results)

            logger.info(f"Stored {stored_count}/{len(extraction_results)} extraction results")

            # Determine extraction status based on success rate
            total_docs = len(documents)
            successful = result.get("successful_extractions", len(documents))
            failed = result.get("failed_extractions", 0)

            # Set status based on results
            if failed == 0:
                extraction_status = "completed"  # All succeeded
                job_status = JobStatus.COMPLETED.value
            elif successful == 0:
                extraction_status = "failed"  # All failed
                job_status = JobStatus.FAILED.value
            else:
                extraction_status = "completed"  # Partial success - still completed
                job_status = JobStatus.COMPLETED.value

            # Update extraction status
            supabase.table("extractions").update({
                "status": extraction_status
            }).eq("id", extraction_id).execute()

            # Update job status (record wall-clock end for duration).
            supabase.table("jobs").update({
                "status": job_status,
                "progress": 100 if job_status == JobStatus.COMPLETED.value else 50,
                "completed_at": datetime.now(timezone.utc).isoformat(),
                "result_data": {
                    "total_documents": total_docs,
                    "successful_extractions": successful,
                    "failed_extractions": failed,
                    "failed_document_ids": failed_doc_ids,
                    "success_rate": f"{successful}/{total_docs}"
                }
            }).eq("id", job_id).execute()

            # Broadcast completion so frontend updates instantly (no poll cycle needed)
            broadcaster._broadcast_message({
                "type": "complete",
                "job_id": str(job_id),
                "status": extraction_status,
            })

            # Log activity and notify on success
            job_record = supabase.table("jobs").select("user_id, project_id").eq("id", job_id).execute()
            if job_record.data:
                _user_id = job_record.data[0]["user_id"]
                _project_id = job_record.data[0].get("project_id")
                sync_notify(user_id=_user_id, job_id=job_id, job_type="extraction", success=True)
                sync_log_activity(
                    user_id=_user_id,
                    action_type="extraction",
                    action="Extraction Completed",
                    description=f"Extraction completed: {successful}/{total_docs} documents",
                    project_id=_project_id,
                    metadata={"extraction_id": extraction_id, "successful": successful, "failed": failed},
                )

            return {
                "status": "success",
                "extraction_id": extraction_id,
                "total_documents": len(documents),
                "results_count": len(extraction_results)
            }
        else:
            # Extraction failed
            error_msg = result.get("error", "Unknown error")
            logger.error(f"Extraction failed for job {extraction_id}: {error_msg}")

            # Update extraction status to failed
            supabase.table("extractions").update({
                "status": "failed"
            }).eq("id", extraction_id).execute()

            # Update job status to failed
            supabase.table("jobs").update({
                "status": JobStatus.FAILED.value,
                "progress": 0,
                "completed_at": datetime.now(timezone.utc).isoformat(),
                "error_message": error_msg
            }).eq("id", job_id).execute()

            # Broadcast completion so frontend updates instantly
            broadcaster._broadcast_message({
                "type": "complete",
                "job_id": str(job_id),
                "status": "failed",
            })

            # Log activity and notify on failure
            job_record = supabase.table("jobs").select("user_id, project_id").eq("id", job_id).execute()
            if job_record.data:
                _user_id = job_record.data[0]["user_id"]
                _project_id = job_record.data[0].get("project_id")
                sync_notify(user_id=_user_id, job_id=job_id, job_type="extraction", success=False, error_message=error_msg)
                sync_log_activity(
                    user_id=_user_id,
                    action_type="extraction",
                    action="Extraction Failed",
                    description=f"Extraction failed: {error_msg}",
                    project_id=_project_id,
                    metadata={"extraction_id": extraction_id, "error": error_msg},
                    status="failed",
                )

            return {
                "status": "failed",
                "extraction_id": extraction_id,
                "error": error_msg
            }

    except Exception as e:
        error_msg = str(e)
        logger.error(f"Error in extraction task: {error_msg}")

        # Update extraction status to failed
        try:
            supabase.table("extractions").update({
                "status": "failed"
            }).eq("id", extraction_id).execute()

            # Update job status to failed
            supabase.table("jobs").update({
                "status": JobStatus.FAILED.value,
                "progress": 0,
                "completed_at": datetime.now(timezone.utc).isoformat(),
                "error_message": error_msg
            }).eq("id", job_id).execute()
            # Broadcast completion so frontend updates instantly
            try:
                broadcaster._broadcast_message({
                    "type": "complete",
                    "job_id": str(job_id),
                    "status": "failed",
                })
            except Exception:
                pass
            # Notify on exception
            job_record = supabase.table("jobs").select("user_id, project_id").eq("id", job_id).execute()
            if job_record.data:
                _user_id = job_record.data[0]["user_id"]
                _project_id = job_record.data[0].get("project_id")
                sync_notify(user_id=_user_id, job_id=job_id, job_type="extraction", success=False, error_message=error_msg)
                sync_log_activity(
                    user_id=_user_id,
                    action_type="extraction",
                    action="Extraction Failed",
                    description=f"Extraction failed: {error_msg}",
                    project_id=_project_id,
                    metadata={"extraction_id": extraction_id, "error": error_msg},
                    status="failed",
                )
        except Exception as db_error:
            logger.error(f"Failed to update database after error: {db_error}")

        # Re-raise the exception for Celery to handle
        raise


@celery_app.task(name="check_extraction_service_health")
def check_extraction_service_health():
    """
    Health check task for extraction service.

    Returns:
        Dictionary with extraction service status
    """
    return extraction_service.check_extraction_status()
