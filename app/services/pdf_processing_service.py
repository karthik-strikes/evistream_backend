"""
PDF processing service - wraps existing pdf_processor for backend use.
"""

from pathlib import Path
from typing import Dict, Any, Optional
import logging

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
                "markdown_content": None,
                "metadata": {}
            }

        try:
            # Check if PDF file exists
            pdf_file = Path(pdf_path)
            if not pdf_file.exists():
                return {
                    "success": False,
                    "error": f"PDF file not found: {pdf_path}",
                    "markdown_content": None,
                    "metadata": {}
                }

            logger.info(f"Processing PDF: {pdf_path}")

            # Process the PDF using existing processor
            result = self.processor.process(
                content=str(pdf_path),
                force_reprocess=False
            )

            if result.get("status") == "success":
                marker_md = result.get("marker", {}) or {}
                marker_json = result.get("marker_json", {}) or {}

                markdown_content = marker_md.get("markdown")

                if not markdown_content:
                    return {
                        "success": False,
                        "error": "No markdown content in processing result",
                        "markdown_content": None,
                        "metadata": {}
                    }

                # The second (json-format) call returns block-level structure under "json".
                # Some Datalab tiers may surface page_count / parse_quality_score on either
                # response; prefer the json call (richer), fall back to markdown call.
                blocks_json = marker_json.get("json")
                page_count = (
                    marker_json.get("page_count")
                    or marker_md.get("page_count")
                    or marker_md.get("pages")
                    or 0
                )
                parse_quality_score = (
                    marker_json.get("parse_quality_score")
                    if marker_json.get("parse_quality_score") is not None
                    else marker_md.get("parse_quality_score")
                )
                checkpoint_id = marker_json.get("checkpoint_id") or marker_md.get("checkpoint_id")
                request_id = marker_json.get("request_id") or marker_md.get("request_id")

                # Per-step outcome of the json/bbox call (call 2), independent of
                # the overall markdown success above. Prefer the explicit status
                # the processor recorded; fall back to inferring from blocks_json.
                marker_json_status = result.get("marker_json_status")
                if marker_json_status:
                    blocks_status = marker_json_status
                else:
                    blocks_status = "completed" if blocks_json else "failed"
                blocks_error = result.get("marker_json_error")

                return {
                    "success": True,
                    "markdown_content": markdown_content,
                    "blocks_json": blocks_json,
                    "blocks_status": blocks_status,
                    "blocks_error": blocks_error,
                    "parse_quality_score": parse_quality_score,
                    "checkpoint_id": checkpoint_id,
                    "request_id": request_id,
                    "page_count": page_count,
                    "error": None,
                    "metadata": {
                        "pages": page_count,
                        "processing_time": marker_md.get("processing_time", 0),
                        "cost": marker_md.get("cost", 0),
                        "parse_quality_score": parse_quality_score,
                    }
                }
            else:
                return {
                    "success": False,
                    "error": result.get("error", "Unknown error during processing"),
                    "markdown_content": None,
                    "metadata": {}
                }

        except Exception as e:
            logger.error(f"Error processing PDF {pdf_path}: {str(e)}")
            return {
                "success": False,
                "error": str(e),
                "markdown_content": None,
                "metadata": {}
            }

    def fetch_blocks_only(self, pdf_path: str) -> Dict[str, Any]:
        """Re-fetch only the Datalab json/bbox sidecar (call 2) for a PDF whose
        markdown was already processed. Does NOT invoke the markdown call, so the
        markdown conversion is not re-billed. Returns the same field shape the
        backfill task consumes.
        """
        if not self.processor:
            return {"success": False, "error": "PDF processor not initialized", "blocks_json": None}
        try:
            if not Path(pdf_path).exists():
                return {"success": False, "error": f"PDF file not found: {pdf_path}", "blocks_json": None}

            marker_json = self.processor.parse_blocks_only(str(pdf_path)) or {}
            blocks_json = marker_json.get("json")
            if not blocks_json:
                return {"success": False, "error": "No block-level JSON returned by Datalab", "blocks_json": None}

            return {
                "success": True,
                "blocks_json": blocks_json,
                "parse_quality_score": marker_json.get("parse_quality_score"),
                "checkpoint_id": marker_json.get("checkpoint_id"),
                "request_id": marker_json.get("request_id"),
                "page_count": marker_json.get("page_count") or 0,
                "error": None,
            }
        except Exception as e:
            logger.error(f"Error fetching blocks for {pdf_path}: {str(e)}")
            return {"success": False, "error": str(e), "blocks_json": None}

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
