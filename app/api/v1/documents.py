"""
Document management endpoints - File upload and CRUD operations.
"""

import re
import logging
from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, status, Request, Query, UploadFile
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel
from supabase import create_client
from uuid import UUID
from typing import List, Optional

from app.dependencies import get_current_user
from app.config import settings
from app.models.schemas import (
    DocumentUploadResponse, DocumentResponse, PresignedUploadResponse,
    DocumentLabelsUpdate, ApproveMetadataRequest,
)
from app.services.storage_service import storage_service
from app.services.project_access import check_project_access
from app.services.activity_service import log_activity

logger = logging.getLogger(__name__)

router = APIRouter()

# Initialize Supabase client
supabase = create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_KEY)

# File validation constants
MAX_FILE_SIZE = 100 * 1024 * 1024  # 100 MB
ALLOWED_EXTENSIONS = {".pdf"}
PDF_MAGIC_BYTES = b'%PDF-'


def validate_pdf_file(file) -> None:
    """Validate uploaded file is PDF and within size limits."""
    if not file.filename or not any(file.filename.lower().endswith(ext) for ext in ALLOWED_EXTENSIONS):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid file type. Only PDF files are allowed."
        )


def sanitize_filename(filename: str) -> str:
    """Sanitize filename for Content-Disposition header."""
    return re.sub(r'[^\w\-.]', '_', filename)


class UploadInitRequest(BaseModel):
    project_id: UUID
    filename: str
    content_hash: str
    file_size: int
    labels: Optional[List[str]] = None


@router.post("/upload", response_model=PresignedUploadResponse, status_code=status.HTTP_201_CREATED)
async def upload_document(
    body: UploadInitRequest,
    background_tasks: BackgroundTasks,
    user_id: UUID = Depends(get_current_user)
):
    """
    Initiate a document upload. Returns a presigned S3 URL for direct browser upload.
    """
    try:
        # Validate file extension
        if not body.filename.lower().endswith(".pdf"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid file type. Only PDF files are allowed."
            )

        # Verify project access and upload permission
        await check_project_access(body.project_id, user_id, "can_upload_docs")

        # Validate file size
        if body.file_size > MAX_FILE_SIZE:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=f"File too large. Maximum size is {MAX_FILE_SIZE // (1024 * 1024)} MB"
            )

        # Check for duplicate by content hash
        dup_result = supabase.table("documents")\
            .select("id,filename,processing_status,content_hash")\
            .eq("project_id", str(body.project_id))\
            .eq("content_hash", body.content_hash)\
            .execute()

        if dup_result.data:
            from fastapi.responses import JSONResponse
            return JSONResponse(
                status_code=status.HTTP_200_OK,
                content={"duplicate": True, "document": dup_result.data[0]}
            )

        # Create document record
        document_data = {
            "project_id": str(body.project_id),
            "filename": body.filename,
            "unique_filename": None,
            "content_hash": body.content_hash,
            "s3_pdf_path": None,
            "s3_markdown_path": None,
            "processing_status": "pending",
            "labels": body.labels or [],
        }
        result = supabase.table("documents").insert(document_data).execute()

        if not result.data:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to create document record"
            )

        document = result.data[0]

        # Create background job record
        from app.models.enums import JobType, JobStatus
        job_data = {
            "user_id": str(user_id),
            "project_id": str(body.project_id),
            "job_type": JobType.PDF_PROCESSING.value,
            "status": JobStatus.PENDING.value,
            "progress": 0,
            "input_data": {
                "document_id": document["id"],
                "filename": document["filename"]
            }
        }
        job_result = supabase.table("jobs").insert(job_data).execute()

        if not job_result.data:
            supabase.table("documents").delete().eq("id", document["id"]).execute()
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to create processing job. Please try uploading again."
            )

        # Generate presigned upload URL
        presigned = storage_service.generate_presigned_upload_url(
            str(body.project_id),
            body.content_hash,
            body.filename,
        )

        background_tasks.add_task(
            log_activity,
            user_id=user_id,
            action_type="upload",
            action="Document Uploaded",
            description=f"Uploaded document: {body.filename}",
            project_id=body.project_id,
            metadata={"filename": body.filename, "document_id": document["id"]},
        )

        return PresignedUploadResponse(
            document_id=document["id"],
            presigned_url=presigned["url"],
            presigned_fields=presigned.get("fields", {}),
            s3_key=presigned["s3_key"],
            confirm_url=f"/api/v1/documents/{document['id']}/confirm-upload",
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Error initiating document upload")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred"
        )


