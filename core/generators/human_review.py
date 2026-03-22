"""
Human-in-the-Loop Review Module

Provides human review capabilities for decomposition validation:
- Present decomposition summary to humans
- Collect approval/rejection feedback
- Resume workflow with human input
"""

import copy
import asyncio
from typing import Dict, Any
from .models import CompleteTaskGenerationState
from utils.supabase_client import get_supabase_client


class HumanReviewHandler:
    """
    Handles human review of decomposition results.

    Provides methods to:
    - Generate human-readable summaries
    - Pause workflow for review
    - Resume workflow with approval or rejection
    """

    def __init__(self, complete_task_workflow):
        """
        Initialize human review handler.

        Args:
            complete_task_workflow: The compiled LangGraph workflow
        """
        self.complete_task_workflow = complete_task_workflow

    def generate_decomposition_summary(
        self, state: CompleteTaskGenerationState
    ) -> str:
        """
        Generate human-readable summary of decomposition.

        Args:
            state: Current workflow state

        Returns:
            Formatted summary string
        """
        decomp = state["decomposition"]
        validation = state.get("validation_results", {})

        summary = []
        summary.append("\n📋 DECOMPOSITION SUMMARY")
        summary.append("=" * 70)

        # Form info
        form_name = state["form_data"].get("form_name", "Unknown Form")
        total_fields = len(state["form_data"].get("fields", []))
        summary.append(f"\n📄 Form: {form_name}")
        summary.append(f"   Total Fields: {total_fields}")

        signatures = decomp.get("signatures", [])
        summary.append(f"\n🔹 Signatures: {len(signatures)}")

        for i, sig in enumerate(signatures, 1):
            sig_name = sig.get("name", f"Signature{i}")
            fields = list(sig.get("fields", {}).keys())
            depends_on = sig.get("depends_on", [])

            summary.append(f"\n  {i}. {sig_name}")
            summary.append(f"     Fields ({len(fields)}): {', '.join(fields[:5])}" +
                           (f" (+{len(fields)-5} more)" if len(fields) > 5 else ""))

            if depends_on:
                summary.append(f"     Depends on: {', '.join(depends_on[:5])}" +
                               (f" (+{len(depends_on)-5} more)" if len(depends_on) > 5 else ""))

        pipeline = decomp.get("pipeline", [])
        summary.append(f"\n🔄 Pipeline: {len(pipeline)} stages")

        for stage in pipeline:
            stage_num = stage.get("stage", "?")
            stage_sigs = stage.get("signatures", [])
            execution = stage.get("execution", "unknown")
            waits_for = stage.get("waits_for_stage")

            summary.append(f"\n  Stage {stage_num} ({execution}):")
            summary.append(f"    Signatures: {', '.join(stage_sigs)}")
            if waits_for:
                summary.append(f"    Waits for: Stage {waits_for}")

        field_coverage = decomp.get("field_coverage", {})
        summary.append(f"\n📊 Field Coverage:")
        summary.append(
            f"   Total: {len(field_coverage)}/{total_fields} fields mapped")

        summary.append(f"\n✅ Validation Results:")

        if validation.get("passed"):
            summary.append(f"   Status: ✓ All checks passed")

            field_cov = validation.get("field_coverage", {})
            summary.append(
                f"   • Field coverage: {field_cov.get('fields_covered')}/{field_cov.get('total_form_fields')}")
            summary.append(f"   • DAG validation: ✓ No circular dependencies")
            summary.append(f"   • Pipeline validation: ✓ Structure valid")
        else:
            issues = validation.get("issues", [])
            summary.append(f"   Status: ✗ {len(issues)} issues found")
            for issue in issues[:3]:
                summary.append(f"    • {issue}")
            if len(issues) > 3:
                summary.append(f"    ... and {len(issues)-3} more")

        summary.append("\n" + "=" * 70)
        return "\n".join(summary)

    def node_human_review(
        self, state: CompleteTaskGenerationState
    ) -> CompleteTaskGenerationState:
        """
        Node: Present decomposition to human for review and approval.

        Args:
            state: Current workflow state

        Returns:
            Updated state with summary and awaiting status
        """
        print(f"\n{'='*70}")
        print(f"STAGE: Human Review")
        print(f"{'='*70}")

        # Generate human-readable summary
        summary = self.generate_decomposition_summary(state)
        state["decomposition_summary"] = summary

        # BACKUP STATE TO SUPABASE before pausing
        thread_id = state.get("thread_id", state.get("task_name", "unknown"))

        supabase = get_supabase_client()
        if supabase and supabase.is_available():
            # Run async save in sync context
            try:
                asyncio.run(supabase.save_workflow_state(
                    thread_id=thread_id,
                    workflow_state=dict(state),
                    metadata={
                        "stage": "human_review",
                        "task_name": state.get("task_name"),
                        "form_name": state.get("form_data", {}).get("form_name")
                    }
                ))
                print(
                    f"✓ Workflow state backed up to Supabase (thread: {thread_id})")
            except Exception as e:
                print(f"❌ FAILED to backup state to Supabase: {e}")
                import traceback
                traceback.print_exc()
        else:
            print("❌ Supabase client not available - cannot save state!")

        # Print summary for human
        print(summary)

        print("\n" + "="*70)
        print("⏸️  WORKFLOW PAUSED FOR HUMAN REVIEW")
        print("="*70)
        print("\n📝 Review the decomposition above and decide:")
        print("\n✅ To APPROVE (if decomposition looks correct):")
        print("   orchestrator.approve_decomposition(thread_id)")
        print("\n❌ To REJECT (if changes needed):")
        print("   orchestrator.reject_decomposition(feedback, thread_id)")
        print("\n💡 Example feedback:")
        print('   "Field X should be grouped with Y, not Z"')
        print('   "Signature A should depend on field B"')
        print('   "Stage 2 should run in parallel, not sequential"')
        print("\n" + "="*70)

        state["current_stage"] = "awaiting_human_review"
        return state

    def approve_decomposition(self, thread_id: str = "default") -> Dict[str, Any]:
        """
        Approve the decomposition and continue workflow.

        Args:
            thread_id: Thread ID of the paused workflow

        Returns:
            Final result dict
        """
        print(f"\n✅ Human approved decomposition for thread: {thread_id}")
        print("   Continuing workflow...\n")

        config = {"configurable": {"thread_id": thread_id}}

        # RETRIEVE STATE FROM SUPABASE (bypassing broken LangGraph checkpoint)
        supabase = get_supabase_client()

        if not supabase or not supabase.is_available():
            return {
                "success": False,
                "error": "Supabase not available - cannot retrieve workflow state"
            }

        # Get state from Supabase
        saved_state = supabase.get_workflow_state(thread_id)

        if not saved_state:
            return {
                "success": False,
                "error": f"No saved state found in Supabase for thread_id: {thread_id}"
            }

        print(
            f"✓ Retrieved state from Supabase with keys: {list(saved_state.keys())}")

        updated_state = copy.deepcopy(saved_state)

        # Set approval flags
        updated_state["human_approved"] = True
        updated_state["human_feedback"] = None

        # Resume workflow with updated state
        return self._resume_workflow_with_state(updated_state, config)

    def reject_decomposition(
        self, feedback: str, thread_id: str = "default"
    ) -> Dict[str, Any]:
        """
        Reject the decomposition with feedback for refinement.

        Args:
            feedback: Human feedback explaining what needs to change
            thread_id: Thread ID of the paused workflow

        Returns:
            Result dict (may be paused again for another review)
        """
        print(f"\n❌ Human rejected decomposition for thread: {thread_id}")
        print(f"   Feedback: {feedback}")
        print("   Re-attempting decomposition with feedback...\n")

        config = {"configurable": {"thread_id": thread_id}}

        # Get current state to enrich feedback
        current_state = self.complete_task_workflow.get_state(config)

        if not current_state or not hasattr(current_state, 'values'):
            return {
                "success": False,
                "error": "No state found for thread_id"
            }

        enriched_feedback = feedback

        current_attempt = current_state.values.get("attempt", 0)
        max_attempts = current_state.values.get("max_attempts", 3)

        if current_attempt >= max_attempts - 1:
            print(
                f"⚠️  WARNING: Already at attempt {current_attempt + 1}/{max_attempts}")
            print("   This will be the final retry attempt.")

        validation_results = current_state.values.get(
            "validation_results", {})

        if validation_results:
            issues = validation_results.get("issues", [])
            if issues:
                enriched_feedback += "\n\n=== Validation Issues Found ==="
                for i, issue in enumerate(issues, 1):
                    enriched_feedback += f"\n{i}. {issue}"

            # Add specific field coverage info
            field_coverage = validation_results.get("field_coverage", {})
            if not field_coverage.get("all_fields_covered", True):
                missing_fields = field_coverage.get("missing_fields", [])
                enriched_feedback += f"\n\n=== Missing Fields ===\n{', '.join(missing_fields)}"

        updated_state = copy.deepcopy(current_state.values)

        # Debug: Verify critical keys
        critical_keys = ["decomposition", "form_data"]
        missing_keys = [
            key for key in critical_keys if key not in updated_state]

        if missing_keys:
            print(
                f"⚠️  WARNING: Critical keys missing from state: {missing_keys}")
            print(f"   Available keys: {list(updated_state.keys())}")
            return {
                "success": False,
                "error": f"Critical keys missing from state: {missing_keys}"
            }

        updated_state["human_approved"] = False
        updated_state["human_feedback"] = enriched_feedback
        updated_state["decomposition_feedback"] = enriched_feedback

        return self._resume_workflow_with_state(updated_state, config)

    def _resume_workflow_with_state(self, updated_state: Dict[str, Any], config: dict) -> Dict[str, Any]:
        """
        Helper to resume workflow after human review with updated state.

        Args:
            updated_state: Complete state dict with deepcopy to preserve all fields
            config: LangGraph config with thread_id

        Returns:
            Result dict (may indicate another pause or final result)
        """
        try:
            # Debug logging
            print(
                f"📝 Resuming workflow with state keys: {list(updated_state.keys())}")

            # Update state with ALL values from Supabase backup
            self.complete_task_workflow.update_state(
                config,
                updated_state,
                as_node="human_review"
            )

            print("✅ State updated in LangGraph from Supabase backup")

            # Resume workflow from the last checkpoint (no new input needed)
            for _ in self.complete_task_workflow.stream(None, config):
                pass

            final_state = self.complete_task_workflow.get_state(config)

            # If we paused again for human review, surface decomposition + validation
            if final_state.next and "human_review" in final_state.next:
                values = final_state.values
                return {
                    "status": "awaiting_human_review",
                    "thread_id": config["configurable"]["thread_id"],
                    "paused": True,
                    "decomposition_summary": values.get(
                        "decomposition_summary", ""
                    ),
                    "decomposition": values.get("decomposition"),
                    "validation_results": {
                        "coverage": values.get("coverage_validation", {}),
                        "flow": values.get("flow_validation", {}),
                        "passed": values.get("decomposition_valid", False),
                        "issues": values.get("validation_results", {}).get("issues", [])
                    },
                }

            # Otherwise, return the final result from the workflow
            values = final_state.values
            return values.get(
                "result",
                {
                    "success": False,
                    "error": "Workflow did not produce result",
                },
            )
        except Exception as e:
            import traceback

            error_details = traceback.format_exc()
            print(f"❌ Error resuming workflow: {error_details}")
            return {
                "success": False,
                "error": f"Resume failed: {str(e)}",
                "details": error_details,
            }

    @staticmethod
    def route_after_human_review(state: CompleteTaskGenerationState) -> str:
        """
        Routing function: After human review.

        Args:
            state: Current workflow state

        Returns:
            Next node name
        """
        if state.get("human_approved", False):
            return "generate_signatures"
        return "decompose"


__all__ = ["HumanReviewHandler"]
