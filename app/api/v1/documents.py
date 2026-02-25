"""
Document management endpoints - File upload and CRUD operations.
"""

from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File, Form, Request
from fastapi.responses import FileResponse, PlainTextResponse
from supabase import create_client
from uuid import UUID
from typing import List, Optional

from app.dependencies import get_current_user, get_optional_user
from app.config import settings
from app.models.schemas import DocumentUploadResponse, DocumentResponse
from app.services.storage_service import storage_service


router = APIRouter()

# Get limiter from app state (set in main.py)
def get_limiter():
    from app.main import limiter
    return limiter

# Initialize Supabase client
supabase = create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)

# File validation constants
MAX_FILE_SIZE = 100 * 1024 * 1024  # 100 MB
ALLOWED_EXTENSIONS = {".pdf"}


def validate_pdf_file(file: UploadFile) -> None:
    """Validate uploaded file is PDF and within size limits."""
    # Check file extension
    if not file.filename or not any(file.filename.lower().endswith(ext) for ext in ALLOWED_EXTENSIONS):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid file type. Only PDF files are allowed."
        )

    # Note: File size validation happens during upload
    # FastAPI will raise 413 if file exceeds configured limit


@router.post("/upload", response_model=DocumentUploadResponse, status_code=status.HTTP_201_CREATED)
async def upload_document(
    request: Request,
    project_id: UUID = Form(...),
    file: UploadFile = File(...),
    user_id: Optional[UUID] = Depends(get_optional_user)
):
    """
    Upload a PDF document to a project.

    - **project_id**: Project to upload document to
    - **file**: PDF file (max 100 MB)

    The document will be stored and queued for processing (PDF → Markdown).
    """
    try:
        # Validate file
        validate_pdf_file(file)

        # Skip project ownership check if no user (dev mode)
        if user_id:
            # Verify project exists and belongs to user
            project_result = supabase.table("projects")\
                .select("id")\
                .eq("id", str(project_id))\
                .eq("user_id", str(user_id))\
                .execute()

            if not project_result.data:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Project not found"
                )

        # Read file content
        file_content = await file.read()

        # Check file size
        if len(file_content) > MAX_FILE_SIZE:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=f"File too large. Maximum size is {MAX_FILE_SIZE // (1024*1024)} MB"
            )

        # Save file to storage
        unique_filename, file_path = storage_service.save_pdf(file_content, file.filename)

        # Create document record in database
        document_data = {
            "project_id": str(project_id),
            "filename": file.filename,
            "unique_filename": unique_filename,
            "s3_pdf_path": file_path,  # For now, local path; will be S3 URL later
            "s3_markdown_path": None,
            "processing_status": "pending"
        }

        result = supabase.table("documents").insert(document_data).execute()

        if not result.data:
            # Clean up uploaded file if database insert fails
            storage_service.delete_pdf(file_path)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to create document record"
            )

        document = result.data[0]

        # Create background job for PDF processing
        from app.workers.pdf_tasks import process_pdf_document
        from app.models.enums import JobType, JobStatus

        # Create job record in database (use dev user if not authenticated)
        job_data = {
            "user_id": str(user_id) if user_id else "00000000-0000-0000-0000-000000000001",
            "project_id": str(project_id),
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
            # Job creation failed — clean up the document and abort
            supabase.table("documents").delete().eq("id", document["id"]).execute()
            storage_service.delete_pdf(file_path)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to create processing job. Please try uploading again."
            )
        else:
            job = job_result.data[0]
            job_id = UUID(job["id"])

            # Trigger background PDF processing task
            celery_task = process_pdf_document.delay(
                document_id=document["id"],
                job_id=str(job_id)
            )

            # Update job with Celery task ID
            supabase.table("jobs").update({
                "celery_task_id": celery_task.id
            }).eq("id", str(job_id)).execute()

        return DocumentUploadResponse(
            id=document["id"],
            filename=document["filename"],
            unique_filename=document["unique_filename"],
            project_id=UUID(document["project_id"]),
            job_id=job_id,
            status=document["processing_status"]
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error uploading document: {str(e)}"
        )