@router.post("/{document_id}/confirm-upload")
async def confirm_upload(
    document_id: UUID,
    user_id: UUID = Depends(get_current_user)
):
    """Called by frontend after successful direct S3 upload."""
    try:
        result = supabase.table("documents")\
            .select("*")\
            .eq("id", str(document_id))\
            .execute()

        if not result.data:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Document not found"
            )

        document = result.data[0]
        await check_project_access(UUID(document["project_id"]), user_id, "can_upload_docs")

        # Build expected S3 key
        s3_key = f"pdfs/{document['project_id']}/{document['content_hash']}.pdf"

        # Verify file actually landed in S3
        if not storage_service.object_exists(s3_key):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="File not found in storage. Upload may have failed."
            )

        # Validate PDF magic bytes to reject non-PDF files renamed to .pdf
        try:
            head_response = storage_service.s3_client.get_object(
                Bucket=settings.S3_BUCKET,
                Key=s3_key,
                Range="bytes=0-4"
            )
            header_bytes = head_response["Body"].read()
            if not header_bytes.startswith(PDF_MAGIC_BYTES):
                # Clean up the invalid file from S3
                storage_service.delete_object(s3_key)
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Uploaded file is not a valid PDF."
                )
        except HTTPException:
            raise
        except Exception as e:
            logger.warning(f"Could not validate PDF magic bytes: {e}")

        # Update document with confirmed S3 path
        supabase.table("documents").update({
            "s3_pdf_path": s3_key
        }).eq("id", str(document_id)).execute()

        # Find the pending job
        job_result = supabase.table("jobs")\
            .select("id")\
            .contains("input_data", {"document_id": str(document_id)})\
            .eq("status", "pending")\
            .execute()

        job_id = job_result.data[0]["id"] if job_result.data else None

        if job_id:
            from app.workers.pdf_tasks import process_pdf_document
            celery_task = process_pdf_document.delay(
                document_id=str(document_id),
                job_id=str(job_id)
            )
            supabase.table("jobs").update({
                "celery_task_id": celery_task.id
            }).eq("id", str(job_id)).execute()

        return {"status": "processing", "job_id": str(job_id) if job_id else None}

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Error confirming upload")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred"
        )


@router.post("/{document_id}/reprocess")
async def reprocess_document(
    document_id: UUID,
    user_id: UUID = Depends(get_current_user)
):
    """Retry processing for a document stuck in 'failed' status."""
    try:
        result = supabase.table("documents").select("*").eq("id", str(document_id)).execute()
        if not result.data:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Document not found"
            )

        document = result.data[0]
        await check_project_access(UUID(document["project_id"]), user_id, "can_upload_docs")

        if document["processing_status"] != "failed":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Only failed documents can be reprocessed"
            )

        if not document.get("s3_pdf_path"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Document has no stored file to reprocess"
            )

        supabase.table("documents").update({
            "processing_status": "pending",
            "processing_error": None,
        }).eq("id", str(document_id)).execute()

        from app.models.enums import JobType, JobStatus
        job_data = {
            "user_id": str(user_id),
            "project_id": document["project_id"],
            "job_type": JobType.PDF_PROCESSING.value,
            "status": JobStatus.PENDING.value,
            "progress": 0,
            "input_data": {
                "document_id": str(document_id),
                "filename": document["filename"]
            }
        }
        job_result = supabase.table("jobs").insert(job_data).execute()

        if not job_result.data:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to create processing job"
            )

        job_id = job_result.data[0]["id"]

        from app.workers.pdf_tasks import process_pdf_document
        celery_task = process_pdf_document.delay(
            document_id=str(document_id),
            job_id=str(job_id)
        )
        supabase.table("jobs").update({
            "celery_task_id": celery_task.id
        }).eq("id", job_id).execute()

        return {"status": "processing", "job_id": job_id}

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Error reprocessing document")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred"
        )


