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
from app.services.doi_service import extract_doi
from app.models.enums import DocumentStatus, JobStatus, BlocksStatus
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
            # Carry any json-call error the service reported; may be overwritten
            # by an upload error below. Drives the per-step blocks_status.
            blocks_error = result.get("blocks_error")
            if result.get("blocks_json"):
                try:
                    blocks_s3_key = storage_service.upload_blocks(
                        result["blocks_json"], project_id, content_hash
                    )
                except Exception as blocks_err:
                    blocks_error = f"blocks upload failed: {blocks_err}"
                    logger.warning(
                        f"Failed to upload blocks sidecar for {document_id}; "
                        f"continuing with markdown only: {blocks_err}"
                    )
            blocks_status = (
                BlocksStatus.COMPLETED.value if blocks_s3_key else BlocksStatus.FAILED.value
            )

            # Store the figures Datalab extracted from the PDF. The markdown we
            # just uploaded references them by bare filename, so without this
            # every `![](<hash>_img.jpg)` in it is a dangling link. Best-effort:
            # extraction is text-only and never reads these, so a failure here
            # must not fail an otherwise good parse.
            image_keys = []
            try:
                image_keys = storage_service.upload_images(
                    result.get("images") or {}, project_id, content_hash
                )
            except Exception as img_err:
                logger.warning(
                    f"Failed to store images for {document_id}; "
                    f"continuing without them: {img_err}"
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
                if image_keys:
                    storage_service.delete_images(project_id, content_hash)
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
                "blocks_status": blocks_status,
                "blocks_error": None if blocks_s3_key else blocks_error,
                "image_count": len(image_keys),
            }

            # Best-effort DOI/title extraction (doi_service.extract_doi) — uses
            # the PDF + blocks JSON we already have, so this adds no extra
            # Datalab calls. Wrapped defensively so a DOI miss (or any bug in
            # the cascade) never fails the parse; leaving doi/doi_source unset
            # on error keeps the doc a backfill candidate instead of wrongly
            # recording "none".
            #
            # PubMed imports (source_type == "pubmed") already carry an
            # authoritative DOI/title from PubMed's own metadata — that DOI is
            # how we found this PDF via Unpaywall in the first place, so the
            # PDF-text-scrape cascade below must never clobber it (it would
            # otherwise unconditionally overwrite doi/doi_source, including to
            # None/"none" when the scrape finds nothing). Manual uploads are
            # unaffected.
            # PubMed/EndNote/RIS imports carry an authoritative DOI/title from
            # their own source metadata — the PDF-text-scrape cascade must never
            # clobber it (variable name kept for the branches below).
            is_pubmed_import = document.get("source_type") in ("pubmed", "endnote", "ris")
            try:
                doi_result = extract_doi(
                    pdf_path=local_pdf,
                    markdown=result["markdown_content"],
                    blocks_json=result.get("blocks_json"),
                )
                if not is_pubmed_import:
                    update_data["doi"] = doi_result.doi
                    update_data["doi_source"] = doi_result.source
                    if doi_result.title:
                        update_data["title"] = doi_result.title
                # Study identity ("Raslan 2021") is filled for EVERY source,
                # import or not — unlike doi/title there is nothing to clobber:
                # only write what we found, and only into an empty column, so an
                # importer's own author/year always wins over a scrape.
                if doi_result.first_author and not document.get("first_author"):
                    update_data["first_author"] = doi_result.first_author
                if doi_result.year and not document.get("pub_year"):
                    update_data["pub_year"] = doi_result.year
                if is_pubmed_import and not document.get("doi") and doi_result.doi:
                    # PubMed had no DOI on record (shouldn't happen via the
                    # Unpaywall-PDF path, but keep this safe) — a scraped one
                    # beats none. Never touch title for a PubMed import.
                    update_data["doi"] = doi_result.doi
                    update_data["doi_source"] = doi_result.source
            except Exception as doi_err:
                logger.warning(
                    f"DOI extraction failed for {document_id}; continuing without it: {doi_err}"
                )

            # Last resort, and ONLY when the free paths came up empty: read the
            # paper's own first page for author + year. Older trials and scanned
            # reprints carry no DOI anywhere, and without this they show a
            # filename forever. One small model call, on the miss path only.
            if not (update_data.get("first_author") or document.get("first_author")):
                try:
                    from app.services.study_identity_service import identify_study

                    llm_author, llm_year = identify_study(result["markdown_content"])
                    if llm_author:
                        update_data["first_author"] = llm_author
                        if llm_year and not document.get("pub_year"):
                            update_data["pub_year"] = llm_year
                        logger.info(
                            f"[study-identity] {document_id}: {llm_author} {llm_year or ''}".strip()
                        )
                except Exception as ident_err:
                    logger.warning(
                        f"Study identity read failed for {document_id}; continuing: {ident_err}"
                    )

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


