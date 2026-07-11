"""
Workflow Orchestration for DSPy Code Generation

This module coordinates the complete DSPy code generation workflow using LangGraph:
- Cognitive form decomposition
- Parallel signature/module generation
- Multi-stage validation (coverage, syntax, semantic, flow)
- Pipeline assembly with stage-based execution
- Refinement loops with error feedback
- Final file assembly and export

The workflow uses LangGraph StateGraph for reliable orchestration with:
- State persistence via PostgreSQL (production-ready concurrent checkpoints)
- Conditional routing based on validation results
- Human-in-the-loop review capability
- Comprehensive error handling
"""

import json
import re
import asyncio
import logging
import uuid
from typing import Dict, Any, List, Optional
from pathlib import Path

from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver
try:
    from langgraph.checkpoint.postgres import PostgresSaver
    _POSTGRES_AVAILABLE = True
except ImportError:
    _POSTGRES_AVAILABLE = False

from .models import CompleteTaskGenerationState, SignatureGenerationState
from .signature_gen import SignatureGenerator
from .module_gen import ModuleGenerator
from .decomposition import decompose_form
from .decomposition_validator import DecompositionValidator
from .human_review import HumanReviewHandler
from .task_utils import sanitize_form_name
from config.models import CODEGEN_SIGNATURE_MODEL

logger = logging.getLogger(__name__)