@router.get("", response_model=List[DocumentResponse])
async def list_documents(
    project_id: Optional[UUID] = None,
    user_id: Optional[UUID] = Depends(get_optional_user)
):
    """
    List documents.

    - **project_id** (optional): Filter by project

    If project_id is provided, only returns documents from that project.
    Otherwise, returns all documents from all user's projects.
    """
    try:
        if project_id:
            # In dev mode (no user), skip ownership check
            if user_id:
                # Verify project belongs to user
                project_result = supabase.table("projects")\
                    .select("id")\
                    .eq("id", str(project_id))\
                    .eq("user_id", str(user_id))\
                    .execute()

                if not project_result.data:
                    raise HTTPException(
                        status_code=status.HTTP_404_NOT_FOUND,
                        detail="Project not found"
                    )

            # Get documents for specific project
            result = supabase.table("documents")\
                .select("*")\
                .eq("project_id", str(project_id))\
                .order("created_at", desc=True)\
                .execute()
        else:
            # In dev mode, return all documents
            if not user_id:
                result = supabase.table("documents")\
                    .select("*")\
                    .order("created_at", desc=True)\
                    .execute()
            else:
                # Get all documents from user's projects
                # First get user's project IDs
                projects_result = supabase.table("projects")\
                    .select("id")\
                    .eq("user_id", str(user_id))\
                    .execute()

                project_ids = [p["id"] for p in (projects_result.data or [])]

                if not project_ids:
                    return []

                # Get documents from those projects
                result = supabase.table("documents")\
                    .select("*")\
                    .in_("project_id", project_ids)\
                    .order("created_at", desc=True)\
                    .execute()

        documents = result.data or []
        return [DocumentResponse(**doc) for doc in documents]

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error listing documents: {str(e)}"
        )


@router.get("/{document_id}", response_model=DocumentResponse)
async def get_document(
    document_id: UUID,
    user_id: Optional[UUID] = Depends(get_optional_user)
):
    """
    Get a specific document by ID.

    Returns 404 if document doesn't exist or doesn't belong to user's project.
    """
    try:
        # Get document
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

        # In dev mode, skip ownership check
        if user_id:
            # Verify document's project belongs to user
            project_result = supabase.table("projects")\
                .select("id")\
                .eq("id", document["project_id"])\
                .eq("user_id", str(user_id))\
                .execute()

            if not project_result.data:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Document not found"
                )

        return DocumentResponse(**document)

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error getting document: {str(e)}"
        )


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(
    document_id: UUID,
    user_id: Optional[UUID] = Depends(get_optional_user)
):
    """
    Delete a document.

    This will:
    - Delete the document record from database
    - Delete the PDF file from storage
    - Delete the markdown file (if processed)
    - CASCADE delete any extraction results

    Only documents from user's projects can be deleted.
    """
    try:
        # Get document
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

        # In dev mode, skip ownership check
        if user_id:
            # Verify document's project belongs to user
            project_result = supabase.table("projects")\
                .select("id")\
                .eq("id", document["project_id"])\
                .eq("user_id", str(user_id))\
                .execute()

            if not project_result.data:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Document not found"
                )

        # Delete files from storage
        if document.get("s3_pdf_path"):
            storage_service.delete_pdf(document["s3_pdf_path"])

        if document.get("s3_markdown_path"):
            storage_service.delete_markdown(document["s3_markdown_path"])

        # Delete document record from database
        supabase.table("documents")\
            .delete()\
            .eq("id", str(document_id))\
            .execute()

        return None  # 204 No Content

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error deleting document: {str(e)}"
        )


@router.get("/{document_id}/markdown")
async def get_document_markdown(
    document_id: UUID,
    user_id: Optional[UUID] = Depends(get_optional_user)
):
    """
    Get a document's processed markdown content.

    Returns the markdown text extracted from the PDF.
    """
    try:
        import os

        # Get document
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

        # In dev mode, skip ownership check
        if user_id:
            project_result = supabase.table("projects")\
                .select("id")\
                .eq("id", document["project_id"])\
                .eq("user_id", str(user_id))\
                .execute()

            if not project_result.data:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Document not found"
                )

        # Check if markdown has been generated
        markdown_path = document.get("s3_markdown_path")
        if not markdown_path:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Markdown not yet generated for this document"
            )

        if not os.path.exists(markdown_path):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Markdown file not found on server"
            )

        # Read and return markdown content
        with open(markdown_path, "r", encoding="utf-8") as f:
            content = f.read()

        return PlainTextResponse(content=content, media_type="text/markdown")

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error getting document markdown: {str(e)}"
        )


@router.get("/{document_id}/download")
async def download_document(
    document_id: UUID,
    user_id: Optional[UUID] = Depends(get_optional_user)
):
    """
    Download a document's PDF file.
    
    Returns the original uploaded PDF file.
    """
    try:
        # Get document
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

        # In dev mode, skip ownership check
        if user_id:
            # Verify document's project belongs to user
            project_result = supabase.table("projects")\
                .select("id")\
                .eq("id", document["project_id"])\
                .eq("user_id", str(user_id))\
                .execute()

            if not project_result.data:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Document not found"
                )

        # Get file path
        file_path = document.get("unique_filename")
        if not file_path:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="File not found"
            )

        # Construct full path (PDFs are in uploads/pdfs subdirectory)
        import os
        full_path = os.path.join(settings.UPLOAD_DIR, "pdfs", file_path)

        if not os.path.exists(full_path):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="File not found on server"
            )

        # Return file for inline display (not download)
        from fastapi.responses import Response
        with open(full_path, "rb") as f:
            pdf_content = f.read()

        return Response(
            content=pdf_content,
            media_type="application/pdf",
            headers={
                "Content-Disposition": f'inline; filename="{document["filename"]}"'
            }
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error downloading document: {str(e)}"
        )
