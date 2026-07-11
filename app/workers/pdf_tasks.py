"""
Celery tasks for PDF processing.
"""

import os
import shutil
import tempfile
import logging
from uuid import UUID
from supabase import create_client

from app.workers.celery_app import celery_app
from app.config import settings
from app.services.pdf_processing_service import pdf_processing_service
from app.services.storage_service import storage_service
from app.services.pdf_cleaner import clean_pdf_bytes
from app.models.enums import DocumentStatus, JobStatus
from app.workers.utils import sync_log_activity, sync_notify

logger = logging.getLogger(__name__)

# Initialize Supabase client
supabase = create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_KEY)


@celery_app.task(
    bind=True,
    name="process_pdf_document",
    max_retries=2,
    default_retry_delay=10,
    autoretry_for=(IOError, OSError, ConnectionError),
    retry_backoff=True,
    retry_jitter=True,
)
def process_pdf_document(self, document_id: str, job_id: str):
    """
    Background task to process a PDF document to markdown.

    Args:
        self: Celery task instance (for updating state)
        document_id: UUID of the document to process
        job_id: UUID of the job record

    Returns:
        Dictionary with processing results
    """
    tmp_dir = tempfile.mkdtemp(prefix=f"evistream_{job_id}_")
    try:
        logger.info(f"Starting PDF processing for document {document_id}")

        # Update job status to processing
        supabase.table("jobs").update({
            "status": JobStatus.PROCESSING.value,
            "progress": 10
        }).eq("id", job_id).execute()

        # Get document from database
        doc_result = supabase.table("documents")\
            .select("*")\
            .eq("id", document_id)\
            .execute()

        if not doc_result.data:
            raise Exception(f"Document {document_id} not found")

        document = doc_result.data[0]
        s3_key = document["s3_pdf_path"]
        content_hash = document.get("content_hash", "")
        project_id = document["project_id"]
        local_pdf = f"{tmp_dir}/source.pdf"
        storage_service.download_to_temp(s3_key, local_pdf)
        logger.info(f"Downloaded PDF to: {local_pdf}")

        # Update document status
        supabase.table("documents").update({
            "processing_status": DocumentStatus.PROCESSING.value
        }).eq("id", document_id).execute()

        # Update job progress
        supabase.table("jobs").update({
            "progress": 30
        }).eq("id", job_id).execute()

        # Process PDF to markdown
        result = pdf_processing_service.process_pdf_to_markdown(local_pdf)

        if result["success"]:
            logger.info(f"PDF processing successful for {document_id}")

            # Upload markdown to S3
            markdown_s3_key = storage_service.upload_markdown(
                result["markdown_content"], project_id, content_hash
            )

            # Upload block-level JSON sidecar (per-block bbox + page_id) if available.
            # Datalab's second /convert call may fail or be unavailable — degrade gracefully.
            blocks_s3_key = None
            if result.get("blocks_json"):
                try:
                    blocks_s3_key = storage_service.upload_blocks(
                        result["blocks_json"], project_id, content_hash
                    )
                except Exception as blocks_err:
                    logger.warning(
                        f"Failed to upload blocks sidecar for {document_id}; "
                        f"continuing with markdown only: {blocks_err}"
                    )

            # Check document still exists (may have been deleted while task was running)
            still_exists = supabase.table("documents")\
                .select("id")\
                .eq("id", document_id)\
                .execute()

            if not still_exists.data:
                logger.warning(f"Document {document_id} was deleted during processing — cleaning up orphaned markdown")
                storage_service.delete_object(markdown_s3_key)
                if blocks_s3_key:
                    storage_service.delete_object(blocks_s3_key)
                return {"status": "aborted", "document_id": document_id, "reason": "document deleted"}

            # Update document with markdown path + new Datalab positional fields
            update_data = {
                "processing_status": DocumentStatus.COMPLETED.value,
                "s3_markdown_path": markdown_s3_key,
                "s3_blocks_path": blocks_s3_key,
                "parse_quality_score": result.get("parse_quality_score"),
                "datalab_checkpoint_id": result.get("checkpoint_id"),
                "datalab_request_id": result.get("request_id"),
                "page_count": result.get("page_count"),
                "processing_error": None,
            }
            supabase.table("documents").update(update_data).eq("id", document_id).execute()

            # Update job status to completed
            supabase.table("jobs").update({
                "status": JobStatus.COMPLETED.value,
                "progress": 100,
                "result_data": {
                    "markdown_s3_key": markdown_s3_key,
                    "metadata": result["metadata"]
                }
            }).eq("id", job_id).execute()

            # Notify and log activity on success
            job_record = supabase.table("jobs").select("user_id, project_id").eq("id", job_id).execute()
            if job_record.data:
                _user_id = job_record.data[0]["user_id"]
                _project_id = job_record.data[0].get("project_id")
                sync_notify(user_id=_user_id, job_id=job_id, job_type="pdf_processing", success=True)
                sync_log_activity(
                    user_id=_user_id,
                    action_type="upload",
                    action="Document Processed",
                    description=f"Document processed successfully: {document.get('filename', document_id)}",
                    project_id=_project_id,
                    metadata={"document_id": document_id, "filename": document.get("filename")},
                )

            # Fire-and-forget the annotation-stripping clean step so the
            # viewer can render an "author-markup-free" PDF. The original is
            # still preserved in s3_pdf_path; clean version lands at
            # s3_clean_pdf_path. If this task fails the viewer falls back to
            # the original, no user-visible regression.
            try:
                clean_pdf_document.delay(document_id)
            except Exception as enq_err:
                logger.warning(
                    f"Failed to enqueue clean_pdf_document for {document_id}: {enq_err}"
                )

            return {
                "status": "success",
                "document_id": document_id,
                "markdown_s3_key": markdown_s3_key,
                "metadata": result["metadata"]
            }
        else:
            # Processing failed
            error_msg = result.get("error", "Unknown error")
            logger.error(f"PDF processing failed for {document_id}: {error_msg}")

            # Update document status to failed
            supabase.table("documents").update({
                "processing_status": DocumentStatus.FAILED.value,
                "processing_error": error_msg
            }).eq("id", document_id).execute()

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
                sync_notify(user_id=_user_id, job_id=job_id, job_type="pdf_processing", success=False, error_message=error_msg)
                sync_log_activity(
                    user_id=_user_id,
                    action_type="upload",
                    action="Document Processing Failed",
                    description=f"Document processing failed: {error_msg}",
                    project_id=_project_id,
                    metadata={"document_id": document_id, "error": error_msg},
                    status="failed",
                )

            return {
                "status": "failed",
                "document_id": document_id,
                "error": error_msg
            }

    except Exception as e:
        error_msg = str(e)
        logger.error(f"Error in PDF processing task: {error_msg}")

        # Update document status to failed
        try:
            supabase.table("documents").update({
                "processing_status": DocumentStatus.FAILED.value,
                "processing_error": error_msg
            }).eq("id", document_id).execute()

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
                sync_notify(user_id=_user_id, job_id=job_id, job_type="pdf_processing", success=False, error_message=error_msg)
                sync_log_activity(
                    user_id=_user_id,
                    action_type="upload",
                    action="Document Processing Failed",
                    description=f"Document processing failed: {error_msg}",
                    project_id=_project_id,
                    metadata={"document_id": document_id, "error": error_msg},
                    status="failed",
                )
        except Exception as db_error:
            logger.error(f"Failed to update database after error: {db_error}")

        # Re-raise the exception for Celery to handle
        raise

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