class WorkflowOrchestrator:
    """
    Orchestrates the complete DSPy code generation workflow using LangGraph.

    This class implements the full pipeline for transforming form specifications
    into production-ready DSPy signatures, modules, and pipelines with comprehensive
    validation at every stage.
    """

    def __init__(
        self,
        signature_gen: Optional[SignatureGenerator] = None,
        module_gen: Optional[ModuleGenerator] = None,
        model_name: str = CODEGEN_SIGNATURE_MODEL,
        human_review_enabled: bool = False,
        log_callback: Optional[callable] = None,
    ):
        """
        Initialize workflow orchestrator.

        Args:
            signature_gen: Optional signature generator instance
            module_gen: Optional module generator instance
            model_name: LLM model identifier
            human_review_enabled: Enable human-in-the-loop review after validation
            log_callback: Optional callback for streaming logs (message, level)
        """
        self.sig_gen = signature_gen or SignatureGenerator(model_name)
        self.mod_gen = module_gen or ModuleGenerator(model_name)
        self.model_name = model_name

        # Initialize checkpointer — prefer PostgreSQL for cross-worker persistence
        self._pg_conn = None
        self.checkpointer = None
        if _POSTGRES_AVAILABLE:
            try:
                import os, psycopg
                db_url = ""
                try:
                    from app.config import settings
                    db_url = getattr(settings, "DATABASE_URL", "") or ""
                except ImportError:
                    pass
                if not db_url:
                    db_url = os.environ.get("DATABASE_URL", "")
                if db_url:
                    self._pg_conn = psycopg.connect(db_url)
                    self.checkpointer = PostgresSaver(self._pg_conn)
                    self.checkpointer.setup()
                    print("✓ Using PostgresSaver checkpointer (persistent)")
            except Exception as e:
                print(f"⚠ PostgresSaver failed ({e}), falling back to MemorySaver")
                if self._pg_conn:
                    try: self._pg_conn.close()
                    except Exception: pass
                self._pg_conn = None
                self.checkpointer = None
        if self.checkpointer is None:
            self.checkpointer = MemorySaver()
            print("✓ Using MemorySaver checkpointer (in-memory)")

        self.human_review_enabled = human_review_enabled
        self.log_callback = log_callback

        # Build the complete task workflow (sets self.complete_task_workflow)
        self._build_complete_task_workflow()

        # Initialize human review handler after workflow is built
        self.human_review_handler = HumanReviewHandler(
            self.complete_task_workflow)

    def __del__(self):
        """Clean up PostgreSQL connection if open."""
        if self._pg_conn is not None:
            try:
                self._pg_conn.close()
            except Exception:
                pass

    def _node_decompose_form(
        self, state: CompleteTaskGenerationState
    ) -> CompleteTaskGenerationState:
        """Node 1: Decompose form using cognitive behavior analysis"""
        print(f"\n{'='*70}")
        print(f"STAGE: Cognitive Decomposition")
        print(f"{'='*70}")
        print(f"Attempt {state['attempt'] + 1}/{state['max_attempts']}")

        if self.log_callback:
            self.log_callback(f"🧠 Stage: Cognitive Decomposition (Attempt {state['attempt'] + 1}/{state['max_attempts']})", "info")

        try:
            # Skip decomposition if already provided (e.g. approved from human review)
            if state.get("decomposition") and state.get("human_approved"):
                print("  ↩ Decomposition already set and approved — skipping LLM call")
                if self.log_callback:
                    self.log_callback("↩ Using approved decomposition — skipping re-decomposition", "info")
                state["current_stage"] = "decomposition_complete"
                state["field_coverage"] = state["decomposition"].get("field_coverage", {})
                return state

            # Use the new simplified decomposition approach
            decomposition = decompose_form(
                state["form_data"],
                model_name=self.model_name,
                feedback=state.get("decomposition_feedback") or None,
            )

            state["decomposition"] = decomposition

            # NEW: field_coverage is now directly in decomposition
            state["field_coverage"] = decomposition.get("field_coverage", {})

            state["current_stage"] = "decomposition_complete"

            if self.log_callback:
                sig_count = len(decomposition.get("signatures", []))
                self.log_callback(f"✓ Decomposition complete: {sig_count} signatures identified", "success")

            # NEW: Use 'signatures' instead of 'atomic_signatures'
            num_signatures = len(decomposition.get('signatures', []))
            num_stages = len(decomposition.get('pipeline', []))

            print(
                f"✓ Decomposed into {num_signatures} signatures across {num_stages} pipeline stages")

            if "reasoning_trace" in decomposition:
                print(
                    f"  Reasoning: {decomposition['reasoning_trace'][:100]}...")

        except Exception as e:
            state["errors"].append(f"Decomposition failed: {str(e)}")
            state["current_stage"] = "decomposition_failed"
            print(f"✗ Decomposition failed: {str(e)}")

        return state

    def _node_validate_decomposition(
        self, state: CompleteTaskGenerationState
    ) -> CompleteTaskGenerationState:
        """Node 2: Validate decomposition with comprehensive checks using DecompositionValidator"""
        print(f"\n{'='*70}")
        print(f"STAGE: Validate Decomposition")
        print(f"{'='*70}")

        validator = DecompositionValidator()
        is_valid, validation_results = validator.validate_complete_decomposition(
            state["decomposition"],
            state["form_data"]
        )

        # Store validation results in state
        state["validation_results"] = validation_results
        state["decomposition_valid"] = is_valid

        if not is_valid:
            # Build feedback from all validation issues
            issues = validation_results.get("issues", [])
            state["decomposition_feedback"] = "\n".join(issues)

            print(f"  ✗ Validation failed: {len(issues)} issues")
            for issue in issues:
                print(f"    - {issue}")

            # Show detailed validation results
            if not validation_results.get("field_coverage", {}).get("all_fields_covered"):
                missing = validation_results["field_coverage"].get(
                    "missing_fields", [])
                print(f"    Missing fields: {missing}")

            if not validation_results.get("dag_validation", {}).get("no_circular_dependencies"):
                print(f"    ⚠️  Circular dependencies detected!")

            if not validation_results.get("pipeline_validation", {}).get("passed"):
                print(f"    ⚠️  Pipeline structure issues detected!")

            if state["attempt"] >= state["max_attempts"] - 1:
                has_missing_fields = any("Missing fields" in i for i in issues)
                if has_missing_fields:
                    print(f"  ⚠️  WARNING: Last attempt with incomplete field coverage")
                    state["errors"].append("CRITICAL: Incomplete field coverage after maximum attempts")
        else:
            state["decomposition_feedback"] = ""
            print(f"  ✓ Validation passed")

            coverage = validation_results.get("field_coverage", {})
            print(f"    Fields covered: {coverage.get('fields_covered')}/{coverage.get('total_form_fields')}, DAG valid: ✓, Pipeline valid: ✓")

            # Phase 2 B7: Risk scorer + tier computation
            try:
                from .risk_scorer import score_decomposition
                risk = score_decomposition(state["decomposition"], state["form_data"])
                validation_results["risk_signals"] = risk["aggregated"]
                validation_results["risk_by_signature"] = risk["by_signature"]
                validation_results["risk_counts"] = risk["counts"]
                validation_results["review_tier"] = risk["tier"]
                state["validation_results"] = validation_results
                counts = risk["counts"]
                print(f"    Risk tier: {risk['tier']} (high={counts['high']}, warn={counts['warn']}, info={counts['info']})")
            except Exception as e:
                print(f"    Risk scoring failed (non-fatal): {e}")

        # If validation passed and we are about to pause for human review,
        # back up state to Supabase BEFORE the interrupt fires. The
        # `human_review` node body never runs under `interrupt_before`, so the
        # save must happen here — otherwise `approve_decomposition` resumes on
        # a different worker and finds "No saved state found".
        if state.get("decomposition_valid"):
            tier = validation_results.get("review_tier", "normal")
            will_pause_for_review = (
                state.get("human_review_enabled", False) and tier != "auto"
            )
            if will_pause_for_review:
                try:
                    self.human_review_handler.backup_state_for_review(state)
                except Exception as e:
                    print(f"⚠️  Pre-interrupt state backup failed: {e}")

        state["current_stage"] = "validation_complete"
        return state

    def _node_human_review(
        self, state: CompleteTaskGenerationState
    ) -> CompleteTaskGenerationState:
        """Node: Present decomposition to human for review and approval (delegates to HumanReviewHandler)"""
        return self.human_review_handler.node_human_review(state)

    def _node_generate_signatures(
        self, state: CompleteTaskGenerationState
    ) -> CompleteTaskGenerationState:
        """Node 3: Generate code for all signatures"""
        print(f"\n{'='*70}")
        print(f"STAGE: Generating Atomic Signatures")
        print(f"{'='*70}")

        if self.log_callback:
            sig_count = len(state["decomposition"].get("signatures", []))
            self.log_callback(f"⚙️ Stage: Generating {sig_count} DSPy signatures...", "info")

        signatures_code = []

        try:
            all_signatures = state["decomposition"].get("signatures", [])

            # Broadcast field list so frontend can show skeletons
            if self.log_callback:
                sig_list = [{"name": s.get("name", ""), "fields": list(s.get("fields", {}).keys())} for s in all_signatures]
                self.log_callback(json.dumps({"_type": "field_list", "signatures": sig_list}), "info")

            # ── Parallel signature generation ─────────────────────────────────
            # All signatures run concurrently via ThreadPoolExecutor.
            # A shared threading.Semaphore caps total in-flight LLM calls across
            # all signatures AND their per-column enrichment calls combined.
            #
            # Semaphore sizing for 4k calls/min API limit:
            #   - Each LLM call takes ~2-3 s latency
            #   - 4000 calls/min = ~66/sec → can sustain ~150 concurrent calls
            #   - A form rarely has >50 total calls (signatures + columns combined)
            #   - Set to 20 to leave headroom for simultaneous extractions/users
            import threading
            from concurrent.futures import ThreadPoolExecutor, as_completed

            MAX_CONCURRENT_LLM_CALLS = 20
            semaphore = threading.Semaphore(MAX_CONCURRENT_LLM_CALLS)

            def _generate_one(idx_sig_tuple):
                idx, enriched_sig = idx_sig_tuple
                sig_name = enriched_sig.get("name", f"Signature{idx}")
                print(f"\n[{idx}/{len(all_signatures)}] {sig_name} — starting")
                try:
                    result = self.sig_gen.generate_signature(
                        enriched_sig, semaphore=semaphore
                    )
                    return idx, sig_name, enriched_sig, result, None
                except Exception as e:
                    import traceback as _tb
                    return idx, sig_name, enriched_sig, None, (str(e), _tb.format_exc())

            with ThreadPoolExecutor(max_workers=len(all_signatures)) as pool:
                futures = [
                    pool.submit(_generate_one, (idx, sig))
                    for idx, sig in enumerate(all_signatures, 1)
                ]
                # Collect in completion order for live progress broadcast;
                # sort into index order at the end so signatures_code is stable.
                completed_items = []
                for future in as_completed(futures):
                    idx, sig_name, enriched_sig, result, err = future.result()
                    fields = enriched_sig.get("fields", {})

                    if err:
                        exc_str, tb_str = err
                        state["errors"].append(f"Error generating {sig_name}: {exc_str}")
                        print(f"  ✗ [{sig_name}] Error: {exc_str}\n{tb_str}")
                        completed_items.append((idx, None))
                        continue

                    if result["is_valid"]:
                        class_name = sanitize_form_name(sig_name)
                        output_field = list(fields.keys())[0] if fields else "output"
                        entry = {
                            "signature_name": sig_name,
                            "class_name": class_name,
                            "code": result["code"],
                            "spec": result.get("spec"),
                            "output_field": output_field,
                            "requires_context": bool(enriched_sig.get("depends_on")),
                            "context_fields": enriched_sig.get("depends_on", [])
                        }
                        completed_items.append((idx, entry))
                        print(f"  ✓ [{sig_name}] Generated")

                        if self.log_callback:
                            self.log_callback(json.dumps({
                                "_type": "field_done",
                                "name": sig_name,
                                "fields": list(fields.keys()),
                                "index": idx,
                                "total": len(all_signatures)
                            }), "info")
                    else:
                        errors = result.get("errors", [])
                        state["errors"].append(f"Failed to generate {sig_name}: {errors}")
                        print(f"  ✗ [{sig_name}] Generation failed: {errors}")
                        completed_items.append((idx, None))

            # Restore original signature order (futures complete out of order)
            for _, entry in sorted(completed_items, key=lambda x: x[0]):
                if entry is not None:
                    signatures_code.append(entry)

            print(f"\nGenerated {len(signatures_code)}/{len(all_signatures)} signatures")

        except Exception as e:
            state["errors"].append(
                f"Critical error in signature generation: {str(e)}")
            print(f"✗ Critical error: {str(e)}")
            import traceback
            traceback.print_exc()

        finally:
            # Always set signatures_code in state, even if empty
            state["signatures_code"] = signatures_code
            state["current_stage"] = "signatures_generated"

        return state

    def _build_schema_def(self, state: CompleteTaskGenerationState) -> Optional[Dict[str, Any]]:
        """Build schema_def dict from workflow state for runtime class construction.

        Returns None if any signature is missing its spec (e.g. on validation failure),
        so callers can skip schema_def without crashing.
        """
        try:
            enriched_sigs_map = {
                s["name"]: s
                for s in state["decomposition"].get("signatures", [])
            }

            sig_defs = []
            for sig_code in state.get("signatures_code", []):
                spec = sig_code.get("spec")
                if not spec:
                    logger.warning(
                        "schema_def build skipped: signature '%s' has no spec",
                        sig_code.get("class_name", "?"),
                    )
                    return None
                sig_name = sig_code["signature_name"]
                enriched_sig = enriched_sigs_map.get(sig_name, {})
                sig_def = self.sig_gen.spec_to_sig_def(spec, enriched_sig)
                sig_defs.append(sig_def)

            if not sig_defs:
                return None

            # Fallback structures per signature class name
            fallback_structures: Dict[str, Any] = {}
            for sig_code in state.get("signatures_code", []):
                class_name = sig_code["class_name"]
                sig_name = sig_code["signature_name"]
                enriched_sig = enriched_sigs_map.get(sig_name, {})
                fallback_structures[class_name] = self.mod_gen.create_fallback_structure(enriched_sig)

            # field_name → signature class_name mapping
            field_map: Dict[str, str] = {}
            for sig_code in state.get("signatures_code", []):
                class_name = sig_code["class_name"]
                sig_name = sig_code["signature_name"]
                enriched_sig = enriched_sigs_map.get(sig_name, {})
                for fname in enriched_sig.get("fields", {}).keys():
                    field_map[fname] = class_name

            return {
                "version": 1,
                "schema_name": state["task_name"],
                "task_name": state["task_name"],
                "signatures": sig_defs,
                "pipeline_stages": state["decomposition"].get("pipeline", []),
                "field_to_signature_map": field_map,
                "fallback_structures": fallback_structures,
            }
        except Exception as e:
            logger.warning("Failed to build schema_def: %s", e)
            return None

    def _node_finalize_and_assemble(
        self, state: CompleteTaskGenerationState
    ) -> CompleteTaskGenerationState:
        """Node 13: Assemble final files and prepare result"""
        print(f"\n{'='*70}")
        print(f"STAGE: Finalization")
        print(f"{'='*70}")

        if self.log_callback:
            self.log_callback("📦 Stage: Assembling final code files...", "info")

        try:
            # Check if we have all required components
            if not state.get("signatures_code"):
                raise ValueError("No signatures were generated")

            # Build schema_def for runtime class construction
            schema_def = self._build_schema_def(state)

            # Prepare result
            state["result"] = {
                "success": True,
                "task_name": state["task_name"],
                "field_mapping": state["field_to_signature_map"],
                "decomposition": state["decomposition"],
                "schema_def": schema_def,
                "statistics": {
                    "total_form_fields": len(state["form_data"].get("fields", [])),
                    "signatures": len(state["signatures_code"]),
                    "modules": len(state.get("modules_code", [])),
                    "pipeline_stages": len(state["decomposition"].get("pipeline", [])),
                    "total_attempts": state["attempt"]
                }
            }

            state["status"] = "completed"
            state["current_stage"] = "finalized"

            print(f"✓ Task generation completed successfully")
            print(f"  - {len(state['signatures_code'])} signatures")
            if schema_def:
                print(f"  - schema_def built ({len(schema_def['signatures'])} sig defs)")

        except Exception as e:
            state["errors"].append(f"Finalization failed: {str(e)}")
            state["status"] = "failed"
            state["current_stage"] = "finalization_failed"
            print(f"✗ Finalization failed: {str(e)}")

        return state

    def _route_after_decompose(self, state: CompleteTaskGenerationState) -> str:
        """Routing: After decomposition"""
        if state.get("current_stage") == "decomposition_failed":
            return "finalize"
        return "validate_decomposition"

    def _route_after_decomposition_validation(self, state: CompleteTaskGenerationState) -> str:
        """Routing: After decomposition validation"""
        if state["decomposition_valid"]:
            # Phase 2 B7: auto-tier bypasses human review
            tier = state.get("validation_results", {}).get("review_tier", "normal")
            if state.get("human_review_enabled", False) and tier != "auto":
                return "human_review"
            else:
                if state.get("human_review_enabled") and tier == "auto":
                    state["auto_approved"] = True
                    print(f"  ⚡ Auto-approved (tier=auto) — skipping human review")
                return "generate_signatures"
        else:
            # Retry decomposition if attempts remaining
            if state["attempt"] < state["max_attempts"] - 1:
                state["attempt"] += 1
                return "decompose"
            return "finalize"

    def _route_after_human_review(self, state: CompleteTaskGenerationState) -> str:
        """Routing: After human review (delegates to HumanReviewHandler)"""
        return HumanReviewHandler.route_after_human_review(state)

    def _route_after_signatures(self, state: CompleteTaskGenerationState) -> str:
        """Routing: After signature generation."""
        return "finalize"

    def _build_workflow_graph(self):
        """Build the LangGraph workflow"""
        workflow = StateGraph(SignatureGenerationState)

        # Add nodes
        workflow.add_node("generate", self._node_generate_code)
        workflow.add_node("validate", self._node_validate_code)
        workflow.add_node("human_review", self._node_human_review)
        workflow.add_node("refine", self._node_refine_code)
        workflow.add_node("finalize", self._node_finalize)

        # Set entry point - directly to generate (no more analyze step)
        workflow.set_entry_point("generate")

        # Add edges
        workflow.add_conditional_edges(
            "generate",
            self._should_continue_generation,
            {"validate": "validate", "finalize": "finalize"},
        )
        workflow.add_conditional_edges(
            "validate",
            self._should_refine_or_finish,
            {
                "human_review": "human_review",
                "refine": "refine",
                "finalize": "finalize",
            },
        )
        workflow.add_conditional_edges(
            "human_review",
            self._after_human_review,
            {"refine": "refine", "finalize": "finalize"},
        )
        workflow.add_edge("refine", "generate")
        workflow.add_edge("finalize", END)

        # Compile with checkpointer and optional interrupt
        interrupt_before = ["human_review"] if self.enable_human_review else []
        self.workflow = workflow.compile(
            checkpointer=self.checkpointer, interrupt_before=interrupt_before
        )

        print("Workflow graph compiled successfully")

    def _build_complete_task_workflow(self):
        """Build the LangGraph workflow for complete task generation"""
        workflow = StateGraph(CompleteTaskGenerationState)

        # Add all nodes
        workflow.add_node("decompose", self._node_decompose_form)
        workflow.add_node("validate_decomposition",
                          self._node_validate_decomposition)
        workflow.add_node("human_review", self._node_human_review)
        workflow.add_node("generate_signatures",
                          self._node_generate_signatures)
        workflow.add_node("finalize", self._node_finalize_and_assemble)

        # Set entry point
        workflow.set_entry_point("decompose")

        # Add routing edges
        workflow.add_conditional_edges(
            "decompose",
            self._route_after_decompose,
            {"validate_decomposition": "validate_decomposition", "finalize": "finalize"}
        )

        workflow.add_conditional_edges(
            "validate_decomposition",
            self._route_after_decomposition_validation,
            {
                "generate_signatures": "generate_signatures",
                "human_review": "human_review",
                "decompose": "decompose",
                "finalize": "finalize"
            }
        )

        workflow.add_conditional_edges(
            "human_review",
            self._route_after_human_review,
            {
                "generate_signatures": "generate_signatures",
                "decompose": "decompose"
            }
        )

        workflow.add_edge("generate_signatures", "finalize")
        workflow.add_edge("finalize", END)

        # Compile workflow with interrupt for human review
        interrupt_before = [
            "human_review"] if self.human_review_enabled else []
        self.complete_task_workflow = workflow.compile(
            checkpointer=self.checkpointer,
            interrupt_before=interrupt_before
        )

    def generate_complete_task(
        self,
        form_data: Dict[str, Any],
        task_name: Optional[str] = None,
        max_attempts: int = 3,
        thread_id: str = "default"
    ) -> Dict[str, Any]:
        """
        Generate complete task from a form definition using cognitive decomposition workflow.

        This method uses a sophisticated LangGraph workflow that:
        1. Decomposes the form into atomic signatures based on cognitive behaviors
        2. Generates all signatures and modules
        3. Creates a multi-stage pipeline with proper dependencies
        4. Validates completeness, syntax, semantics, and flow
        5. Refines on errors automatically

        Args:
            form_data: Form specification with name, description, fields
            task_name: Optional task identifier (used for file/module naming)
            max_attempts: Maximum generation attempts if validation fails
            thread_id: Thread ID for workflow state persistence

        Returns:
            dict with:
            - success: bool
            - task_name: str
            - signatures_file: str (complete signatures.py content)
            - modules_file: str (complete modules.py content)
            - field_mapping: dict (field-to-signature mapping)
            - decomposition: dict (decomposition details)
            - validation_results: dict (all validation results)
            - statistics: dict (generation statistics)
        """
        form_name = form_data.get(
            "form_name") or form_data.get("name", "CustomForm")

        # Derive a default task_name if not provided
        if task_name is None:
            task_name = f"dynamic_{sanitize_form_name(form_name)}"

        print(f"GENERATING TASK: {task_name}")

    # Initialize workflow state
        initial_state: CompleteTaskGenerationState = {
            "form_data": form_data,
            "task_name": task_name,
            "thread_id": thread_id,  # Store thread_id in state for Supabase backup
            "max_attempts": max_attempts,
            "decomposition": None,
            "decomposition_valid": False,
            "decomposition_feedback": form_data.get("human_feedback", ""),
            "signatures_code": [],
            "modules_code": [],
            "field_to_signature_map": {},
            "current_stage": "initialized",
            "attempt": 0,
            "errors": [],
            "warnings": [],
            "result": None,
            "status": "in_progress",
            # Human-in-the-loop fields
            "human_review_enabled": self.human_review_enabled,
            "human_feedback": None,
            "human_approved": False,
            "decomposition_summary": None,
        }

        # Run workflow
        config = {"configurable": {"thread_id": thread_id}}

        try:
            # Execute workflow
            print(f"\nExecuting complete task generation workflow...")
            for event in self.complete_task_workflow.stream(initial_state, config):
                # Stream events for monitoring
                pass

            # Get final state
            final_state = self.complete_task_workflow.get_state(config)

            # Check if paused for human review
            if final_state.next and "human_review" in final_state.next:
                print(f"\n{'='*70}")
                print(f"⏸️  WORKFLOW PAUSED FOR HUMAN REVIEW")
                print(f"{'='*70}")

                # Extract decomposition data for the review UI
                decomposition_data = final_state.values.get(
                    "decomposition", {})

                # Validation results
                validation_results = {
                    "passed": final_state.values.get("decomposition_valid", False)
                }

                return {
                    "status": "awaiting_human_review",
                    "thread_id": thread_id,
                    "paused": True,
                    "decomposition_summary": final_state.values.get("decomposition_summary", ""),
                    "task_name": task_name,
                    "decomposition": decomposition_data,
                    "validation_results": validation_results,
                }

            # Extract result
            from pathlib import Path
            from datetime import datetime

            # Write to debug file since stdout is captured by Streamlit
            debug_file = Path("debug_workflow_state.log")
            with open(debug_file, "a") as f:
                f.write(f"\n{'='*60}\n")
                f.write(f"[{datetime.now()}] WORKFLOW FINAL STATE\n")
                f.write(f"Keys in state: {list(final_state.values.keys())}\n")
                f.write(
                    f"Has result: {bool(final_state.values.get('result'))}\n")
                f.write(
                    f"Current stage: {final_state.values.get('current_stage')}\n")
                f.write(
                    f"Number of errors: {len(final_state.values.get('errors', []))}\n")
                if final_state.values.get('errors'):
                    f.write(f"Errors:\n")
                    for i, err in enumerate(final_state.values.get('errors', [])[:10], 1):
                        f.write(f"  {i}. {err}\n")
                f.write(f"{'='*60}\n")

            print(f"\n>>> Checking final state...", flush=True)
            print(
                f">>> final_state.values keys: {list(final_state.values.keys())}", flush=True)
            print(
                f">>> Has result: {bool(final_state.values.get('result'))}", flush=True)
            print(
                f">>> Current stage: {final_state.values.get('current_stage')}", flush=True)
            print(
                f">>> Errors: {final_state.values.get('errors', [])}", flush=True)

            if final_state.values.get("result"):
                result = final_state.values["result"]

                if result.get("success"):
                    print(f"\n{'='*70}")
                    print(f"✓ TASK GENERATION COMPLETED SUCCESSFULLY")
                    print(f"{'='*70}")
                    print(f"  Task: {result['task_name']}")
                    print(
                        f"  Signatures: {result['statistics']['signatures']}")
                    print(
                        f"  Pipeline stages: {result['statistics']['pipeline_stages']}")

                    if final_state.values.get("warnings"):
                        print(
                            f"\n  ⚠ Warnings: {len(final_state.values['warnings'])}")
                        for warning in final_state.values["warnings"][:5]:
                            print(f"    - {warning}")
                else:
                    print(f"\n{'='*70}")
                    print(f"✗ TASK GENERATION FAILED")
                    print(f"{'='*70}")
                    if final_state.values.get("errors"):
                        print(f"  Errors:")
                        for error in final_state.values["errors"][:10]:
                            print(f"    - {error}")

                return result
            else:
                # Workflow didn't complete properly
                errors = final_state.values.get("errors", [])
                current_stage = final_state.values.get(
                    "current_stage", "unknown")

                print(f"\n{'='*70}", flush=True)
                print(f"✗ WORKFLOW DID NOT PRODUCE A RESULT", flush=True)
                print(f"{'='*70}", flush=True)
                print(f"  Current stage: {current_stage}", flush=True)
                print(f"  Number of errors: {len(errors)}", flush=True)
                if errors:
                    print(f"  First 5 errors:", flush=True)
                    for i, error in enumerate(errors[:5], 1):
                        print(f"    {i}. {error}", flush=True)
                print(f"{'='*70}\n", flush=True)

                return {
                    "success": False,
                    "error": f"Workflow did not produce a result. Stage: {current_stage}. Errors: {len(errors)}",
                    "task_name": task_name,
                    "errors": errors,
                    "warnings": final_state.values.get("warnings", []),
                    "current_stage": current_stage,
                }

        except Exception as e:
            print(f"\n✗ Exception during task generation: {str(e)}")
            import traceback
            traceback.print_exc()
            return {
                "success": False,
                "error": str(e),
                "task_name": task_name,
            }

    def generate_from_approved_decomposition(
        self,
        form_data: Dict[str, Any],
        decomposition: Dict[str, Any],
        task_name: str,
        thread_id: str = "default",
    ) -> Dict[str, Any]:
        """
        Skip decomposition and run signature/module generation with a pre-approved decomposition.

        Used when a human has already approved the decomposition in a previous worker invocation
        and MemorySaver state is no longer available.
        """
        initial_state: CompleteTaskGenerationState = {
            "form_data": form_data,
            "task_name": task_name,
            "thread_id": thread_id,
            "max_attempts": 1,
            "decomposition": decomposition,
            "decomposition_valid": True,
            "decomposition_feedback": "",
            "signatures_code": [],
            "modules_code": [],
            "field_to_signature_map": {},
            "current_stage": "decomposition_complete",
            "attempt": 0,
            "errors": [],
            "warnings": [],
            "result": None,
            "status": "in_progress",
            "human_review_enabled": False,  # Skip human review — already approved
            "human_feedback": None,
            "human_approved": True,
            "decomposition_summary": None,
        }

        config = {"configurable": {"thread_id": thread_id}}

        try:
            for _ in self.complete_task_workflow.stream(initial_state, config):
                pass

            final_state = self.complete_task_workflow.get_state(config)

            if final_state.values.get("result"):
                return final_state.values["result"]

            errors = final_state.values.get("errors", [])
            return {
                "success": False,
                "error": f"Workflow did not produce a result. Errors: {errors}",
                "task_name": task_name,
            }
        except Exception as e:
            return {"success": False, "error": str(e), "task_name": task_name}

    def approve_decomposition(self, thread_id: str = "default") -> Dict[str, Any]:
        """
        Approve the decomposition and continue workflow (delegates to HumanReviewHandler).

        Args:
            thread_id: Thread ID of the paused workflow

        Returns:
            Final result dict
        """
        return self.human_review_handler.approve_decomposition(thread_id)

    def reject_decomposition(
        self, feedback: str, thread_id: str = "default"
    ) -> Dict[str, Any]:
        """
        Reject the decomposition with feedback for revision (delegates to HumanReviewHandler).

        Args:
            feedback: Human feedback explaining what needs to change
            thread_id: Thread ID of the paused workflow

        Returns:
            Result dict (may be paused again for another review)
        """
        return self.human_review_handler.reject_decomposition(feedback, thread_id)


__all__ = ["WorkflowOrchestrator"]
