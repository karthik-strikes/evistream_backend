"""
Document management endpoints - File upload and CRUD operations.
"""

import re
import logging
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status, Request, Query
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel
from supabase import create_client
from uuid import UUID
from typing import List, Optional

from app.dependencies import get_current_user
from app.config import settings
from app.models.schemas import DocumentUploadResponse, DocumentResponse, PresignedUploadResponse, DocumentLabelsUpdate
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
    """
    try:
        if project_id:
            # Verify project access and view permission
            await check_project_access(project_id, user_id, "can_view_docs")

            query = supabase.table("documents")\
                .select("*")\
                .eq("project_id", str(project_id))\
                .order("created_at", desc=True)
            if search:
                query = query.ilike("filename", f"%{search}%")
            result = query.range(offset, offset + limit - 1).execute()
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
            if search:
                query = query.ilike("filename", f"%{search}%")
            result = query.range(offset, offset + limit - 1).execute()

        documents = result.data or []

        # Apply search filter (filename or labels)
        if search:
            search_lower = search.lower()
            documents = [
                d for d in documents
                if search_lower in (d.get("filename") or "").lower()
                or any(search_lower in label.lower() for label in (d.get("labels") or []))
            ]

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
