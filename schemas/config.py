"""
Dynamic Schema Configuration

Self-contained configuration for dynamically generated schemas.
Stores all information needed to build and execute extraction pipelines.
"""

import importlib
import asyncio
import logging
from dataclasses import dataclass
from typing import Dict, Any, List, Optional
import dspy
from utils.extraction_assertions import validate_extraction_output
from utils.source_linker import enrich_extraction_results, parse_page_boundaries

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DynamicSchemaConfig:
    """
    Configuration for dynamically generated schemas.

    Stores all information needed to:
    - Build extraction pipeline following decomposition stages
    - Load signature classes on demand
    - Discover output fields at runtime
    """

    # Identity
    schema_name: str  # Human-readable name (e.g., "ClinicalSummary")
    task_name: str    # Technical name (e.g., "task_4bc4179e")

    # Paths (for lazy loading)
    module_path: str  # e.g., "dspy_components.tasks.task_4bc4179e"
    signatures_path: str  # e.g., "dspy_components.tasks.task_4bc4179e.signatures"

    # Signatures (from decomposition.signatures)
    signature_class_names: List[str]  # All signature names in order

    # Pipeline Structure (from decomposition.pipeline)
    pipeline_stages: List[Dict[str, Any]]  # Execution stages with dependencies

    # Metadata
    project_id: str
    form_id: str
    form_name: str

    def __post_init__(self):
        """Validate configuration at creation time."""
        self._validate_parallel_field_uniqueness()

    def _validate_parallel_field_uniqueness(self):
        """Ensure no two signatures in the same parallel stage produce overlapping output fields."""
        for stage in self.pipeline_stages:
            if stage.get("execution") != "parallel":
                continue
            sig_names = stage.get("signatures", [])
            if len(sig_names) < 2:
                continue
            try:
                signatures_module = importlib.import_module(self.signatures_path)
            except (ImportError, ModuleNotFoundError):
                # Module not yet generated; skip validation
                return
            seen_fields: Dict[str, str] = {}
            for sig_name in sig_names:
                sig_class = getattr(signatures_module, sig_name, None)
                if sig_class is None:
                    continue
                # Get output fields from DSPy signature
                output_fields = list(getattr(sig_class, 'output_fields', {}).keys())
                for field in output_fields:
                    if field in seen_fields:
                        raise ValueError(
                            f"Duplicate output field '{field}' across parallel signatures "
                            f"'{seen_fields[field]}' and '{sig_name}' in stage {stage.get('stage', '?')}"
                        )
                    seen_fields[field] = sig_name

    def load_signature_class(self, signature_name: str) -> Any:
        """Load a specific signature class by name."""
        signatures_module = importlib.import_module(self.signatures_path)
        return getattr(signatures_module, signature_name)

    def load_all_signature_classes(self) -> List[Any]:
        """Load all signature classes."""
        signatures_module = importlib.import_module(self.signatures_path)
        return [getattr(signatures_module, name) for name in self.signature_class_names]

    def build_pipeline(self) -> Any:
        """
        Build extraction pipeline following pipeline_stages structure.

        Respects:
        - Stage execution order
        - Parallel vs sequential execution within stages
        - Field dependencies between stages
        """
        return self._build_staged_pipeline()

    def _build_staged_pipeline(self) -> Any:
        """Build pipeline that follows pipeline_stages execution order."""
        modules_module = importlib.import_module(f"{self.module_path}.modules")

        # Map signature names to extractor factory classes (NOT instances).
        # DSPy modules hold internal state (message histories, optimizer state),
        # so sharing a single instance across concurrent coroutines causes
        # state corruption. We store the class and instantiate per-invocation.
        extractor_factories = {}
        for sig_name in self.signature_class_names:
            extractor_name = f"Async{sig_name}Extractor"
            if hasattr(modules_module, extractor_name):
                extractor_factories[sig_name] = getattr(modules_module, extractor_name)
            else:
                logger.warning(
                    f"Extractor {extractor_name} not found in {self.module_path}.modules")

        if not extractor_factories:
            raise ValueError(
                f"No extractors found for schema {self.schema_name}. Available classes: {[n for n in dir(modules_module) if not n.startswith('_')]}")

        class StagedPipeline(dspy.Module):
            """Pipeline that executes stages in order with dependency handling."""

            MAX_EXTRACTOR_RETRIES = 2

            def __init__(self, stages, extractor_factories_map):
                super().__init__()
                self.stages = stages
                self.extractor_factories = extractor_factories_map

            @staticmethod
            def _to_dict(result: Any) -> Optional[Dict]:
                """Convert extractor result to dict, or None if not convertible."""
                if isinstance(result, dict):
                    return result
                if isinstance(result, Exception):
                    return None
                if hasattr(result, '__dict__'):
                    return {k: v for k, v in result.__dict__.items() if not k.startswith('_')}
                return None

            async def _run_extractor_with_retry(self, sig_name, extractor, markdown_content, **kwargs):
                """Run a single extractor with retry on total failure.

                Retries ONLY when all fields are empty/NR (extractor completely failed).
                Does NOT retry when some fields have data and others are NR — that's
                normal for papers that don't report everything.
                """
                from utils.circuit_breaker import ModelRouter, AllModelsUnavailableError

                best_result = None
                best_score = -1.0

                for attempt in range(self.MAX_EXTRACTOR_RETRIES + 1):
                    try:
                        try:
                            router = ModelRouter.get_instance()
                            result = await router.run_with_routing(
                                async_callable=extractor,
                                operation_name=f"Extractor:{sig_name}",
                                markdown_content=markdown_content,
                                **kwargs
                            )
                        except AllModelsUnavailableError as e:
                            logger.error(
                                f"[StagedPipeline] {sig_name}: All models unavailable. "
                                f"CB states: {e.model_states}"
                            )
                            return {"__extraction_failed": True, "__reason": "all_models_unavailable"}
                        result_dict = self._to_dict(result)

                        if result_dict is None:
                            result_dict = {}

                        quality = validate_extraction_output(result_dict)

                        # Track best result across attempts
                        if quality["score"] > best_score:
                            best_score = quality["score"]
                            best_result = result_dict

                        # Accept if at least one field has real data
                        if not quality["all_failed"]:
                            if quality["nr_fields"]:
                                logger.info(
                                    f"Extractor {sig_name}: {len(quality['substantive_fields'])} "
                                    f"substantive, {len(quality['nr_fields'])} NR "
                                    f"(NR fields: {quality['nr_fields']})"
                                )
                            return result_dict

                        # All fields empty/NR — retry
                        if attempt < self.MAX_EXTRACTOR_RETRIES:
                            logger.warning(
                                f"Extractor {sig_name}: all fields empty/NR "
                                f"(empty={quality['empty_fields']}, nr={quality['nr_fields']}) "
                                f"attempt {attempt + 1}/{self.MAX_EXTRACTOR_RETRIES + 1}, retrying..."
                            )
                    except Exception as e:
                        logger.warning(f"Extractor {sig_name} failed (attempt {attempt + 1}): {e}")
                        if attempt == self.MAX_EXTRACTOR_RETRIES:
                            break

                logger.error(
                    f"Extractor {sig_name}: all attempts returned empty/NR "
                    f"(best score={best_score:.2f} after {self.MAX_EXTRACTOR_RETRIES + 1} attempts)"
                )
                if best_result is not None:
                    return best_result
                return {"__extraction_failed": True, "__reason": f"all {self.MAX_EXTRACTOR_RETRIES + 1} extraction attempts failed"}

            def _create_extractor(self, sig_name):
                """Create a fresh extractor instance from the factory for this signature."""
                return self.extractor_factories[sig_name]()

            async def __call__(self, markdown_content: str, **kwargs):
                """
                Execute pipeline following stage order.

                Accumulates results from each stage and passes them to dependent stages.
                """
                accumulated_results = {}

                # Execute each stage in order
                for stage_info in self.stages:
                    stage_num = stage_info.get("stage", 0)
                    signature_names = stage_info.get("signatures", [])
                    execution_mode = stage_info.get("execution", "parallel")

                    # Get fresh extractor instances for this stage
                    stage_extractors = [
                        (sig_name, self._create_extractor(sig_name))
                        for sig_name in signature_names
                        if sig_name in self.extractor_factories
                    ]

                    if not stage_extractors:
                        logger.warning(
                            f"No extractors found for stage {stage_num} with signatures {signature_names}")
                        continue

                    # Build kwargs for this stage, filtered to only the fields it needs.
                    # requires_fields declares which upstream fields this stage depends on.
                    # Without filtering, irrelevant fields pollute the DSPy call.
                    requires_fields = stage_info.get("requires_fields", [])
                    if requires_fields:
                        relevant_accumulated = {
                            k: v for k, v in accumulated_results.items()
                            if k in requires_fields
                        }
                        stage_kwargs = {**kwargs, **relevant_accumulated}
                    else:
                        # No requires_fields declared — pass everything
                        # (backward compatible with static hand-written tasks)
                        stage_kwargs = {**kwargs, **accumulated_results}

                    if execution_mode == "parallel":
                        # Run all extractors in parallel with retry
                        results = await asyncio.gather(
                            *[
                                self._run_extractor_with_retry(
                                    sig_name, extractor, markdown_content, **stage_kwargs
                                )
                                for sig_name, extractor in stage_extractors
                            ]
                        )

                        # Merge results from all extractors in this stage
                        for i, result_dict in enumerate(results):
                            if result_dict:
                                if result_dict.get("__extraction_failed"):
                                    logger.warning(
                                        f"Stage {stage_num} extractor {i} failed: {result_dict.get('__reason')}"
                                    )
                                    continue
                                accumulated_results.update(result_dict)
                    else:  # sequential
                        # Run extractors one by one, passing accumulated results forward
                        for sig_name, extractor in stage_extractors:
                            result_dict = await self._run_extractor_with_retry(
                                sig_name, extractor, markdown_content, **stage_kwargs
                            )
                            if result_dict:
                                if result_dict.get("__extraction_failed"):
                                    logger.warning(
                                        f"Stage {stage_num} extractor {sig_name} failed: {result_dict.get('__reason')}"
                                    )
                                    continue
                                accumulated_results.update(result_dict)
                                stage_kwargs.update(result_dict)

                # Enrich results with PDF source locations
                try:
                    page_map = parse_page_boundaries(markdown_content)
                    if page_map:
                        accumulated_results = enrich_extraction_results(
                            accumulated_results, markdown_content, page_map
                        )
                except Exception as e:
                    logger.warning(f"Source linking failed (non-fatal): {e}")

                # Return final accumulated results
                return accumulated_results

            async def run_batch(
                self,
                papers: list,
                task_semaphore: asyncio.Semaphore,
                on_paper_done=None,
            ) -> dict:
                """
                Stage-level fan-out: all papers run each stage together.

                For each pipeline stage:
                  - parallel stages:   fans out ALL (paper × extractor) tasks simultaneously
                  - sequential stages: fans out papers, but within each paper extractors stay sequential
                Semaphore limits total LLM calls in flight across all papers.
                """
                accumulated = {p["doc_id"]: {} for p in papers}

                for stage_info in self.stages:
                    stage_num       = stage_info.get("stage", 0)
                    signature_names = stage_info.get("signatures", [])
                    execution_mode  = stage_info.get("execution", "parallel")
                    requires_fields = stage_info.get("requires_fields", [])

                    # Collect valid signature names for this stage
                    valid_sig_names = [
                        sig_name for sig_name in signature_names
                        if sig_name in self.extractor_factories
                    ]
                    if not valid_sig_names:
                        logger.warning(f"[run_batch] No extractors for stage {stage_num}, skipping")
                        continue

                    logger.info(
                        f"[run_batch] Stage {stage_num}: {len(papers)} papers × "
                        f"{len(valid_sig_names)} extractors ({execution_mode})"
                    )

                    def _build_stage_kwargs(doc_id):
                        paper_acc = accumulated[doc_id]
                        if requires_fields:
                            return {k: v for k, v in paper_acc.items() if k in requires_fields}
                        return dict(paper_acc)

                    if execution_mode == "parallel":
                        async def _run_one(doc_id, sig_name, markdown, stage_kwargs):
                            # Fresh extractor instance per call to avoid shared state
                            extractor = self._create_extractor(sig_name)
                            async with task_semaphore:
                                result = await self._run_extractor_with_retry(
                                    sig_name, extractor, markdown, **stage_kwargs
                                )
                            return doc_id, result

                        tasks = [
                            _run_one(
                                p["doc_id"], sig_name,
                                p["markdown_content"],
                                _build_stage_kwargs(p["doc_id"])
                            )
                            for p in papers
                            for sig_name in valid_sig_names
                        ]
                        stage_results = await asyncio.gather(*tasks, return_exceptions=True)
                        for res in stage_results:
                            if isinstance(res, Exception):
                                logger.warning(f"[run_batch] Stage {stage_num} task raised: {res}")
                                continue
                            doc_id, result_dict = res
                            if result_dict:
                                if result_dict.get("__extraction_failed"):
                                    logger.warning(
                                        f"[run_batch] Stage {stage_num} doc {doc_id} failed: {result_dict.get('__reason')}"
                                    )
                                    continue
                                accumulated[doc_id].update(result_dict)

                    else:  # sequential
                        async def _run_sequential_paper(paper):
                            doc_id   = paper["doc_id"]
                            markdown = paper["markdown_content"]
                            stage_kw = _build_stage_kwargs(doc_id)
                            for sig_name in valid_sig_names:
                                # Fresh extractor instance per call
                                extractor = self._create_extractor(sig_name)
                                async with task_semaphore:
                                    result = await self._run_extractor_with_retry(
                                        sig_name, extractor, markdown, **stage_kw
                                    )
                                if result:
                                    if result.get("__extraction_failed"):
                                        logger.warning(
                                            f"[run_batch] Stage {stage_num} doc {doc_id} extractor {sig_name} failed: {result.get('__reason')}"
                                        )
                                        continue
                                    stage_kw.update(result)
                                    accumulated[doc_id].update(result)

                        await asyncio.gather(
                            *[_run_sequential_paper(p) for p in papers],
                            return_exceptions=True
                        )

                    logger.info(f"[run_batch] Stage {stage_num} complete")

                # Enrich all paper results with PDF source locations
                for paper in papers:
                    doc_id = paper["doc_id"]
                    try:
                        page_map = parse_page_boundaries(paper["markdown_content"])
                        if page_map and accumulated[doc_id]:
                            accumulated[doc_id] = enrich_extraction_results(
                                accumulated[doc_id], paper["markdown_content"], page_map
                            )
                    except Exception as e:
                        logger.warning(f"[run_batch] Source linking failed for {doc_id} (non-fatal): {e}")

                # All stages done — fire on_paper_done once per paper
                if on_paper_done is not None:
                    for paper in papers:
                        doc_id = paper["doc_id"]
                        try:
                            await on_paper_done(doc_id, accumulated[doc_id])
                        except Exception as e:
                            logger.warning(f"[run_batch] on_paper_done error for {doc_id}: {e}")

                return accumulated

        return StagedPipeline(self.pipeline_stages, extractor_factories)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dict for database/JSON storage."""
        return {
            "schema_name": self.schema_name,
            "task_name": self.task_name,
            "module_path": self.module_path,
            "signatures_path": self.signatures_path,
            "signature_class_names": self.signature_class_names,
            "pipeline_stages": self.pipeline_stages,
            "project_id": self.project_id,
            "form_id": self.form_id,
            "form_name": self.form_name,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "DynamicSchemaConfig":
        """Deserialize from dict (database/JSON)."""
        return cls(**data)
