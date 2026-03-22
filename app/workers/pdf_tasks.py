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

            # Check document still exists (may have been deleted while task was running)
            still_exists = supabase.table("documents")\
                .select("id")\
                .eq("id", document_id)\
                .execute()

            if not still_exists.data:
                logger.warning(f"Document {document_id} was deleted during processing — cleaning up orphaned markdown")
                storage_service.delete_object(markdown_s3_key)
                return {"status": "aborted", "document_id": document_id, "reason": "document deleted"}

            # Update document with markdown path
            update_data = {
                "processing_status": DocumentStatus.COMPLETED.value,
                "s3_markdown_path": markdown_s3_key,
                "processing_error": None
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