@router.patch("/approve-metadata")
async def approve_metadata_extraction(
    body: ApproveMetadataRequest,
    user_id: UUID = Depends(get_current_user)
):
    """Accept (or un-accept) thin-evidence documents for extraction.

    A `metadata_only` document — an abstract-only PubMed record, a
    registration-only trial — is held out of extraction until a reviewer
    accepts it, so a run never silently reads an abstract as if it were a full
    paper. Whether an abstract suffices depends on the form being run, which is
    why this is a human decision rather than a rule.

    Approving deliberately does NOT change processing_status: the document stays
    `metadata_only` so results, exports and eval can still tell the evidence was
    thin. Bulk by design — nobody will click through 200 rows.
    """
    try:
        ids = [str(d) for d in body.document_ids]
        result = supabase.table("documents")\
            .select("id, project_id, processing_status")\
            .in_("id", ids)\
            .execute()

        docs = result.data or []
        if not docs:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="No matching documents found"
            )

        # Documents may span projects; authorize each one that appears.
        for project_id in {d["project_id"] for d in docs}:
            await check_project_access(UUID(project_id), user_id, "can_upload_docs")

        eligible = [d["id"] for d in docs if d.get("processing_status") == "metadata_only"]
        if not eligible:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="None of these documents need accepting — they already have full text."
            )

        supabase.table("documents")\
            .update({"metadata_extraction_approved": body.approved})\
            .in_("id", eligible)\
            .execute()

        return {
            "approved": body.approved,
            "count": len(eligible),
            "document_ids": eligible,
            "skipped": len(docs) - len(eligible),
        }

    except HTTPException:
        raise
    except Exception:
        logger.exception("Error approving metadata-only documents")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred"
        )


@router.post("/{document_id}/attach-pdf")
async def attach_pdf(
    document_id: UUID,
    file: UploadFile = File(...),
    user_id: UUID = Depends(get_current_user)
):
    """
    Manual full-text fallback: attach a user-supplied PDF to a document that
    doesn't have one yet — today, exclusively PubMed imports where no free
    open-access copy was found automatically (see ImportedTrialDrawer's
    "Attach PDF" prompt). Runs the exact same Datalab/Celery pipeline a fresh
    upload does; mirrors /reprocess above, just uploading a fresh file
    instead of retrying an existing s3_pdf_path.
    """
    try:
        result = supabase.table("documents").select("*").eq("id", str(document_id)).execute()
        if not result.data:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Document not found"
            )

        document = result.data[0]
        await check_project_access(UUID(document["project_id"]), user_id, "can_upload_docs")

        validate_pdf_file(file)

        pdf_bytes = await file.read()
        if len(pdf_bytes) > MAX_FILE_SIZE:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=f"File too large. Maximum size is {MAX_FILE_SIZE // (1024 * 1024)} MB"
            )
        if not pdf_bytes.startswith(PDF_MAGIC_BYTES):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Uploaded file is not a valid PDF."
            )

        s3_key = storage_service.upload_pdf(pdf_bytes, document["project_id"], document["content_hash"])

        supabase.table("documents").update({
            "s3_pdf_path": s3_key,
            "processing_status": "pending",
            "processing_error": None,
        }).eq("id", str(document_id)).execute()

        from app.models.enums import JobType, JobStatus
        job_data = {
            "user_id": str(user_id),
            "project_id": document["project_id"],
            "job_type": JobType.PDF_PROCESSING.value,
            "status": JobStatus.PENDING.value,
            "progress": 0,
            "input_data": {
                "document_id": str(document_id),
                "filename": document["filename"]
            }
        }
        job_result = supabase.table("jobs").insert(job_data).execute()

        if not job_result.data:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to create processing job"
            )

        job_id = job_result.data[0]["id"]

        from app.workers.pdf_tasks import process_pdf_document
        celery_task = process_pdf_document.delay(
            document_id=str(document_id),
            job_id=str(job_id)
        )
        supabase.table("jobs").update({
            "celery_task_id": celery_task.id
        }).eq("id", job_id).execute()

        return {"status": "processing", "job_id": job_id}

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Error attaching PDF")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred"
        )


