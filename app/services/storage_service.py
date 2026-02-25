"""
Storage service for handling file uploads.
Currently uses local storage, can be extended to S3.
"""

import os
import shutil
from pathlib import Path
from uuid import uuid4
from typing import Tuple, Optional

from app.config import settings


class StorageService:
    """Service for file storage operations."""

    def __init__(self):
        # Ensure upload directories exist
        self.pdf_dir = settings.PROJECT_ROOT / "storage" / "uploads" / "pdfs"
        self.markdown_dir = settings.PROJECT_ROOT / "storage" / "processed" / "extracted_pdfs"

        self.pdf_dir.mkdir(parents=True, exist_ok=True)
        self.markdown_dir.mkdir(parents=True, exist_ok=True)

    def save_pdf(self, file_content: bytes, original_filename: str) -> Tuple[str, str]:
        """
        Save uploaded PDF file to local storage.

        Args:
            file_content: PDF file bytes
            original_filename: Original filename from upload

        Returns:
            Tuple of (unique_filename, file_path)
        """
        # Generate unique filename
        file_extension = Path(original_filename).suffix
        unique_filename = f"{uuid4()}{file_extension}"
        file_path = self.pdf_dir / unique_filename

        # Save file
        with open(file_path, "wb") as f:
            f.write(file_content)

        return unique_filename, str(file_path)

    def save_markdown(self, markdown_content: str, pdf_filename: str) -> str:
        """
        Save processed markdown file.

        Args:
            markdown_content: Markdown text content
            pdf_filename: Original PDF filename (to create corresponding .md file)

        Returns:
            Path to saved markdown file
        """
        # Create markdown filename based on PDF filename
        base_name = Path(pdf_filename).stem
        markdown_filename = f"{base_name}.md"
        markdown_path = self.markdown_dir / markdown_filename

        # Save markdown
        with open(markdown_path, "w", encoding="utf-8") as f:
            f.write(markdown_content)

        return str(markdown_path)

    def delete_pdf(self, file_path: str) -> bool:
        """
        Delete PDF file from storage.

        Args:
            file_path: Path to PDF file

        Returns:
            True if deleted, False if file not found
        """
        try:
            if os.path.exists(file_path):
                os.remove(file_path)
                return True
            return False
        except Exception:
            return False

    def delete_markdown(self, file_path: str) -> bool:
        """
        Delete markdown file from storage.

        Args:
            file_path: Path to markdown file

        Returns:
            True if deleted, False if file not found
        """
        try:
            if os.path.exists(file_path):
                os.remove(file_path)
                return True
            return False
        except Exception:
            return False

    def get_pdf_path(self, unique_filename: str) -> Optional[str]:
        """
        Get full path to PDF file.

        Args:
            unique_filename: Unique filename

        Returns:
            Full path if file exists, None otherwise
        """
        file_path = self.pdf_dir / unique_filename
        return str(file_path) if file_path.exists() else None

    def file_exists(self, file_path: str) -> bool:
        """Check if file exists."""
        return os.path.exists(file_path)


# Global storage service instance
storage_service = StorageService()