@celery_app.task(name="check_pdf_processor_health")
def check_pdf_processor_health():
    """
    Health check task for PDF processor.

    Returns:
        Dictionary with processor status
    """
    return pdf_processing_service.check_processor_status()


@celery_app.task(
    name="clean_pdf_document",
    max_retries=1,
    default_retry_delay=30,
    autoretry_for=(IOError, OSError, ConnectionError),
)
def clean_pdf_document(document_id: str):
    """
    Strip author-added annotations (highlights, sticky notes, underlines, etc.)
    from a document's PDF and store the result under `s3_clean_pdf_path`.

    Runs after the parse step on new uploads and via lazy backfill from the
    `/documents/{id}/file` endpoint for legacy docs. Idempotent — exits early
    if the doc already has a clean path or no original PDF.
    """
    try:
        doc_result = (
            supabase.table("documents")
            .select("id, project_id, s3_pdf_path, s3_clean_pdf_path, content_hash, filename")
            .eq("id", document_id)
            .execute()
        )
        if not doc_result.data:
            logger.info(f"[clean_pdf] document {document_id} not found — skipping")
            return {"status": "skipped", "reason": "not_found"}

        doc = doc_result.data[0]
        if doc.get("s3_clean_pdf_path"):
            return {"status": "skipped", "reason": "already_clean"}

        s3_key = doc.get("s3_pdf_path")
        if not s3_key:
            return {"status": "skipped", "reason": "no_pdf"}

        # Download original from S3
        import io as _io
        try:
            resp = storage_service.s3_client.get_object(
                Bucket=settings.S3_BUCKET, Key=s3_key
            )
            pdf_bytes = resp["Body"].read()
        except Exception as e:
            logger.warning(f"[clean_pdf] could not download s3://{settings.S3_BUCKET}/{s3_key}: {e}")
            return {"status": "failed", "reason": "download_failed"}

        # Strip annotations + (if needed) ghostscript flatten
        try:
            result = clean_pdf_bytes(pdf_bytes, use_ghostscript=True)
        except Exception as e:
            logger.exception(f"[clean_pdf] cleaner crashed for {document_id}: {e}")
            return {"status": "failed", "reason": "cleaner_error"}

        if not result.was_cleaned:
            # No annotations found and ghostscript fallback didn't change the file.
            # Mark the row so we don't keep retrying. We use the original key —
            # the endpoint will see s3_clean_pdf_path == s3_pdf_path and skip
            # the lazy-trigger path on subsequent reads.
            supabase.table("documents").update(
                {"s3_clean_pdf_path": s3_key}
            ).eq("id", document_id).execute()
            return {"status": "noop", "reason": "no_annotations"}

        # Upload cleaned PDF
        try:
            clean_s3_key = storage_service.upload_clean_pdf(
                result.pdf_bytes,
                doc["project_id"],
                doc["content_hash"] or document_id,
            )
        except Exception as e:
            logger.exception(f"[clean_pdf] upload failed for {document_id}: {e}")
            return {"status": "failed", "reason": "upload_failed"}

        # Persist
        supabase.table("documents").update(
            {"s3_clean_pdf_path": clean_s3_key}
        ).eq("id", document_id).execute()

        logger.info(
            f"[clean_pdf] {document_id}: removed {result.annotations_removed} annot(s)"
            f" via {'fitz+gs' if result.used_ghostscript else 'fitz'}; stored at {clean_s3_key}"
        )
        return {
            "status": "success",
            "document_id": document_id,
            "clean_s3_key": clean_s3_key,
            "annotations_removed": result.annotations_removed,
            "used_ghostscript": result.used_ghostscript,
        }

    except Exception as e:
        logger.exception(f"[clean_pdf] unexpected error for {document_id}: {e}")
        return {"status": "failed", "reason": f"unexpected: {e}"}