def _enqueue_blocks_backfill(document: dict, user_id: UUID) -> str:
    """Create a pdf_processing job (mode=blocks_backfill) and enqueue the
    backfill_pdf_blocks Celery task. Returns the job id."""
    from app.models.enums import JobType, JobStatus
    job_data = {
        "user_id": str(user_id),
        "project_id": document["project_id"],
        "job_type": JobType.PDF_PROCESSING.value,
        "status": JobStatus.PENDING.value,
        "progress": 0,
        "input_data": {
            "document_id": str(document["id"]),
            "filename": document.get("filename"),
            "mode": "blocks_backfill",
        },
    }
    job_result = supabase.table("jobs").insert(job_data).execute()
    if not job_result.data:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create backfill job"
        )
    job_id = job_result.data[0]["id"]

    from app.workers.pdf_tasks import backfill_pdf_blocks
    celery_task = backfill_pdf_blocks.delay(
        document_id=str(document["id"]),
        job_id=str(job_id),
    )
    supabase.table("jobs").update({
        "celery_task_id": celery_task.id
    }).eq("id", job_id).execute()
    return job_id


@router.post("/{document_id}/backfill-blocks")
async def backfill_document_blocks(
    document_id: UUID,
    user_id: UUID = Depends(get_current_user)
):
    """Re-fetch the Datalab blocks (json/bbox) sidecar for a document whose
    markdown is already processed but whose blocks call failed or never ran.

    Only the json/bbox call is issued — the markdown conversion is not re-run
    (and not re-billed).
    """
    try:
        result = supabase.table("documents").select("*").eq("id", str(document_id)).execute()
        if not result.data:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Document not found"
            )

        document = result.data[0]
        await check_project_access(UUID(document["project_id"]), user_id, "can_upload_docs")

        if document.get("processing_status") != "completed":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Blocks can only be backfilled once markdown processing is complete"
            )
        if document.get("blocks_status") == "completed":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Document already has its blocks sidecar"
            )
        if not document.get("s3_pdf_path"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Document has no stored file to backfill from"
            )

        job_id = _enqueue_blocks_backfill(document, user_id)
        return {"status": "processing", "job_id": job_id}

    except HTTPException:
        raise
    except Exception:
        logger.exception("Error backfilling document blocks")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred"
        )


class BatchBackfillRequest(BaseModel):
    project_id: UUID
    limit: int = 50


@router.post("/backfill-blocks/batch")
async def backfill_blocks_batch(
    body: BatchBackfillRequest,
    user_id: UUID = Depends(get_current_user)
):
    """Owner/admin-only: enqueue a blocks backfill for up to `limit` documents in a
    project whose markdown is complete but whose blocks sidecar is missing/failed.
    Bounded to keep Datalab spend explicit."""
    try:
        perms = await check_project_access(body.project_id, user_id, "can_upload_docs")
        if not (perms.get("is_owner") or perms.get("is_admin")):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only a project owner or admin can run a batch backfill"
            )

        limit = max(1, min(body.limit, 500))
        raw = supabase.table("documents")\
            .select("*")\
            .eq("project_id", str(body.project_id))\
            .eq("processing_status", "completed")\
            .neq("blocks_status", "completed")\
            .limit(limit)\
            .execute()
        raw_docs = raw.data or []
        docs = [d for d in raw_docs if d.get("s3_pdf_path")]

        enqueued = []
        for document in docs:
            try:
                job_id = _enqueue_blocks_backfill(document, user_id)
                enqueued.append({"document_id": document["id"], "job_id": job_id})
            except Exception as enq_err:
                logger.warning(f"Failed to enqueue blocks backfill for {document['id']}: {enq_err}")

        truncated = len(raw_docs) == limit
        if truncated:
            logger.info(
                f"Blocks batch backfill for project {body.project_id} hit the cap of "
                f"{limit}; more candidates may remain — re-run to continue."
            )

        return {
            "enqueued_count": len(enqueued),
            "enqueued": enqueued,
            "capped_at": limit,
            "possibly_more_remaining": truncated,
        }

    except HTTPException:
        raise
    except Exception:
        logger.exception("Error running batch blocks backfill")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred"
        )