@celery_app.task(
    bind=True,
    name="backfill_pdf_blocks",
    max_retries=2,
    default_retry_delay=10,
    autoretry_for=(IOError, OSError, ConnectionError),
    retry_backoff=True,
    retry_jitter=True,
)
def backfill_pdf_blocks(self, document_id: str, job_id: str):
    """Re-fetch ONLY the Datalab json/bbox sidecar (call 2) for a document whose
    markdown is already processed but whose blocks call failed or never ran.

    Does not invoke the markdown call (so it is never re-billed) and never
    touches processing_status / s3_markdown_path.
    """
    tmp_dir = tempfile.mkdtemp(prefix=f"evistream_blocks_{job_id}_")
    try:
        supabase.table("jobs").update({
            "status": JobStatus.PROCESSING.value,
            "progress": 10
        }).eq("id", job_id).execute()

        doc_result = supabase.table("documents").select("*").eq("id", document_id).execute()
        if not doc_result.data:
            raise Exception(f"Document {document_id} not found")

        document = doc_result.data[0]
        s3_key = document.get("s3_pdf_path")
        content_hash = document.get("content_hash", "")
        project_id = document["project_id"]
        if not s3_key:
            raise Exception("Document has no stored PDF to backfill blocks from")

        local_pdf = f"{tmp_dir}/source.pdf"
        storage_service.download_to_temp(s3_key, local_pdf)
        supabase.table("jobs").update({"progress": 40}).eq("id", job_id).execute()

        # Call 2 only — markdown is not re-run.
        result = pdf_processing_service.fetch_blocks_only(local_pdf)

        if not result["success"]:
            error_msg = result.get("error", "Unknown error")
            logger.warning(f"Blocks backfill failed for {document_id}: {error_msg}")
            supabase.table("documents").update({
                "blocks_status": BlocksStatus.FAILED.value,
                "blocks_error": error_msg,
            }).eq("id", document_id).execute()
            supabase.table("jobs").update({
                "status": JobStatus.FAILED.value, "progress": 0, "error_message": error_msg
            }).eq("id", job_id).execute()
            return {"status": "failed", "document_id": document_id, "error": error_msg}

        blocks_s3_key = storage_service.upload_blocks(
            result["blocks_json"], project_id, content_hash
        )

        # The json call carries the extracted figures too, so a blocks backfill
        # doubles as an image backfill at no extra Datalab cost. Best-effort for
        # the same reason as in process_pdf_document.
        image_keys = []
        try:
            image_keys = storage_service.upload_images(
                result.get("images") or {}, project_id, content_hash
            )
        except Exception as img_err:
            logger.warning(
                f"Failed to store images during blocks backfill for {document_id}: {img_err}"
            )

        # Guard: document may have been deleted while the task ran.
        still_exists = supabase.table("documents").select("id").eq("id", document_id).execute()
        if not still_exists.data:
            logger.warning(f"Document {document_id} deleted during blocks backfill — cleaning up")
            storage_service.delete_object(blocks_s3_key)
            return {"status": "aborted", "document_id": document_id, "reason": "document deleted"}

        # Only fill positional metadata that is currently missing; never overwrite
        # values the original markdown parse already recorded.
        update_data = {
            "s3_blocks_path": blocks_s3_key,
            "blocks_status": BlocksStatus.COMPLETED.value,
            "blocks_error": None,
        }
        if image_keys:
            update_data["image_count"] = len(image_keys)
        if document.get("datalab_checkpoint_id") is None and result.get("checkpoint_id") is not None:
            update_data["datalab_checkpoint_id"] = result.get("checkpoint_id")
        if document.get("datalab_request_id") is None and result.get("request_id") is not None:
            update_data["datalab_request_id"] = result.get("request_id")
        if document.get("parse_quality_score") is None and result.get("parse_quality_score") is not None:
            update_data["parse_quality_score"] = result.get("parse_quality_score")
        if not document.get("page_count") and result.get("page_count"):
            update_data["page_count"] = result.get("page_count")

        supabase.table("documents").update(update_data).eq("id", document_id).execute()
        supabase.table("jobs").update({
            "status": JobStatus.COMPLETED.value,
            "progress": 100,
            "result_data": {"blocks_s3_key": blocks_s3_key}
        }).eq("id", job_id).execute()

        logger.info(f"Blocks backfill successful for {document_id}")
        return {"status": "success", "document_id": document_id, "blocks_s3_key": blocks_s3_key}

    except Exception as e:
        error_msg = str(e)
        logger.error(f"Error in blocks backfill task: {error_msg}")
        try:
            supabase.table("documents").update({
                "blocks_status": BlocksStatus.FAILED.value,
                "blocks_error": error_msg,
            }).eq("id", document_id).execute()
            supabase.table("jobs").update({
                "status": JobStatus.FAILED.value, "progress": 0, "error_message": error_msg
            }).eq("id", job_id).execute()
        except Exception as db_error:
            logger.error(f"Failed to update database after backfill error: {db_error}")
        raise
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


