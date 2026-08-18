"""
Dynamic Schema Configuration

Self-contained configuration for dynamically generated schemas.
Stores all information needed to build and execute extraction pipelines.
"""

import importlib
import inspect
import json
import asyncio
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
import os
from dataclasses import dataclass, field
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

    # Runtime schema definition (Phase A+: populated by code-gen workflow).
    # When present and USE_RUNTIME_BUILDERS=true, extraction builds DSPy
    # classes via type() from this dict instead of importlib from disk files.
    schema_def: Optional[Dict[str, Any]] = field(default=None)

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

            seen_fields: Dict[str, str] = {}

            if not self.schema_def:
                continue
            sig_defs_map = {
                s["class_name"]: s
                for s in self.schema_def.get("signatures", [])
            }
            for sig_name in sig_names:
                sig_def = sig_defs_map.get(sig_name)
                if sig_def is None:
                    continue
                for out in sig_def.get("output_fields", []):
                    fname = out["name"]
                    if fname in seen_fields:
                        raise ValueError(
                            f"Duplicate output field '{fname}' across parallel signatures "
                            f"'{seen_fields[fname]}' and '{sig_name}' in stage {stage.get('stage', '?')}"
                        )
                    seen_fields[fname] = sig_name

    def build_pipeline(self, pilot_feedback=None, review_scope=None) -> Any:
        """
        Build extraction pipeline following pipeline_stages structure.

        Review-time field edits (examples/hints/rules/description) are spliced
        directly into signatures.py at save time, so they are already in the
        base desc before this runs. Only pilot calibration is injected at runtime.

        Args:
            pilot_feedback: Optional dict with 'field_examples' and 'field_instructions'
                from pilot calibration.
            review_scope: Optional project-level scope text, injected into every
                signature as extraction context. Never filters rows.
        """
        return self._build_staged_pipeline(
            pilot_feedback=pilot_feedback, review_scope=review_scope
        )

    def _build_staged_pipeline(self, pilot_feedback=None, review_scope=None) -> Any:
        """Build pipeline that follows pipeline_stages execution order."""
        use_runtime = os.getenv("USE_RUNTIME_BUILDERS", "true").lower() == "true"

        if use_runtime and self.schema_def:
            # Phase B: build extractor classes at runtime from JSON schema_def.
            # No disk imports, no sys.modules staleness, no Celery worker restarts needed.
            from dspy_components.runtime_builders import (
                apply_review_scope, build_schema_classes, build_signature_class,
            )

            # Scope is folded into each sig_def BEFORE any class is built, so the
            # signature cache (keyed on the content hash of sig_def) can never serve
            # one project's compiled signature to another project with a different
            # scope. Returns self.schema_def unchanged when no scope is set.
            schema_def = apply_review_scope(self.schema_def, review_scope)

            extractor_factories = build_schema_classes(schema_def, self.task_name)
            pipeline_stages = schema_def.get("pipeline_stages", self.pipeline_stages)

            # Pilot augmentation: build sig class from schema_def, then subclass via type()
            def _sig_provider(sig_name: str):
                sig_defs_map = {
                    s["class_name"]: s
                    for s in schema_def.get("signatures", [])
                }
                sig_def = sig_defs_map.get(sig_name)
                return build_signature_class(sig_def, self.task_name) if sig_def else None

        else:
            raise RuntimeError(
                f"Schema '{self.schema_name}' has no schema_def. "
                "Regenerate the form so the workflow finalize node can persist "
                "schema_def to forms.schema_def + schemas.schema_def."
            )

        _task_name_for_logging = self.task_name  # captured for cost-log tagging

        class StagedPipeline(dspy.Module):
            """Pipeline that executes stages in order with dependency handling."""

            MAX_EXTRACTOR_RETRIES = 2

            def __init__(self, stages, extractor_factories_map, pilot_fb=None, sig_module=None, sig_provider=None):
                super().__init__()
                self.stages = stages
                self.extractor_factories = extractor_factories_map
                self.pilot_feedback = pilot_fb
                self._signatures_module = sig_module  # legacy disk path
                self._sig_provider = sig_provider      # runtime builder path
                # Per-job model override (Beta) — set by ExtractionService when
                # the user has picked a non-default model in Settings. None
                # means "use ModelRouter's normal primary + fallback chain".
                self.primary_model_override: Optional[str] = None

            @staticmethod
            def _to_dict(result: Any) -> Optional[Dict]:
                """Convert extractor result to dict, or None if not convertible."""
                if isinstance(result, dict):
                    return result
                if isinstance(result, Exception):
                    return None
                if isinstance(result, dspy.Prediction):
                    # DSPy 2.5+ stores fields under `_store`, not __dict__.
                    return dict(result)
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
                                override_primary_model=self.primary_model_override,
                                markdown_content=markdown_content,
                                **kwargs
                            )
                            # Universal cache-usage log: fires the `prompt_cache
                            # write=X read=Y` summary line for both single-call
                            # and keyed extraction paths. Surface exceptions
                            # (we don't want them silenced) but otherwise quiet.
                            try:
                                from utils.dspy_async import _log_cache_usage
                                _log_cache_usage(cot_instance=extractor, tag=sig_name)
                            except Exception as _cache_exc:
                                logger.warning("cache usage log raised: %s", _cache_exc)
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
                """Create a fresh extractor instance from the factory for this signature.

                If pilot feedback exists, augments the signature class with calibration
                examples and instructions before instantiation.

                Review-time edits (hints/rules/examples/description) are already baked
                into signatures.py via the splicer, so no runtime overlay needed for them.
                """
                ExtractorCls = self.extractor_factories[sig_name]
                extractor = ExtractorCls()

                # Two-stage composite extractors: apply pilot calibration to the
                # inner record-discovery / slot-fill signatures directly (the outer module
                # has no single `.extract` predictor).
                if getattr(ExtractorCls, "_is_keyed_pipeline", False):
                    if self.pilot_feedback:
                        field_examples = self.pilot_feedback.get("field_examples", {})
                        field_instructions = self.pilot_feedback.get("field_instructions", {})
                        if field_examples or field_instructions:
                            from utils.pilot_feedback import augment_signature_with_feedback
                            parent = getattr(ExtractorCls, "_field_name", "")
                            key_cols = set(getattr(ExtractorCls, "_key_cols", None) or [])

                            def _parts(key: str):
                                return key.split(".", 1) if "." in key else (key, None)

                            # Record discovery outputs the parent field: top-level feedback
                            # plus anchor-column feedback belongs there.
                            fe1 = {
                                k: v for k, v in field_examples.items()
                                if _parts(k)[1] is None
                                or (_parts(k)[0] == parent and _parts(k)[1] in key_cols)
                            }
                            fi1 = {
                                k: v for k, v in field_instructions.items()
                                if _parts(k)[1] is None
                                or (_parts(k)[0] == parent and _parts(k)[1] in key_cols)
                            }
                            # The slot-fill signature's output fields ARE the attributes —
                            # re-key "parent.col" → "col" so feedback lands on them.
                            fe2 = {
                                _parts(k)[1]: v for k, v in field_examples.items()
                                if _parts(k)[0] == parent and _parts(k)[1]
                                and _parts(k)[1] not in key_cols
                            }
                            fi2 = {
                                _parts(k)[1]: v for k, v in field_instructions.items()
                                if _parts(k)[0] == parent and _parts(k)[1]
                                and _parts(k)[1] not in key_cols
                            }
                            for predictor, base_sig, fe, fi in (
                                (getattr(extractor, "record_discovery", None),
                                 getattr(ExtractorCls, "_record_discovery_class", None), fe1, fi1),
                                (getattr(extractor, "row_slot_filler", None),
                                 getattr(ExtractorCls, "_row_slot_fill_class", None), fe2, fi2),
                            ):
                                if predictor is None or base_sig is None or not (fe or fi):
                                    continue
                                augmented = augment_signature_with_feedback(base_sig, fe, fi)
                                if augmented is not base_sig and isinstance(predictor, dspy.ChainOfThought):
                                    predictor.signature = augmented
                    return extractor

                # Pilot augmentation: resolve sig class from either path
                _resolver = self._sig_provider or (
                    (lambda n: getattr(self._signatures_module, n, None))
                    if self._signatures_module else None
                )
                if self.pilot_feedback and _resolver:
                    field_examples = self.pilot_feedback.get("field_examples", {})
                    field_instructions = self.pilot_feedback.get("field_instructions", {})
                    if field_examples or field_instructions:
                        from utils.pilot_feedback import augment_signature_with_feedback
                        sig_class = _resolver(sig_name)
                        if sig_class is not None:
                            augmented = augment_signature_with_feedback(
                                sig_class, field_examples, field_instructions,
                            )
                            if augmented is not sig_class:
                                # Runtime-built extractors keep the predictor at
                                # `.extract`. Fall back to dir() scan only if
                                # the contract changes.
                                predictor = getattr(extractor, "extract", None)
                                if isinstance(predictor, dspy.ChainOfThought):
                                    predictor.signature = augmented
                                else:
                                    for attr_name in dir(extractor):
                                        attr = getattr(extractor, attr_name, None)
                                        if isinstance(attr, dspy.ChainOfThought):
                                            attr.signature = augmented
                                            break

                return extractor

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

                # Enrich results with PDF source locations (+ deterministic bboxes
                # when the caller passed a Datalab blocks_json sidecar).
                try:
                    page_map = parse_page_boundaries(markdown_content)
                    if page_map:
                        bbox_anchors = None
                        blocks_json = kwargs.get("_blocks_json")
                        if blocks_json:
                            try:
                                from utils.bbox_map import build_bbox_map
                                bbox_anchors = build_bbox_map(markdown_content, blocks_json)
                            except Exception as be:
                                logger.warning(f"build_bbox_map failed (non-fatal): {be}")
                        accumulated_results = enrich_extraction_results(
                            accumulated_results, markdown_content, page_map,
                            bbox_anchors=bbox_anchors,
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
                failed_docs: dict = {}  # doc_id → reason

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
                    stage_start = time.monotonic()

                    def _build_stage_kwargs(doc_id):
                        paper_acc = accumulated[doc_id]
                        if requires_fields:
                            return {k: v for k, v in paper_acc.items() if k in requires_fields}
                        return dict(paper_acc)

                    # LOG STAGE HANDOFF — opt-in via EVISTREAM_SIGNATURES_LOG=<path>
                    try:
                        import json as _json
                        _sig_log = os.getenv("EVISTREAM_SIGNATURES_LOG")
                        if _sig_log and requires_fields and papers:
                            _sample_doc = papers[0]["doc_id"]
                            _handoff = _build_stage_kwargs(_sample_doc)
                            _lines = [
                                f"\n  >> Stage {stage_num} handoff (doc {_sample_doc}):",
                                f"     fields passed in: {list(_handoff.keys())}",
                            ]
                            for _k, _v in _handoff.items():
                                _val = _v.get("value", _v) if isinstance(_v, dict) else _v
                                _lines.append(f"     {_k}: {str(_val)[:120]}")
                            with open(_sig_log, "a") as _fh:
                                _fh.write("\n".join(_lines) + "\n")
                    except Exception:
                        pass
                    # END LOG STAGE HANDOFF

                    if execution_mode == "parallel":
                        async def _run_one(doc_id, sig_name, markdown, stage_kwargs):
                            # Fresh extractor instance per call to avoid shared state
                            extractor = self._create_extractor(sig_name)
                            async with task_semaphore:
                                result = await self._run_extractor_with_retry(
                                    sig_name, extractor, markdown, **stage_kwargs
                                )
                            return doc_id, result

                        # Warm-then-fan-out per paper.
                        # Anthropic prompt caching: a cache entry only becomes available
                        # AFTER the first response begins. Firing all (paper × signature)
                        # calls via a single asyncio.gather causes every parallel sibling
                        # to MISS the cache and write a fresh entry — defeating the whole
                        # point of caching. To fix this, for each paper we run the first
                        # signature sequentially (which writes the paper to cache), then
                        # asyncio.gather the remaining signatures (which read from the
                        # now-warm cache at 0.1× input rate).
                        # Papers themselves stay parallel: each paper has a different
                        # markdown_content, so different cache keys, no contention.
                        async def _run_paper(paper):
                            doc_id = paper["doc_id"]
                            markdown = paper["markdown_content"]
                            stage_kwargs = _build_stage_kwargs(doc_id)
                            results = []
                            if valid_sig_names:
                                # Warm call: first signature populates the paper cache.
                                warm = await _run_one(doc_id, valid_sig_names[0], markdown, stage_kwargs)
                                results.append(warm)
                                if len(valid_sig_names) > 1:
                                    # Fan-out: remaining signatures run in parallel and
                                    # hit the warm cache.
                                    rest = await asyncio.gather(
                                        *[
                                            _run_one(doc_id, sn, markdown, stage_kwargs)
                                            for sn in valid_sig_names[1:]
                                        ],
                                        return_exceptions=True,
                                    )
                                    results.extend(rest)
                            return results

                        per_paper_results = await asyncio.gather(
                            *[_run_paper(p) for p in papers],
                            return_exceptions=True,
                        )
                        # Flatten: per_paper_results is a list of lists (or exceptions).
                        stage_results = []
                        for pr in per_paper_results:
                            if isinstance(pr, Exception):
                                logger.warning(f"[run_batch] Stage {stage_num} paper task raised: {pr}")
                                continue
                            stage_results.extend(pr)

                        for res in stage_results:
                            if isinstance(res, Exception):
                                logger.warning(f"[run_batch] Stage {stage_num} task raised: {res}")
                                continue
                            doc_id, result_dict = res
                            if result_dict:
                                if result_dict.get("__extraction_failed"):
                                    reason = result_dict.get('__reason') or "extraction_failed"
                                    failed_docs.setdefault(doc_id, reason)
                                    logger.warning(
                                        f"[run_batch] Stage {stage_num} doc {doc_id} failed: {reason}"
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
                                        reason = result.get('__reason') or "extraction_failed"
                                        failed_docs.setdefault(doc_id, reason)
                                        logger.warning(
                                            f"[run_batch] Stage {stage_num} doc {doc_id} extractor {sig_name} failed: {reason}"
                                        )
                                        continue
                                    stage_kw.update(result)
                                    accumulated[doc_id].update(result)

                        await asyncio.gather(
                            *[_run_sequential_paper(p) for p in papers],
                            return_exceptions=True
                        )

                    stage_elapsed = time.monotonic() - stage_start
                    n_calls = len(papers) * len(valid_sig_names)
                    logger.info(f"[run_batch] Stage {stage_num} complete ({stage_elapsed:.1f}s, {n_calls} calls)")
                    if stage_elapsed < 2.0 and n_calls > 0:
                        logger.warning(
                            f"[run_batch] Stage {stage_num} completed in {stage_elapsed:.1f}s for {n_calls} LLM calls "
                            f"— likely all extractors failed silently (check for exceptions above)"
                        )

                # Stamp failure metadata for any doc that hit __extraction_failed
                # in any stage. This is the signal `_on_paper_done` and the Celery
                # task use to populate `failed_document_ids` so the user-facing
                # retry banner appears (instead of silently writing all-NR rows).
                if failed_docs:
                    failed_at = datetime.now(timezone.utc).isoformat()
                    for doc_id, reason in failed_docs.items():
                        accumulated[doc_id]["_meta_extraction_failed"] = {
                            "reason": reason,
                            "at": failed_at,
                        }
                    logger.warning(
                        f"[run_batch] {len(failed_docs)} doc(s) marked as failed: "
                        f"{list(failed_docs.keys())}"
                    )

                # Enrich all paper results with PDF source locations + bboxes.
                # bbox_anchors comes from utils.bbox_map.build_bbox_map applied to
                # the Datalab blocks_json sidecar (per-block bbox + page from the
                # PDF parse — deterministic, no model guessing).
                for paper in papers:
                    doc_id = paper["doc_id"]
                    try:
                        page_map = parse_page_boundaries(paper["markdown_content"])
                        if page_map and accumulated[doc_id]:
                            bbox_anchors = None
                            blocks_json = paper.get("blocks_json")
                            if blocks_json:
                                try:
                                    from utils.bbox_map import build_bbox_map
                                    bbox_anchors = build_bbox_map(
                                        paper["markdown_content"], blocks_json
                                    )
                                except Exception as be:
                                    logger.warning(
                                        f"[run_batch] build_bbox_map failed for {doc_id} "
                                        f"(non-fatal): {be}"
                                    )
                            accumulated[doc_id] = enrich_extraction_results(
                                accumulated[doc_id], paper["markdown_content"], page_map,
                                bbox_anchors=bbox_anchors,
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

                # Flush per-call LLM usage (tokens/cost) to llm_history table.
                # Covers production extraction AND pilot calibration — both route
                # through this run_batch. ModelRouter holds the real history.
                try:
                    from utils.logging import log_all_lm_histories
                    n_logged = log_all_lm_histories(
                        source_file=f"extraction:{_task_name_for_logging}",
                        schema_name=_task_name_for_logging,
                        # The papers are still in memory here, which is the only
                        # place a call can be tied back to the paper it was
                        # about — the markdown lives in S3 and this list is gone
                        # by the time anyone opens the usage page.
                        run_papers=papers,
                    )
                    logger.info(f"[run_batch] llm_history flush: {n_logged} calls recorded")
                except Exception as e:
                    logger.warning(f"[run_batch] LLM cost logging failed (non-fatal): {e}")

                return accumulated

        return StagedPipeline(
            pipeline_stages, extractor_factories,
            pilot_fb=pilot_feedback,
            sig_module=None,
            sig_provider=_sig_provider,
        )

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
            "schema_def": self.schema_def,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "DynamicSchemaConfig":
        """Deserialize from dict (database/JSON)."""
        return cls(**data)