def _enqueue_doi_backfill(document: dict, user_id: UUID) -> str:
    """Create a pdf_processing job (mode=doi_backfill) and enqueue the
    backfill_pdf_doi Celery task. Returns the job id."""
    from app.models.enums import JobType, JobStatus
    job_data = {
        "user_id": str(user_id),
        "project_id": document["project_id"],
        "job_type": JobType.PDF_PROCESSING.value,
        "status": JobStatus.PENDING.value,
        "progress": 0,
        "input_data": {
            "document_id": str(document["id"]),
            "filename": document.get("filename"),
            "mode": "doi_backfill",
        },
    }
    job_result = supabase.table("jobs").insert(job_data).execute()
    if not job_result.data:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create backfill job"
        )
    job_id = job_result.data[0]["id"]

    from app.workers.pdf_tasks import backfill_pdf_doi
    celery_task = backfill_pdf_doi.delay(
        document_id=str(document["id"]),
        job_id=str(job_id),
    )
    supabase.table("jobs").update({
        "celery_task_id": celery_task.id
    }).eq("id", job_id).execute()
    return job_id


@router.post("/{document_id}/backfill-doi")
async def backfill_document_doi(
    document_id: UUID,
    user_id: UUID = Depends(get_current_user)
):
    """Best-effort re-extraction of a document's DOI/title for docs whose
    DOI was never attempted (doi_source IS NULL — legacy docs that pre-date
    the DOI pipeline). Issues no Datalab calls; Crossref lookups only."""
    try:
        result = supabase.table("documents").select("*").eq("id", str(document_id)).execute()
        if not result.data:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Document not found"
            )

        document = result.data[0]
        await check_project_access(UUID(document["project_id"]), user_id, "can_upload_docs")

        if document.get("processing_status") != "completed":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="DOI can only be backfilled once markdown processing is complete"
            )
        if document.get("doi_source") is not None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="DOI extraction has already been attempted for this document"
            )
        if not document.get("s3_pdf_path"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Document has no stored file to backfill from"
            )

        job_id = _enqueue_doi_backfill(document, user_id)
        return {"status": "processing", "job_id": job_id}

    except HTTPException:
        raise
    except Exception:
        logger.exception("Error backfilling document DOI")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred"
        )


@router.post("/backfill-doi/batch")
async def backfill_doi_batch(
    body: BatchBackfillRequest,
    user_id: UUID = Depends(get_current_user)
):
    """Owner/admin-only: enqueue a DOI backfill for up to `limit` documents in
    a project whose markdown is complete but whose DOI extraction was never
    attempted. Bounded; issues no Datalab calls (Crossref lookups only)."""
    try:
        perms = await check_project_access(body.project_id, user_id, "can_upload_docs")
        if not (perms.get("is_owner") or perms.get("is_admin")):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only a project owner or admin can run a batch backfill"
            )

        limit = max(1, min(body.limit, 500))
        raw = supabase.table("documents")\
            .select("*")\
            .eq("project_id", str(body.project_id))\
            .eq("processing_status", "completed")\
            .is_("doi_source", "null")\
            .limit(limit)\
            .execute()
        raw_docs = raw.data or []
        docs = [d for d in raw_docs if d.get("s3_pdf_path")]

        enqueued = []
        for document in docs:
            try:
                job_id = _enqueue_doi_backfill(document, user_id)
                enqueued.append({"document_id": document["id"], "job_id": job_id})
            except Exception as enq_err:
                logger.warning(f"Failed to enqueue DOI backfill for {document['id']}: {enq_err}")

        truncated = len(raw_docs) == limit
        if truncated:
            logger.info(
                f"DOI batch backfill for project {body.project_id} hit the cap of "
                f"{limit}; more candidates may remain — re-run to continue."
            )

        return {
            "enqueued_count": len(enqueued),
            "enqueued": enqueued,
            "capped_at": limit,
            "possibly_more_remaining": truncated,
        }

    except HTTPException:
        raise
    except Exception:
        logger.exception("Error running batch DOI backfill")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred"
        )


