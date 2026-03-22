"""
Celery tasks for extraction jobs.
"""

import logging
import json
import tempfile
import asyncio
import os
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
    max_documents: Optional[int] = None
):
    """
    Background task to run extraction on documents.

    Args:
        self: Celery task instance (for updating state)
        extraction_id: UUID of the extraction job
        job_id: UUID of the job record
        document_ids: Optional list of specific document IDs to extract
        max_documents: Optional limit on number of documents to process

    Returns:
        Dictionary with extraction results
    """
    try:
        logger.info(f"Starting extraction job {extraction_id}")

        from app.workers.log_broadcaster import CeleryLogBroadcaster
        broadcaster = CeleryLogBroadcaster(str(job_id))

        # Update job status to processing
        supabase.table("jobs").update({
            "status": JobStatus.PROCESSING.value,
            "progress": 10
        }).eq("id", job_id).execute()

        # Get extraction configuration from database
        extraction_result = supabase.table("extractions")\
            .select("*, forms(schema_name, task_dir)")\
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

        logger.info(f"Using schema: {schema_name}")

        # Note: results are upserted below on (extraction_id, document_id) to ensure idempotency

        # Get documents for this project
        project_id = extraction.get("project_id")
        documents_query = supabase.table("documents")\
            .select("id, s3_markdown_path, processing_status")\
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

        # Determine if single or batch extraction
        if len(documents) == 1:
            # Single document extraction — download from S3 to temp file
            doc = documents[0]
            s3_key = doc["s3_markdown_path"]

            logger.info(f"Downloading markdown from S3: {s3_key}")
            tmp_dir = tempfile.mkdtemp(prefix="evistream_extraction_")
            try:
                response = storage_service.s3_client.get_object(
                    Bucket=settings.S3_BUCKET,
                    Key=s3_key
                )
                local_path = os.path.join(tmp_dir, Path(s3_key).name)
                with open(local_path, "wb") as f:
                    f.write(response["Body"].read())

                result = extraction_service.run_extraction(
                    markdown_path=local_path,
                    schema_name=schema_name,
                    document_ids=[doc["id"]],
                    ground_truth=None,
                    max_documents=None
                )
            finally:
                import shutil
                shutil.rmtree(tmp_dir, ignore_errors=True)

        else:
            # Batch extraction - download markdowns from S3 to temp files, then process
            s3_docs = {doc["s3_markdown_path"]: doc["id"] for doc in documents if doc.get("s3_markdown_path")}

            if not s3_docs:
                raise Exception("No markdown files found for selected documents")

            logger.info(f"Running batch extraction on {len(s3_docs)} markdown files")

            tmp_dir = tempfile.mkdtemp(prefix="evistream_extraction_")
            valid_path_to_doc_id = {}
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
                    except Exception as e:
                        logger.warning(f"Could not download {s3_key} from S3: {e}, skipping")

                missing = len(s3_docs) - len(valid_path_to_doc_id)
                if missing:
                    logger.warning(f"{missing} markdown file(s) could not be downloaded, skipping")

                total_papers = len(valid_path_to_doc_id)
                completed_papers = 0
                failed_doc_ids: list = []
                paper_results: dict = {}  # doc_id → normalized extracted_data, inserted after asyncio.run() returns

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
                )
            finally:
                # Clean up temp files
                import shutil
                shutil.rmtree(tmp_dir, ignore_errors=True)

        # Update job progress
        supabase.table("jobs").update({
            "progress": 90
        }).eq("id", job_id).execute()

        if result.get("success"):
            logger.info(f"Extraction successful for job {extraction_id}")

            # Save extraction results
            extraction_results = result.get("results", [])

            # Build a lookup from markdown path to document ID
            doc_path_to_id = {d["s3_markdown_path"]: d["id"] for d in documents}

            # Store results in extraction_results table
            stored_count = 0
            if len(documents) == 1:
                # Single-document: save results now (no checkpoint callback was used)
                for extraction_result_data in extraction_results:
                    # Match result to document - now we tag document_id directly in batch processing
                    if isinstance(extraction_result_data, dict):
                        # First check if we tagged document_id directly (new batch approach)
                        doc_id = extraction_result_data.pop("document_id", None)

                        # Fallback: try to match by source_file path (old approach)
                        if not doc_id:
                            source_file = extraction_result_data.pop("source_file", None)
                            doc_id = doc_path_to_id.get(source_file)
                        else:
                            # Remove source_file if present since we already have doc_id
                            extraction_result_data.pop("source_file", None)

                        # Normalize: always extract from "results" key if present
                        extracted = extraction_result_data.get("results", extraction_result_data)
                        # If extracted is a list with one item, unwrap it
                        if isinstance(extracted, list) and len(extracted) == 1:
                            extracted = extracted[0]
                    else:
                        doc_id = None
                        extracted = {"data": extraction_result_data}

                    if not doc_id and len(documents) == 1:
                        # Single-document extraction: safe to use the only document
                        doc_id = documents[0]["id"]

                    if not doc_id:
                        logger.warning(f"Could not match extraction result to a document (source_file={source_file}), skipping")
                        continue

                    result_record = {
                        "extraction_id": extraction_id,
                        "job_id": str(job_id),
                        "project_id": extraction.get("project_id"),
                        "form_id": extraction.get("form_id"),
                        "document_id": doc_id,
                        "extracted_data": extracted if isinstance(extracted, dict) else {"data": extracted},
                        "extraction_type": "ai",
                    }
                    supabase.table("extraction_results").upsert(
                        result_record,
                        on_conflict="extraction_id,document_id"
                    ).execute()
                    stored_count += 1
            else:
                # Batch: results collected in-memory — single batch INSERT in sync context
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
                        }
                        for doc_id, extracted_data in paper_results.items()
                    ]
                    try:
                        supabase.table("extraction_results").upsert(
                            records,
                            on_conflict="extraction_id,document_id"
                        ).execute()
                    except Exception as e:
                        logger.error(f"Batch UPSERT failed: {e}")
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

            # Update job status
            supabase.table("jobs").update({
                "status": job_status,
                "progress": 100 if job_status == JobStatus.COMPLETED.value else 50,
                "result_data": {
                    "total_documents": total_docs,
                    "successful_extractions": successful,
                    "failed_extractions": failed,
                    "failed_document_ids": failed_doc_ids,
                    "success_rate": f"{successful}/{total_docs}"
                }
            }).eq("id", job_id).execute()

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
                "error_message": error_msg
            }).eq("id", job_id).execute()

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
                "error_message": error_msg
            }).eq("id", job_id).execute()
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
