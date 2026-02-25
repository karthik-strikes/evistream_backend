"""
PDF processing service - wraps existing pdf_processor for backend use.
"""

import sys
from pathlib import Path
from typing import Dict, Any, Optional
import logging

# Add project root to Python path
project_root = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(project_root))

from pdf_processor.pdf_processor import PDFProcessor


logger = logging.getLogger(__name__)


class PDFProcessingService:
    """Service for processing PDF documents to markdown."""

    def __init__(self):
        """Initialize PDF processor."""
        try:
            self.processor = PDFProcessor()
            logger.info("PDF processor initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize PDF processor: {e}")
            self.processor = None

    def process_pdf_to_markdown(
        self,
        pdf_path: str,
        output_dir: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Process a PDF file and convert it to markdown.

        Args:
            pdf_path: Path to the PDF file
            output_dir: Optional output directory for markdown file

        Returns:
            Dictionary containing:
            - success: bool
            - markdown_path: str (path to generated markdown)
            - error: str (if failed)
            - metadata: dict (processing info)
        """
        if not self.processor:
            return {
                "success": False,
                "error": "PDF processor not initialized",
                "markdown_path": None,
                "metadata": {}
            }

        try:
            # Check if PDF file exists
            pdf_file = Path(pdf_path)
            if not pdf_file.exists():
                return {
                    "success": False,
                    "error": f"PDF file not found: {pdf_path}",
                    "markdown_path": None,
                    "metadata": {}
                }

            logger.info(f"Processing PDF: {pdf_path}")

            # Process the PDF using existing processor
            result = self.processor.process(
                content=str(pdf_path),
                force_reprocess=False
            )

            if result.get("status") == "success":
                # Extract markdown content from result
                markdown_content = result.get("marker", {}).get("markdown")

                if not markdown_content:
                    return {
                        "success": False,
                        "error": "No markdown content in processing result",
                        "markdown_path": None,
                        "metadata": {}
                    }

                # Save markdown content to a separate .md file
                unique_filename = result.get("unique_filename", Path(pdf_path).stem)
                markdown_dir = Path(__file__).parent.parent.parent.parent / "storage" / "processed" / "extracted_pdfs"
                markdown_dir.mkdir(parents=True, exist_ok=True)

                markdown_file = markdown_dir / f"{unique_filename}.md"
                with open(markdown_file, "w", encoding="utf-8") as f:
                    f.write(markdown_content)

                logger.info(f"Saved markdown to: {markdown_file}")

                return {
                    "success": True,
                    "markdown_path": str(markdown_file),
                    "error": None,
                    "metadata": {
                        "pages": result.get("marker", {}).get("pages", 0),
                        "processing_time": result.get("marker", {}).get("processing_time", 0),
                        "cost": result.get("marker", {}).get("cost", 0),
                        "unique_filename": unique_filename
                    }
                }
            else:
                return {
                    "success": False,
                    "error": result.get("error", "Unknown error during processing"),
                    "markdown_path": None,
                    "metadata": {}
                }

        except Exception as e:
            logger.error(f"Error processing PDF {pdf_path}: {str(e)}")
            return {
                "success": False,
                "error": str(e),
                "markdown_path": None,
                "metadata": {}
            }

    def check_processor_status(self) -> Dict[str, Any]:
        """Check if PDF processor is available and healthy."""
        if not self.processor:
            return {
                "available": False,
                "error": "PDF processor not initialized"
            }

        try:
            # Check cost tracking
            totals = self.processor.cost_tracker.get_current_totals()
            return {
                "available": True,
                "cost_info": totals,
                "error": None
            }
        except Exception as e:
            return {
                "available": False,
                "error": str(e)
            }


# Global service instance
pdf_processing_service = PDFProcessingService()