@router.get("", response_model=List[DocumentResponse])
async def list_documents(
    project_id: Optional[UUID] = None,
    search: Optional[str] = None,
    limit: int = Query(default=50, le=500),
    offset: int = Query(default=0, ge=0),
    user_id: UUID = Depends(get_current_user)
):
    """
    List documents.

    - **project_id** (optional): Filter by project
    - **search** (optional): Matches filename OR any label (case-insensitive
      substring). Applied in Python, not as a DB-level filter — Supabase's
      query builder has no cheap substring-match over a JSON array column
      (labels), so pushing an `.ilike("filename", ...)` filter to the DB
      would exclude a label-only match before the label check ever runs.
      When searching, pagination is applied AFTER filtering (not before),
      so a match past the unfiltered set's first `limit` rows isn't missed.
    """
    try:
        if project_id:
            # Verify project access and view permission
            await check_project_access(project_id, user_id, "can_view_docs")

            query = supabase.table("documents")\
                .select("*")\
                .eq("project_id", str(project_id))\
                .order("created_at", desc=True)
            if not search:
                query = query.range(offset, offset + limit - 1)
            result = query.execute()
        else:
            # Get all documents from user's owned + member projects
            owned_result = supabase.table("projects")\
                .select("id")\
                .eq("user_id", str(user_id))\
                .execute()
            member_result = supabase.table("project_members")\
                .select("project_id")\
                .eq("user_id", str(user_id))\
                .eq("can_view_docs", True)\
                .execute()
            owned_ids = [p["id"] for p in (owned_result.data or [])]
            member_ids = [r["project_id"] for r in (member_result.data or [])]
            project_ids = list(set(owned_ids + member_ids))

            if not project_ids:
                return []

            query = supabase.table("documents")\
                .select("*")\
                .in_("project_id", project_ids)\
                .order("created_at", desc=True)
            if not search:
                query = query.range(offset, offset + limit - 1)
            result = query.execute()

        documents = result.data or []

        # Search filter (filename or labels), then paginate the FILTERED set.
        if search:
            search_lower = search.lower()
            documents = [
                d for d in documents
                if search_lower in (d.get("filename") or "").lower()
                or any(search_lower in label.lower() for label in (d.get("labels") or []))
            ]
            documents = documents[offset:offset + limit]

        return [DocumentResponse(**doc) for doc in documents]

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Error listing documents")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred"
        )


@router.get("/{document_id}", response_model=DocumentResponse)
async def get_document(
    document_id: UUID,
    user_id: UUID = Depends(get_current_user)
):
    """Get a specific document by ID."""
    try:
        result = supabase.table("documents")\
            .select("*")\
            .eq("id", str(document_id))\
            .execute()

        if not result.data:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Document not found"
            )

        document = result.data[0]

        # Verify project access and view permission
        await check_project_access(UUID(document["project_id"]), user_id, "can_view_docs")

        return DocumentResponse(**document)

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Error getting document")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred"
        )


