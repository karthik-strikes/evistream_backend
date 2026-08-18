"""
Extraction service for running DSPy extractions on documents.
"""

import asyncio
import logging
from typing import Dict, Any

from app.config import settings as _app_settings
_BATCH_CONCURRENCY = _app_settings.EXTRACTION_BATCH_CONCURRENCY

from schemas import get_schema
from schemas.registry import auto_discover_schemas
from utils.lm_config import get_dspy_model
from utils import record_context

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


    async def _run_files_stage_fanout(
        self,
        path_to_doc_id: dict,
        schema_config,
        on_paper_done=None,
        pilot_feedback=None,
        path_to_blocks_path: dict = None,
        model_name: str = None,
        review_scope: str = None,
    ) -> list:
        """
        Stage-level fan-out extraction.
        Reads all files upfront, then fans them out stage-by-stage.

        path_to_blocks_path (optional): {markdown_local_path: blocks_local_path}.
        When present, the Datalab blocks_json sidecar is loaded per paper and
        threaded into the pipeline so enrich_extraction_results can attach
        deterministic bboxes (utils/bbox_map.build_bbox_map) to each
        source_location.
        """
        import json as _json
        from app.config import settings as _s

        # Read all markdown files upfront
        papers = []
        for markdown_path, doc_id in path_to_doc_id.items():
            try:
                with open(markdown_path, "r", encoding="utf-8") as f:
                    content = f.read()
                # Imported documents (CT.gov, PubMed, EndNote, RIS) store their
                # record as JSON, not markdown, and the signature tells the
                # model it's reading a paper. Prepend a short note saying what
                # the record actually is, to quote values rather than JSON
                # syntax, and that a thin record's missing fields are genuinely
                # absent. Prepend only — the body stays byte-identical, so
                # quotes remain literal substrings of the stored file. Returns
                # "" for PDF markdown. See utils/record_context.
                content = record_context.preamble_for(content) + content
                paper = {"doc_id": doc_id, "markdown_content": content, "path": markdown_path}
                # Best-effort load of the blocks sidecar — bbox features
                # degrade gracefully if missing or malformed.
                if path_to_blocks_path:
                    blocks_path = path_to_blocks_path.get(markdown_path)
                    if blocks_path:
                        try:
                            with open(blocks_path, "r", encoding="utf-8") as bf:
                                paper["blocks_json"] = _json.load(bf)
                        except Exception as be:
                            logger.warning(
                                f"[stage_fanout] Failed to read blocks sidecar "
                                f"{blocks_path}: {be}"
                            )
                papers.append(paper)
            except Exception as e:
                logger.error(f"[stage_fanout] Failed to read {markdown_path}: {e}")

        if not papers:
            return []

        pipeline = schema_config.build_pipeline(
            pilot_feedback=pilot_feedback, review_scope=review_scope
        )
        # Per-job model override (Beta — user's Settings → AI Model selection).
        # Read inside StagedPipeline._run_extractor_with_retry, passed to
        # ModelRouter so this becomes the primary candidate for every call in
        # this batch while keeping circuit-breaker fallback intact.
        if model_name:
            pipeline.primary_model_override = model_name

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
            failed_meta = (accumulated_results or {}).get("_meta_extraction_failed")
            result = {
                "success": not bool(failed_meta),
                "results": [{**accumulated_results, "document_id": doc_id}],
            }
            if failed_meta:
                result["error"] = failed_meta.get("reason", "extraction_failed")
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
            failed_meta = paper_data.get("_meta_extraction_failed")
            entry = {
                "success": not bool(failed_meta),
                "results": [{**paper_data, "document_id": doc_id, "source_file": paper["path"]}],
                "source_file": paper["path"],
            }
            if failed_meta:
                entry["error"] = failed_meta.get("reason", "extraction_failed")
            results.append(entry)
        return results

    def run_files_extraction(
        self,
        path_to_doc_id: dict,
        schema_name: str,
        on_paper_done=None,
        pilot_feedback=None,
        path_to_blocks_path: dict = None,
        model_name: str = None,
        review_scope: str = None,
    ) -> Dict[str, Any]:
        """
        Sync entry point for Celery: run parallel extraction on a
        path→doc_id mapping. Calls asyncio.run() exactly once.

        path_to_blocks_path (optional): map markdown path → blocks sidecar
        path so enrich_extraction_results can attach deterministic bboxes
        to each source_location.
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
                self._run_files_stage_fanout(
                    path_to_doc_id, schema_config,
                    on_paper_done=on_paper_done,
                    pilot_feedback=pilot_feedback,
                    path_to_blocks_path=path_to_blocks_path,
                    model_name=model_name,
                    review_scope=review_scope,
                )
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
