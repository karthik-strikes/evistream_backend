"""
Extraction service for running DSPy extractions on documents.
"""

import asyncio
import logging
from pathlib import Path
from typing import Dict, Any, Optional, List

from app.config import settings as _app_settings
_BATCH_CONCURRENCY = _app_settings.EXTRACTION_BATCH_CONCURRENCY

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

            # Get schema and build runtime (re-discover if not found)
            logger.info(f"Loading schema: {schema_name}")
            try:
                schema_config = get_schema(schema_name)
            except ValueError:
                logger.info(f"Schema {schema_name} not found, re-discovering...")
                from schemas.registry import auto_discover_schemas
                auto_discover_schemas()
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

            logger.info(f"Processing {len(markdown_files)} documents (concurrency={_BATCH_CONCURRENCY})")

            # Run extractions in parallel, bounded by semaphore
            semaphore = asyncio.Semaphore(_BATCH_CONCURRENCY)

            async def _extract_with_semaphore(md_file):
                async with semaphore:
                    return await self._run_single_extraction(
                        markdown_path=str(md_file),
                        schema_runtime=schema_runtime,
                        ground_truth=[]
                    )

            results = await asyncio.gather(
                *[_extract_with_semaphore(f) for f in markdown_files],
                return_exceptions=False,
            )

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

    async def _run_files_parallel(
        self,
        path_to_doc_id: dict,
        schema_runtime,
        on_paper_done=None,
    ) -> list:
        """
        Run extraction on a list of (markdown_path, doc_id) pairs in parallel,
        bounded by _BATCH_CONCURRENCY semaphore.
        """
        semaphore = asyncio.Semaphore(_BATCH_CONCURRENCY)

        async def _extract_one(markdown_path, doc_id):
            async with semaphore:
                result = await self._run_single_extraction(
                    markdown_path=markdown_path,
                    schema_runtime=schema_runtime,
                    ground_truth=[]
                )
                # Tag with doc_id for the Celery worker to store correctly
                if result.get("success") and result.get("results"):
                    for r in result["results"]:
                        r["document_id"] = doc_id
                        r["source_file"] = markdown_path
                if on_paper_done is not None:
                    try:
                        await on_paper_done(doc_id, result)
                    except Exception as cb_err:
                        logger.warning(f"on_paper_done callback error (non-fatal): {cb_err}")
                return result

        tasks = [
            _extract_one(path, doc_id)
            for path, doc_id in path_to_doc_id.items()
        ]
        # return_exceptions=True means one paper failing does NOT
        # cancel the other 4 — each result is either a dict or an Exception
        return await asyncio.gather(*tasks, return_exceptions=True)

    async def _run_files_stage_fanout(
        self,
        path_to_doc_id: dict,
        schema_config,
        on_paper_done=None,
    ) -> list:
        """
        Stage-level fan-out extraction.
        Reads all files upfront, then fans them out stage-by-stage.
        """
        from app.config import settings as _s

        # Read all markdown files upfront
        papers = []
        for markdown_path, doc_id in path_to_doc_id.items():
            try:
                with open(markdown_path, "r", encoding="utf-8") as f:
                    content = f.read()
                papers.append({"doc_id": doc_id, "markdown_content": content, "path": markdown_path})
            except Exception as e:
                logger.error(f"[stage_fanout] Failed to read {markdown_path}: {e}")

        if not papers:
            return []

        pipeline = schema_config.build_pipeline()

        # Adaptive concurrency: reduce when circuit breaker is recovering
        # to avoid blasting a recovering model with 350 simultaneous requests.
        from utils.circuit_breaker import ModelRouter
        try:
            router = ModelRouter.get_instance()
            if router.is_any_breaker_half_open():
                effective_concurrency = max(1, int(_s.EXTRACTION_TASK_CONCURRENCY * 0.1))
                logger.warning(
                    f"[stage_fanout] Circuit breaker in HALF_OPEN — reducing concurrency "
                    f"from {_s.EXTRACTION_TASK_CONCURRENCY} to {effective_concurrency}"
                )
            else:
                effective_concurrency = _s.EXTRACTION_TASK_CONCURRENCY
        except Exception:
            effective_concurrency = _s.EXTRACTION_TASK_CONCURRENCY

        task_semaphore = asyncio.Semaphore(effective_concurrency)

        async def _on_paper_done(doc_id, accumulated_results):
            result = {
                "success": True,
                "results": [{**accumulated_results, "document_id": doc_id}],
            }
            if on_paper_done is not None:
                try:
                    await on_paper_done(doc_id, result)
                except Exception as e:
                    logger.warning(f"[stage_fanout] on_paper_done error for {doc_id}: {e}")

        accumulated = await pipeline.run_batch(papers, task_semaphore, on_paper_done=_on_paper_done)

        results = []
        for paper in papers:
            doc_id = paper["doc_id"]
            paper_data = accumulated.get(doc_id, {})
            results.append({
                "success": True,
                "results": [{**paper_data, "document_id": doc_id, "source_file": paper["path"]}],
                "source_file": paper["path"],
            })
        return results

    def run_files_extraction(
        self,
        path_to_doc_id: dict,
        schema_name: str,
        on_paper_done=None,
    ) -> Dict[str, Any]:
        """
        Sync entry point for Celery: run parallel extraction on a
        path→doc_id mapping. Calls asyncio.run() exactly once.
        """
        try:
            self._ensure_dspy_configured()
            try:
                schema_config = get_schema(schema_name)
            except ValueError:
                logger.info(f"Schema {schema_name} not found, re-discovering...")
                from schemas.registry import auto_discover_schemas
                auto_discover_schemas()
                schema_config = get_schema(schema_name)

            results = asyncio.run(
                self._run_files_stage_fanout(path_to_doc_id, schema_config, on_paper_done=on_paper_done)
            )

            all_results = []
            failed = 0
            for r in results:
                if isinstance(r, Exception):
                    # One paper threw an unhandled exception — log and skip
                    logger.error(f"Paper extraction raised exception: {r}")
                    failed += 1
                elif r.get("success") and r.get("results"):
                    all_results.extend(r["results"])
                else:
                    # Paper returned success=False (e.g. file read error, LLM error)
                    logger.warning(f"Paper extraction failed: {r.get('error')}")
                    failed += 1

            return {
                "success": True,
                "total_documents": len(path_to_doc_id),
                "successful_extractions": len(path_to_doc_id) - failed,
                "failed_extractions": failed,
                "results": all_results,
            }
        except Exception as e:
            logger.error(f"Parallel files extraction failed: {e}")
            return {"success": False, "error": str(e)}

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