@router.patch("/{document_id}/labels", response_model=DocumentResponse)
async def update_document_labels(
    document_id: UUID,
    body: DocumentLabelsUpdate,
    user_id: UUID = Depends(get_current_user)
):
    """Update labels for a document."""
    try:
        result = supabase.table("documents")\
            .select("*")\
            .eq("id", str(document_id))\
            .execute()

        if not result.data:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Document not found"
            )

        document = result.data[0]
        await check_project_access(UUID(document["project_id"]), user_id, "can_upload_docs")

        update_result = supabase.table("documents")\
            .update({"labels": body.labels})\
            .eq("id", str(document_id))\
            .execute()

        if not update_result.data:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to update labels"
            )

        return DocumentResponse(**update_result.data[0])

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Error updating document labels")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred"
        )


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(
    document_id: UUID,
    background_tasks: BackgroundTasks,
    user_id: UUID = Depends(get_current_user)
):
    """Delete a document."""
    try:
        result = supabase.table("documents")\
            .select("*")\
            .eq("id", str(document_id))\
            .execute()

        if not result.data:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Document not found"
            )

        document = result.data[0]

        # Verify project access and upload permission (upload implies delete)
        await check_project_access(UUID(document["project_id"]), user_id, "can_upload_docs")

        # Delete files from storage
        if document.get("s3_pdf_path"):
            storage_service.delete_object(document["s3_pdf_path"])

        if document.get("s3_markdown_path"):
            storage_service.delete_object(document["s3_markdown_path"])

        supabase.table("documents")\
            .delete()\
            .eq("id", str(document_id))\
            .execute()

        background_tasks.add_task(
            log_activity,
            user_id=user_id,
            action_type="upload",
            action="Document Deleted",
            description=f"Deleted document: {document.get('filename', str(document_id))}",
            project_id=UUID(document["project_id"]),
            metadata={"document_id": str(document_id), "filename": document.get("filename")},
        )

        return None

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Error deleting document")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred"
        )


@router.get("/{document_id}/markdown")
async def get_document_markdown(
    document_id: UUID,
    user_id: UUID = Depends(get_current_user)
):
    """Get a document's processed markdown content."""
    try:
        result = supabase.table("documents")\
            .select("*")\
            .eq("id", str(document_id))\
            .execute()

        if not result.data:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Document not found"
            )

        document = result.data[0]
        await check_project_access(UUID(document["project_id"]), user_id, "can_view_docs")

        markdown_key = document.get("s3_markdown_path")
        if not markdown_key:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Markdown not yet generated for this document"
            )

        try:
            response = storage_service.s3_client.get_object(
                Bucket=settings.S3_BUCKET,
                Key=markdown_key
            )
            content = response["Body"].read().decode("utf-8")
        except Exception:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Markdown file not found in storage"
            )

        return PlainTextResponse(content=content, media_type="text/markdown")

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Error getting document markdown")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred"
        )


@router.get("/{document_id}/file")
async def get_document_file(
    document_id: UUID,
    user_id: UUID = Depends(get_current_user)
):
    """Stream the raw PDF bytes through the backend.

    Same-origin response so pdfjs / react-pdf can fetch it via XHR without
    triggering S3 CORS (the bucket has no CORS policy). Used by the viewer
    inside the source-evidence drawer; bulk "download to disk" still uses
    /download which returns a presigned S3 URL.

    Prefers the *annotation-stripped* PDF at `s3_clean_pdf_path` so the
    viewer never shows highlights / sticky-notes added by previous readers
    that would visually compete with our own source-text overlay. Falls
    back to the original if the clean variant doesn't exist yet, and lazily
    enqueues `clean_pdf_document` so the next viewer load gets the clean
    version.
    """
    try:
        result = supabase.table("documents")\
            .select("*")\
            .eq("id", str(document_id))\
            .execute()

        if not result.data:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Document not found"
            )

        document = result.data[0]
        await check_project_access(UUID(document["project_id"]), user_id, "can_view_docs")

        clean_key = document.get("s3_clean_pdf_path")
        original_key = document.get("s3_pdf_path")
        s3_key = clean_key or original_key
        if not s3_key:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="PDF not available"
            )

        # Lazy backfill: if we have an original but no clean version, kick off
        # the cleaner task in the background. The user gets the original this
        # one time; the next viewer load (or any other user's load) hits clean.
        if not clean_key and original_key:
            try:
                from app.workers.pdf_tasks import clean_pdf_document
                clean_pdf_document.delay(str(document_id))
                logger.info(f"Lazily enqueued clean_pdf_document for {document_id}")
            except Exception as enq_err:
                logger.warning(f"Could not enqueue clean task for {document_id}: {enq_err}")

        try:
            s3_response = storage_service.s3_client.get_object(
                Bucket=settings.S3_BUCKET,
                Key=s3_key,
            )
            body_stream = s3_response["Body"]
        except Exception:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="PDF not found in storage"
            )

        def iter_chunks(chunk_size: int = 65536):
            try:
                while True:
                    chunk = body_stream.read(chunk_size)
                    if not chunk:
                        break
                    yield chunk
            finally:
                body_stream.close()

        from fastapi.responses import StreamingResponse
        import hashlib
        filename = (document.get("filename") or "document.pdf").replace('"', '')
        # ETag includes the S3 key so the cache invalidates the instant a doc
        # transitions from "original served" to "clean served" (different key,
        # different ETag). Without this, browsers happily serve the cached
        # original for up to max-age after the clean version becomes available.
        etag = hashlib.md5(s3_key.encode("utf-8")).hexdigest()[:16]
        return StreamingResponse(
            iter_chunks(),
            media_type="application/pdf",
            headers={
                "Content-Disposition": f'inline; filename="{filename}"',
                # Short max-age + must-revalidate so the browser re-asks the
                # server on the next view. ETag lets the server respond 304
                # when the variant hasn't changed.
                "Cache-Control": "private, max-age=300, must-revalidate",
                "ETag": f'"{etag}"',
            },
        )

    except HTTPException:
        raise
    except Exception:
        logger.exception("Error streaming document file")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred"
        )


