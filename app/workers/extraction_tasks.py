"""
Celery tasks for extraction jobs.
"""

import logging
import json
from typing import List, Optional
from supabase import create_client

from app.workers.celery_app import celery_app
from app.config import settings
from app.services.extraction_service import extraction_service
from app.models.enums import JobStatus

logger = logging.getLogger(__name__)

# Initialize Supabase client
supabase = create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_KEY)


@celery_app.task(bind=True, name="run_extraction")
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
            # Single document extraction
            doc = documents[0]
            markdown_path = doc["s3_markdown_path"]

            logger.info(f"Running single extraction on: {markdown_path}")

            result = extraction_service.run_extraction(
                markdown_path=markdown_path,
                schema_name=schema_name,
                document_ids=[doc["id"]],
                ground_truth=None,
                max_documents=None
            )

        else:
            # Batch extraction - process each document's markdown file individually
            # since document IDs don't match filename patterns
            from pathlib import Path

            # Build a mapping of markdown_path -> document_id for result storage
            path_to_doc_id = {doc["s3_markdown_path"]: doc["id"] for doc in documents if doc.get("s3_markdown_path")}

            if not path_to_doc_id:
                raise Exception("No markdown files found for selected documents")

            logger.info(f"Running batch extraction on {len(path_to_doc_id)} markdown files")

            # Process each file individually
            all_results = []
            for markdown_path, doc_id in path_to_doc_id.items():
                if not Path(markdown_path).exists():
                    logger.warning(f"Markdown file not found: {markdown_path}")
                    continue

                result = extraction_service.run_extraction(
                    markdown_path=markdown_path,
                    schema_name=schema_name,
                    document_ids=None,
                    ground_truth=None,
                    max_documents=None
                )

                if result.get("success") and result.get("results"):
                    # Tag results with the correct document_id
                    for res in result["results"]:
                        res["document_id"] = doc_id
                        res["source_file"] = markdown_path
                    all_results.extend(result["results"])

            # Build aggregated result
            result = {
                "success": True,
                "total_documents": len(path_to_doc_id),
                "successful_extractions": len(all_results),
                "failed_extractions": len(path_to_doc_id) - len(all_results),
                "results": all_results
            }

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
                    "extracted_data": extracted if isinstance(extracted, dict) else {"data": extracted}
                }
                supabase.table("extraction_results").insert(result_record).execute()
                stored_count += 1

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
                    "success_rate": f"{successful}/{total_docs}"
                }
            }).eq("id", job_id).execute()

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
