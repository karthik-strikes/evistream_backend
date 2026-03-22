"""
Decomposition Validation Module

Centralized validation logic for form decomposition results.
Performs comprehensive checks on:
- Field coverage completeness
- Duplicate field assignments
- DAG dependency validation (cycles, orphaned dependencies)
- Pipeline flow integrity
"""

import logging
from typing import Dict, Any, List, Tuple, Optional
from collections import deque

logger = logging.getLogger(__name__)


def _topological_sort(
    dependency_graph: Dict[str, List[str]],
    output_providers: Dict[str, str]
) -> List[str]:
    """
    Perform topological sort to find valid execution order.

    Args:
        dependency_graph: sig_name -> list of fields it needs
        output_providers: field_name -> sig_name that produces it

    Returns:
        List of signature names in valid execution order

    Raises:
        ValueError: If graph has cycles or disconnected components
    """
    # Build adjacency list: sig_name -> list of sigs that depend on it
    in_degree = {sig: 0 for sig in dependency_graph}
    adj_list = {sig: [] for sig in dependency_graph}

    for sig_name, needed_fields in dependency_graph.items():
        for field in needed_fields:
            provider = output_providers.get(field)
            if provider and provider in dependency_graph:
                adj_list[provider].append(sig_name)
                in_degree[sig_name] += 1

    # Kahn's algorithm
    queue = deque([sig for sig, degree in in_degree.items() if degree == 0])
    result = []

    while queue:
        current = queue.popleft()
        result.append(current)

        for dependent in adj_list[current]:
            in_degree[dependent] -= 1
            if in_degree[dependent] == 0:
                queue.append(dependent)

    if len(result) != len(dependency_graph):
        raise ValueError("Graph has cycles or disconnected components")

    return result


def validate_dag_dependencies(
    signatures: List[Dict[str, Any]]
) -> Tuple[bool, List[str]]:
    """
    Validate that signature dependencies form a valid DAG with no cycles.

    Args:
        signatures: List of signature specifications (new simplified format)

    Returns:
        Tuple of (is_valid, list_of_issues)
    """
    issues = []

    if not signatures:
        return True, issues

    # Build dependency graph: signature_name -> list of required fields
    dependency_graph = {}
    output_providers = {}  # field_name -> signature_name that produces it

    for sig in signatures:
        sig_name = sig.get("name", "<unnamed>")
        fields = sig.get("fields", {})
        depends_on = sig.get("depends_on", [])

        # What individual fields does this signature produce?
        for field_name in fields.keys():
            if field_name in output_providers:
                issues.append(
                    f"Multiple signatures produce field '{field_name}': "
                    f"{output_providers[field_name]} and {sig_name}"
                )
            output_providers[field_name] = sig_name

        # What does this signature need as context?
        dependency_graph[sig_name] = depends_on

    # Check 1: All required fields are provided by some signature
    for sig_name, needed_fields in dependency_graph.items():
        for field in needed_fields:
            if field not in output_providers:
                issues.append(
                    f"Signature '{sig_name}' requires field '{field}' "
                    f"but no signature produces it"
                )

    # Check 2: Detect circular dependencies using DFS
    visited = set()
    rec_stack = set()

    def has_cycle(sig_name: str, path: List[str]) -> Optional[List[str]]:
        """DFS to detect cycles. Returns cycle path if found."""
        if sig_name in rec_stack:
            # Found a cycle
            try:
                cycle_start = path.index(sig_name)
                return path[cycle_start:] + [sig_name]
            except ValueError:
                return path + [sig_name]

        if sig_name in visited:
            return None

        visited.add(sig_name)
        rec_stack.add(sig_name)

        # Get dependencies (convert field names to sig names)
        needed_fields = dependency_graph.get(sig_name, [])
        for field in needed_fields:
            provider_sig = output_providers.get(field)
            if provider_sig:
                cycle = has_cycle(provider_sig, path + [sig_name])
                if cycle:
                    return cycle

        rec_stack.remove(sig_name)
        return None

    # Check all signatures for cycles
    for sig_name in dependency_graph:
        if sig_name not in visited:
            cycle = has_cycle(sig_name, [])
            if cycle:
                issues.append(
                    f"Circular dependency detected: {' -> '.join(cycle)}"
                )
                break

    # Check 3: Verify topological sort is possible
    if not issues:
        try:
            execution_order = _topological_sort(
                dependency_graph, output_providers)
            logger.debug(f"Valid execution order: {execution_order}")
        except ValueError as e:
            issues.append(f"Cannot determine execution order: {str(e)}")

    return len(issues) == 0, issues


def detect_duplicate_field_assignments(
    signatures: List[Dict[str, Any]]
) -> Tuple[bool, Dict[str, List[str]]]:
    """
    Detect if any field is assigned to multiple signatures.

    Args:
        signatures: List of signature specifications (new simplified format)

    Returns:
        Tuple of (has_duplicates, dict of field_name -> list of signature names)
    """
    field_assignments: Dict[str, List[str]] = {}

    for sig in signatures:
        sig_name = sig.get("name", "<unnamed>")
        fields = sig.get("fields", {})

        for field_name in fields.keys():
            if field_name not in field_assignments:
                field_assignments[field_name] = []
            field_assignments[field_name].append(sig_name)

    # Find duplicates
    duplicates = {
        field: sigs for field, sigs in field_assignments.items()
        if len(sigs) > 1
    }

    return len(duplicates) > 0, duplicates


