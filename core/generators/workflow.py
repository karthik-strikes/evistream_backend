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
from .task_utils import sanitize_form_name, create_task_directory, sanitize_field_key
from config.models import CODEGEN_SIGNATURE_MODEL


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

            for idx, enriched_sig in enumerate(all_signatures, 1):
                sig_name = enriched_sig.get("name", f"Signature{idx}")
                print(f"\n[{idx}/{len(all_signatures)}] {sig_name}")

                try:
                    result = self.sig_gen.generate_signature(enriched_sig)

                    if result["is_valid"]:
                        class_name = sanitize_form_name(sig_name)
                        fields = enriched_sig.get("fields", {})
                        output_field = list(fields.keys())[0] if fields else "output"

                        signatures_code.append({
                            "signature_name": sig_name,
                            "class_name": class_name,
                            "code": result["code"],
                            "output_field": output_field,
                            "requires_context": bool(enriched_sig.get("depends_on")),
                            "context_fields": enriched_sig.get("depends_on", [])
                        })
                        print(f"  ✓ Generated")
                    else:
                        errors = result.get('errors', [])
                        state["errors"].append(
                            f"Failed to generate {sig_name}: {errors}")
                        print(f"  ✗ Generation failed")
                        for error in errors:
                            print(f"     • {error}")

                except Exception as e:
                    state["errors"].append(
                        f"Error generating {sig_name}: {str(e)}")
                    print(f"  ✗ Error: {str(e)}")
                    import traceback
                    traceback.print_exc()

                # Add delay to avoid rate limiting (except after last signature)
                if idx < len(all_signatures):
                    import time
                    print(f"Waiting 6 seconds to avoid rate limit...")
                    time.sleep(6)

            print(
                f"\nGenerated {len(signatures_code)}/{len(all_signatures)} signatures")

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

    def _node_generate_modules(
        self, state: CompleteTaskGenerationState
    ) -> CompleteTaskGenerationState:
        """Node 4: Generate modules for signatures"""
        print(f"\n{'='*70}")
        print(f"STAGE: Generating Modules")
        print(f"{'='*70}")

        if self.log_callback:
            self.log_callback("⚙️ Stage: Generating extractor modules...", "info")

        # Check if signatures were generated
        if "signatures_code" not in state or not state["signatures_code"]:
            error_msg = "No signatures available for module generation"
            state["errors"].append(error_msg)
            state["modules_code"] = []
            state["current_stage"] = "modules_failed"
            print(f"✗ {error_msg}")
            return state

        modules_code = []

        for idx, sig_code in enumerate(state["signatures_code"], 1):
            sig_name = sig_code["signature_name"]
            print(
                f"\n[{idx}/{len(state['signatures_code'])}] Module for {sig_name}")

            try:
                all_sigs = state["decomposition"].get("signatures", [])
                enriched_sig = next(
                    (s for s in all_sigs if s.get("name") == sig_name), {})

                fallback = self.mod_gen.create_fallback_structure(enriched_sig)

                result = self.mod_gen.generate_module(
                    sig_code["class_name"],
                    sig_code["output_field"],
                    fallback,
                    requires_fields=sig_code.get("context_fields", []),
                )

                if result["is_valid"]:
                    modules_code.append(result["code"])
                    print(f"  ✓ Module generated")
                else:
                    errors = result.get('errors', [])
                    state["errors"].append(
                        f"Failed to generate module for {sig_name}: {errors}")
                    print(f"  ✗ Module generation failed")
                    for error in errors:
                        print(f"     • {error}")

            except Exception as e:
                state["errors"].append(
                    f"Error generating module for {sig_name}: {str(e)}")
                print(f"  ✗ Error: {str(e)}")

        state["modules_code"] = modules_code
        state["current_stage"] = "modules_generated"

        print(f"\nGenerated {len(modules_code)} modules")

        if self.log_callback:
            self.log_callback(f"✓ Generated {len(modules_code)} extractor modules", "success")

        return state

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

            # Assemble signatures file using SignatureGenerator
            signatures_file = self.sig_gen.assemble_signatures_file(
                state["signatures_code"],
                state["task_name"]
            )

            # Collect signature class names for imports
            signature_class_names = []
            for sig_code in state["signatures_code"]:
                if "class_name" in sig_code:
                    signature_class_names.append(sig_code["class_name"])

            # Assemble modules file using ModuleGenerator
            modules_file = self.mod_gen.assemble_modules_file(
                state["modules_code"],
                state["task_name"],
                signature_class_names
            )

            # Prepare result
            state["result"] = {
                "success": True,
                "task_name": state["task_name"],
                "signatures_file": signatures_file,
                "modules_file": modules_file,
                "field_mapping": state["field_to_signature_map"],
                "decomposition": state["decomposition"],
                "statistics": {
                    "total_form_fields": len(state["form_data"].get("fields", [])),
                    "signatures": len(state["signatures_code"]),
                    "modules": len(state["modules_code"]),
                    "pipeline_stages": len(state["decomposition"].get("pipeline", [])),
                    "total_attempts": state["attempt"]
                }
            }

            state["status"] = "completed"
            state["current_stage"] = "finalized"

            print(f"✓ Task generation completed successfully")
            print(f"  - {len(state['signatures_code'])} signatures")
            print(f"  - {len(state['modules_code'])} modules")

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
            # Check if human review is enabled
            if state.get("human_review_enabled", False):
                return "human_review"
            else:
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
        """Routing: After signature generation"""
        # Generate modules if any signatures were successfully generated
        # Even if there are some errors, we should generate modules for successful signatures
        if state.get("signatures_code") and len(state["signatures_code"]) > 0:
            return "generate_modules"
        # Only skip modules if no signatures were generated at all
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
        workflow.add_node("generate_modules",
                          self._node_generate_modules)
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

        workflow.add_conditional_edges(
            "generate_signatures",
            self._route_after_signatures,
            {
                "generate_modules": "generate_modules",
                "finalize": "finalize"
            }
        )

        workflow.add_edge("generate_modules", "finalize")
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