@celery_app.task(
    bind=True,
    name="backfill_pdf_doi",
    max_retries=2,
    default_retry_delay=10,
    autoretry_for=(IOError, OSError, ConnectionError),
    retry_backoff=True,
    retry_jitter=True,
)
def backfill_pdf_doi(self, document_id: str, job_id: str):
    """Best-effort DOI/title extraction for a document whose markdown is
    already processed but whose DOI was never attempted (doi_source IS NULL
    — legacy docs that pre-date this column).

    Reuses whatever we already have in S3 (PDF, markdown, blocks JSON) and
    issues NO Datalab calls — never re-billed. Only Crossref lookups (if the
    cheaper metadata/text steps come up empty) touch the network.
    """
    tmp_dir = tempfile.mkdtemp(prefix=f"evistream_doi_{job_id}_")
    try:
        supabase.table("jobs").update({
            "status": JobStatus.PROCESSING.value,
            "progress": 10
        }).eq("id", job_id).execute()

        doc_result = supabase.table("documents").select("*").eq("id", document_id).execute()
        if not doc_result.data:
            raise Exception(f"Document {document_id} not found")

        document = doc_result.data[0]
        s3_key = document.get("s3_pdf_path")
        if not s3_key:
            raise Exception("Document has no stored PDF to extract a DOI from")

        local_pdf = f"{tmp_dir}/source.pdf"
        storage_service.download_to_temp(s3_key, local_pdf)
        supabase.table("jobs").update({"progress": 30}).eq("id", job_id).execute()

        # Reuse the already-processed markdown (title fallback + page-1 text
        # fallback) — no re-parsing, no Datalab call.
        markdown_content = None
        markdown_key = document.get("s3_markdown_path")
        if markdown_key:
            try:
                resp = storage_service.s3_client.get_object(Bucket=settings.S3_BUCKET, Key=markdown_key)
                markdown_content = resp["Body"].read().decode("utf-8")
            except Exception as e:
                logger.warning(f"[doi_backfill] could not read markdown for {document_id}: {e}")

        # Reuse the already-fetched blocks sidecar for reliable page-1 text.
        blocks_json = None
        blocks_key = document.get("s3_blocks_path")
        if blocks_key:
            try:
                resp = storage_service.s3_client.get_object(Bucket=settings.S3_BUCKET, Key=blocks_key)
                import json as _json
                blocks_json = _json.loads(resp["Body"].read().decode("utf-8"))
            except Exception as e:
                logger.warning(f"[doi_backfill] could not read blocks for {document_id}: {e}")

        supabase.table("jobs").update({"progress": 60}).eq("id", job_id).execute()

        doi_result = extract_doi(
            pdf_path=local_pdf,
            markdown=markdown_content,
            blocks_json=blocks_json,
        )

        # Guard: document may have been deleted while the task ran.
        still_exists = supabase.table("documents").select("id").eq("id", document_id).execute()
        if not still_exists.data:
            logger.warning(f"Document {document_id} deleted during DOI backfill — skipping update")
            return {"status": "aborted", "document_id": document_id, "reason": "document deleted"}

        update_data = {"doi": doi_result.doi, "doi_source": doi_result.source}
        if doi_result.title:
            update_data["title"] = doi_result.title
        # Same rule as the main parse path: study identity is additive, and a
        # value already on the row (an importer's, or a human's) is never
        # overwritten by a scrape.
        if doi_result.first_author:
            update_data["first_author"] = doi_result.first_author
        if doi_result.year:
            update_data["pub_year"] = doi_result.year
        supabase.table("documents").update(update_data).eq("id", document_id).execute()

        supabase.table("jobs").update({
            "status": JobStatus.COMPLETED.value,
            "progress": 100,
            "result_data": {"doi": doi_result.doi, "doi_source": doi_result.source},
        }).eq("id", job_id).execute()

        logger.info(f"DOI backfill for {document_id}: doi={doi_result.doi!r} source={doi_result.source}")
        return {
            "status": "success",
            "document_id": document_id,
            "doi": doi_result.doi,
            "doi_source": doi_result.source,
        }

    except Exception as e:
        error_msg = str(e)
        logger.error(f"Error in DOI backfill task: {error_msg}")
        # Note: unlike blocks backfill, we do NOT mark doi_source='none' here —
        # this branch means the task itself blew up (e.g. S3 download failed),
        # not that the extraction cascade ran and found nothing. Leaving
        # doi_source untouched (NULL) keeps the doc a backfill candidate.
        try:
            supabase.table("jobs").update({
                "status": JobStatus.FAILED.value, "progress": 0, "error_message": error_msg
            }).eq("id", job_id).execute()
        except Exception as db_error:
            logger.error(f"Failed to update job after DOI backfill error: {db_error}")
        raise
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
