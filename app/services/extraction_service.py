"""
Extraction service for running DSPy extractions on documents.
"""

import sys
import asyncio
import logging
from pathlib import Path
from typing import Dict, Any, Optional, List

# Add project root to Python path
project_root = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(project_root))

from core.extractor import run_async_extraction_and_evaluation
from schemas import get_schema, build_runtime
from schemas.registry import auto_discover_schemas
from utils.lm_config import get_dspy_model
from utils.helpers.print_helpers import print_extracted_vs_ground_truth

logger = logging.getLogger(__name__)


class ExtractionService:
    """Service for running extractions on documents."""

    def __init__(self):
        """Initialize extraction service."""
        self.initialized = False
        self.schemas_discovered = False

    def _ensure_dspy_configured(self):
        """Ensure DSPy is configured with LM."""
        if not self.initialized:
            try:
                # Auto-discover dynamic schemas
                if not self.schemas_discovered:
                    count = auto_discover_schemas()
                    logger.info(f"Auto-discovered {count} dynamic schemas")
                    self.schemas_discovered = True

                get_dspy_model()
                self.initialized = True
                logger.info("DSPy LM configured successfully")
            except Exception as e:
                logger.error(f"Failed to configure DSPy LM: {e}")
                raise

    def run_extraction(
        self,
        markdown_path: str,
        schema_name: str,
        document_ids: Optional[List[str]] = None,
        ground_truth: Optional[List[Dict]] = None,
        max_documents: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Run extraction on a document or batch of documents.

        Args:
            markdown_path: Path to markdown file or directory
            schema_name: Name of the schema to use for extraction
            document_ids: Optional list of document IDs to process
            ground_truth: Optional ground truth data for evaluation
            max_documents: Maximum number of documents to process

        Returns:
            Dictionary with extraction results
        """
        try:
            self._ensure_dspy_configured()

            # Get schema and build runtime
            logger.info(f"Loading schema: {schema_name}")
            schema_config = get_schema(schema_name)
            schema_runtime = build_runtime(schema_config)

            # Check if single file or directory
            path = Path(markdown_path)

            if path.is_file():
                # Single file extraction
                return asyncio.run(
                    self._run_single_extraction(
                        markdown_path=str(path),
                        schema_runtime=schema_runtime,
                        ground_truth=ground_truth or []
                    )
                )
            elif path.is_dir():
                # Batch extraction
                return asyncio.run(
                    self._run_batch_extraction(
                        markdown_dir=str(path),
                        schema_runtime=schema_runtime,
                        document_ids=document_ids,
                        max_documents=max_documents
                    )
                )
            else:
                raise FileNotFoundError(f"Path not found: {markdown_path}")

        except Exception as e:
            logger.error(f"Extraction failed: {e}")
            return {
                "success": False,
                "error": str(e)
            }

    async def _run_single_extraction(
        self,
        markdown_path: str,
        schema_runtime,
        ground_truth: List[Dict]
    ) -> Dict[str, Any]:
        """
        Run extraction on a single markdown file.

        Args:
            markdown_path: Path to markdown file
            schema_runtime: Schema runtime instance
            ground_truth: Ground truth data for evaluation

        Returns:
            Dictionary with extraction results
        """
        try:
            logger.info(f"Running extraction on: {markdown_path}")

            # Read markdown content
            with open(markdown_path, 'r', encoding='utf-8') as f:
                markdown_content = f.read()

            # Run extraction
            result = await run_async_extraction_and_evaluation(
                markdown_content=markdown_content,
                source_file=markdown_path,
                one_study_records=ground_truth,
                schema_runtime=schema_runtime,
                override=False,
                run_diagnostic=False,
                print_results=False,
                field_level_analysis=False,
                print_field_table=False
            )

            logger.info(f"Extraction completed for: {markdown_path}")

            # Tag each result with the source file for document-ID mapping
            baseline_results = result["baseline_results"]
            if isinstance(baseline_results, list):
                tagged_results = [
                    {**(r if isinstance(r, dict) else {"data": r}), "source_file": markdown_path}
                    for r in baseline_results
                ]
            else:
                tagged_results = [{"data": baseline_results, "source_file": markdown_path}]

            return {
                "success": True,
                "results": tagged_results,
                "source_file": markdown_path
            }

        except Exception as e:
            logger.error(f"Single extraction failed: {e}")
            return {
                "success": False,
                "error": str(e),
                "source_file": markdown_path
            }

    async def _run_batch_extraction(
        self,
        markdown_dir: str,
        schema_runtime,
        document_ids: Optional[List[str]] = None,
        max_documents: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Run extraction on multiple markdown files.

        Args:
            markdown_dir: Directory containing markdown files
            schema_runtime: Schema runtime instance
            document_ids: Optional list of document IDs to process
            max_documents: Maximum number of documents to process

        Returns:
            Dictionary with batch extraction results
        """
        try:
            logger.info(f"Running batch extraction on directory: {markdown_dir}")

            # Find all markdown files
            dir_path = Path(markdown_dir)
            all_markdown_files = list(dir_path.glob("*.md"))

            # Filter by document IDs if provided
            logger.info(f"DEBUG: Found {len(all_markdown_files)} markdown files")
            logger.info(f"DEBUG: document_ids = {document_ids}")
            if document_ids:
                logger.info(f"DEBUG: File stems = {[f.stem for f in all_markdown_files]}")
                markdown_files = [
                    f for f in all_markdown_files
                    if any(doc_id in f.stem for doc_id in document_ids)
                ]
                logger.info(f"DEBUG: Filtered to {len(markdown_files)} files")
            else:
                markdown_files = all_markdown_files

            # Limit number of documents
            if max_documents:
                markdown_files = markdown_files[:max_documents]

            logger.info(f"Processing {len(markdown_files)} documents")

            # Run extractions sequentially (can be made parallel if needed)
            results = []
            for md_file in markdown_files:
                result = await self._run_single_extraction(
                    markdown_path=str(md_file),
                    schema_runtime=schema_runtime,
                    ground_truth=[]
                )
                results.append(result)

            # Count successes and failures
            successes = [r for r in results if r.get("success")]
            failures = [r for r in results if not r.get("success")]

            logger.info(
                f"Batch extraction completed: {len(successes)} succeeded, "
                f"{len(failures)} failed"
            )

            return {
                "success": True,
                "total_documents": len(results),
                "successful_extractions": len(successes),
                "failed_extractions": len(failures),
                "results": results
            }

        except Exception as e:
            logger.error(f"Batch extraction failed: {e}")
            return {
                "success": False,
                "error": str(e)
            }

    def check_extraction_status(self) -> Dict[str, Any]:
        """
        Check if extraction service is ready.

        Returns:
            Dictionary with service status
        """
        try:
            self._ensure_dspy_configured()
            return {
                "status": "ready",
                "dspy_configured": self.initialized
            }
        except Exception as e:
            return {
                "status": "error",
                "error": str(e),
                "dspy_configured": False
            }


# Global service instance
extraction_service = ExtractionService()
