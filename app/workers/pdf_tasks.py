"""
Celery tasks for PDF processing.
"""

import logging
from uuid import UUID
from supabase import create_client

from app.workers.celery_app import celery_app
from app.config import settings
from app.services.pdf_processing_service import pdf_processing_service
from app.models.enums import DocumentStatus, JobStatus

logger = logging.getLogger(__name__)

# Initialize Supabase client
supabase = create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_KEY)


@celery_app.task(bind=True, name="process_pdf_document")
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
        pdf_path = document["s3_pdf_path"]

        logger.info(f"Processing PDF at: {pdf_path}")

        # Update document status
        supabase.table("documents").update({
            "processing_status": DocumentStatus.PROCESSING.value
        }).eq("id", document_id).execute()

        # Update job progress
        supabase.table("jobs").update({
            "progress": 30
        }).eq("id", job_id).execute()

        # Process PDF to markdown
        result = pdf_processing_service.process_pdf_to_markdown(pdf_path)

        if result["success"]:
            logger.info(f"PDF processing successful for {document_id}")

            # Update document with markdown path
            update_data = {
                "processing_status": DocumentStatus.COMPLETED.value,
                "s3_markdown_path": result["markdown_path"],
                "processing_error": None
            }
            supabase.table("documents").update(update_data).eq("id", document_id).execute()

            # Update job status to completed
            supabase.table("jobs").update({
                "status": JobStatus.COMPLETED.value,
                "progress": 100,
                "result_data": {
                    "markdown_path": result["markdown_path"],
                    "metadata": result["metadata"]
                }
            }).eq("id", job_id).execute()

            return {
                "status": "success",
                "document_id": document_id,
                "markdown_path": result["markdown_path"],
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
        except Exception as db_error:
            logger.error(f"Failed to update database after error: {db_error}")

        # Re-raise the exception for Celery to handle
        raise


@celery_app.task(name="check_pdf_processor_health")
def check_pdf_processor_health():
    """
    Health check task for PDF processor.

    Returns:
        Dictionary with processor status
    """
    return pdf_processing_service.check_processor_status()