def validate_pipeline_stages(
    pipeline: List[Dict[str, Any]],
    signatures: List[Dict[str, Any]]
) -> Tuple[bool, List[str]]:
    """
    Validate pipeline stage structure and dependencies.

    Args:
        pipeline: List of pipeline stages
        signatures: List of signature specifications

    Returns:
        Tuple of (is_valid, list_of_issues)
    """
    issues = []

    if not pipeline:
        issues.append("Pipeline is empty")
        return False, issues

    # Get all signature names
    all_sig_names = {sig.get("name") for sig in signatures}

    # Check each stage
    stage_numbers = set()
    for stage in pipeline:
        stage_num = stage.get("stage")
        stage_sigs = stage.get("signatures", [])

        # Check stage number is unique
        if stage_num in stage_numbers:
            issues.append(f"Duplicate stage number: {stage_num}")
        stage_numbers.add(stage_num)

        # Check all signatures in stage exist
        for sig_name in stage_sigs:
            if sig_name not in all_sig_names:
                issues.append(
                    f"Stage {stage_num} references unknown signature: {sig_name}"
                )

        # Check waits_for_stage exists (if specified)
        waits_for = stage.get("waits_for_stage")
        if waits_for is not None:
            if waits_for not in stage_numbers and waits_for != stage_num:
                # Only issue warning, not error (stage might be defined later in list)
                logger.debug(
                    f"Stage {stage_num} waits for stage {waits_for} which hasn't been seen yet"
                )

    # Check all signatures are assigned to a stage
    all_stage_sigs = set()
    for stage in pipeline:
        all_stage_sigs.update(stage.get("signatures", []))

    missing_from_stages = all_sig_names - all_stage_sigs
    if missing_from_stages:
        issues.append(
            f"Signatures not assigned to any stage: {list(missing_from_stages)}"
        )

    # Check no signature appears in multiple stages
    sig_stage_count = {}
    for stage in pipeline:
        for sig_name in stage.get("signatures", []):
            sig_stage_count[sig_name] = sig_stage_count.get(sig_name, 0) + 1

    duplicates = {sig: count for sig,
                  count in sig_stage_count.items() if count > 1}
    if duplicates:
        issues.append(
            f"Signatures appear in multiple stages: {duplicates}"
        )

    return len(issues) == 0, issues


class DecompositionValidator:
    """
    Comprehensive validator for form decomposition results.

    Validates the new simplified decomposition format with:
    - Field coverage completeness
    - No duplicate field assignments
    - Valid DAG dependencies
    - Proper pipeline flow
    """

    def validate_complete_decomposition(
        self,
        decomposition: Dict[str, Any],
        form_data: Dict[str, Any]
    ) -> Tuple[bool, Dict[str, Any]]:
        """
        Run all validation checks on a decomposition.

        Args:
            decomposition: The decomposition result (new simplified format)
            form_data: Original form specification

        Returns:
            Tuple of (is_valid, validation_results_dict)
        """
        all_issues = []
        validation_results = {
            "passed": False,
            "issues": all_issues,
        }

        # Extract data from decomposition
        signatures = decomposition.get("signatures", []) or []
        pipeline = decomposition.get("pipeline", []) or []
        field_coverage = decomposition.get("field_coverage", {}) or {}

        # Get form fields
        form_fields = set(
            f.get("field_name") or f.get("name")
            for f in form_data.get("fields", [])
            if f.get("field_name") or f.get("name")
        )

        covered_fields = set(field_coverage.keys())
        missing_fields = sorted(list(form_fields - covered_fields))
        extra_fields = sorted(list(covered_fields - form_fields))

        all_fields_covered = len(missing_fields) == 0

        coverage_validation = {
            "total_form_fields": len(form_fields),
            "fields_covered": len(covered_fields & form_fields),
            "all_fields_covered": all_fields_covered,
            "missing_fields": missing_fields,
            "extra_fields": extra_fields,
            "coverage_map": field_coverage
        }
        validation_results["field_coverage"] = coverage_validation

        if missing_fields:
            all_issues.append(f"Missing fields: {missing_fields}")
            logger.warning(
                f"Missing fields not covered by any signature: {missing_fields}"
            )

        if extra_fields:
            logger.warning(
                f"Extra fields in decomposition not in form: {extra_fields}"
            )

        has_duplicates, duplicates = detect_duplicate_field_assignments(
            signatures
        )

        if has_duplicates:
            validation_results["duplicate_assignments"] = duplicates
            for field, sigs in duplicates.items():
                issue = f"Field '{field}' assigned to multiple signatures: {sigs}"
                all_issues.append(issue)
                logger.warning(issue)

        dag_valid, dag_issues = validate_dag_dependencies(signatures)

        validation_results["dag_validation"] = {
            "passed": dag_valid,
            "issues": dag_issues,
            "no_circular_dependencies": not any(
                "Circular dependency" in issue for issue in dag_issues
            )
        }

        if not dag_valid:
            all_issues.extend(dag_issues)
            logger.warning(f"DAG validation issues: {dag_issues}")

        pipeline_valid, pipeline_issues = validate_pipeline_stages(
            pipeline, signatures
        )

        validation_results["pipeline_validation"] = {
            "passed": pipeline_valid,
            "issues": pipeline_issues
        }

        if not pipeline_valid:
            all_issues.extend(pipeline_issues)
            logger.warning(f"Pipeline validation issues: {pipeline_issues}")

        validation_results["passed"] = len(all_issues) == 0

        if validation_results["passed"]:
            logger.info("✓ Decomposition validation passed all checks")
        else:
            logger.warning(
                f"✗ Decomposition validation failed with {len(all_issues)} issues"
            )

        return validation_results["passed"], validation_results


__all__ = [
    "DecompositionValidator",
    "validate_dag_dependencies",
    "detect_duplicate_field_assignments",
    "validate_pipeline_stages",
]