@router.get("/{document_id}/blocks")
async def get_document_blocks(
    document_id: UUID,
    user_id: UUID = Depends(get_current_user)
):
    """Return the Datalab block-level JSON sidecar (per-block bbox/page/type).

    Used by the frontend PDF viewer to draw exact-coordinate highlights over
    the source PDF. Returns 404 with detail=`blocks_unavailable` for documents
    parsed before the blocks pipeline existed — the viewer should fall back
    to fuzzy-match highlighting in that case.
    """
    try:
        result = supabase.table("documents")\
            .select("*")\
            .eq("id", str(document_id))\
            .execute()

        if not result.data:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Document not found"
            )

        document = result.data[0]
        await check_project_access(UUID(document["project_id"]), user_id, "can_view_docs")

        blocks_key = document.get("s3_blocks_path")
        if not blocks_key:
            # Legacy doc that pre-dates the bbox pipeline. We return 200 with
            # an "unavailable" sentinel instead of 404 because the document
            # itself exists — only its bbox sidecar is missing. The 404 was
            # visually noisy in browser devtools (Chrome paints failed fetches
            # red regardless of how the frontend handles them); a 2xx with a
            # tiny JSON payload lets the frontend route to text-layer fallback
            # without any console error.
            from fastapi.responses import JSONResponse
            return JSONResponse(content={"unavailable": True, "reason": "blocks_unavailable"})

        try:
            response = storage_service.s3_client.get_object(
                Bucket=settings.S3_BUCKET,
                Key=blocks_key
            )
            content = response["Body"].read()
        except Exception:
            # Sidecar path is set but the object isn't there — still a soft
            # failure from the viewer's perspective; surface as "unavailable".
            from fastapi.responses import JSONResponse
            return JSONResponse(content={"unavailable": True, "reason": "sidecar_missing"})

        from fastapi.responses import Response
        return Response(content=content, media_type="application/json")

    except HTTPException:
        raise
    except Exception:
        logger.exception("Error getting document blocks")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred"
        )


@router.get("/{document_id}/download")
async def download_document(
    document_id: UUID,
    user_id: UUID = Depends(get_current_user)
):
    """Return a presigned S3 download URL for the document PDF."""
    try:
        result = supabase.table("documents")\
            .select("*")\
            .eq("id", str(document_id))\
            .execute()

        if not result.data:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Document not found"
            )

        document = result.data[0]
        await check_project_access(UUID(document["project_id"]), user_id, "can_view_docs")

        s3_key = document.get("s3_pdf_path")
        if not s3_key:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="File not found"
            )

        url = storage_service.generate_presigned_download_url(s3_key, document["filename"])
        return {"download_url": url, "expires_in": 3600}

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Error generating download URL")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred"
        )
