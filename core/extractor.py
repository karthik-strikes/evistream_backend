import asyncio
import json
import traceback
from typing import Dict, List, Any
from utils.helpers.print_helpers import print_extracted_vs_ground_truth, print_field_level_table, print_evaluation_summary
from utils.flatten_json import flatten_json
from schemas.runtime import SchemaRuntime


async def run_async_extraction_and_evaluation(
    markdown_content: str,
    source_file: str,
    one_study_records: List[Dict],
    schema_runtime: SchemaRuntime,
    override: bool = False,
    run_diagnostic: bool = False,
    print_results: bool = False,
    field_level_analysis: bool = False,
    print_field_table: bool = False
):
    """
    Run the complete async extraction and evaluation pipeline.
    1. Extracts records
    2. Aligns + matches
    3. Evaluates
    4. Saves results
    """

    try:
        async_pipeline = schema_runtime.pipeline

        # ---- 1️⃣ Extract ----
        # Pipeline returns dict with all extracted fields
        pipeline_result = await async_pipeline(markdown_content)

        # Use entire dict as result (contains all extracted fields from all stages)
        if isinstance(pipeline_result, dict):
            baseline_results = pipeline_result
        else:
            # Fallback: treat as single result
            baseline_results = pipeline_result

        # Ensure it's a list
        if not isinstance(baseline_results, list):
            baseline_results = [baseline_results]

        # Flatten nested lists if present
        flat_results = []
        for item in baseline_results:
            if isinstance(item, list):
                flat_results.extend(item)
            else:
                flat_results.append(item)
        baseline_results = flat_results

        if print_results:
            print_extracted_vs_ground_truth(
                baseline_results, one_study_records)

        baseline_results = [flatten_json(rec) for rec in baseline_results]
        one_study_records = [flatten_json(rec) for rec in one_study_records]

        # Run evaluation only when ground truth is available
        baseline_evaluation = None
        field_counts = None
        matches = []
        aligned_records = {}

        if one_study_records and any(one_study_records):
            try:
                from core.evaluation import AsyncMedicalExtractionEvaluator
                evaluator = AsyncMedicalExtractionEvaluator(
                    required_fields=list(one_study_records[0].keys()) if one_study_records else [],
                    semantic_fields=[],
                    exact_fields=list(one_study_records[0].keys()) if one_study_records else [],
                    use_semantic=False,
                )
                baseline_evaluation = await evaluator.evaluate(
                    baseline_results, one_study_records
                )
                matches, aligned_records = await evaluator.get_matches_and_aligned_records(
                    baseline_results, one_study_records
                )
                if field_level_analysis:
                    field_counts = await evaluator.calculate_field_counts(
                        baseline_results, one_study_records
                    )
                    if print_field_table:
                        print_field_level_table(field_counts)
                    print_evaluation_summary(baseline_evaluation)
                evaluator.close()
            except Exception as eval_err:
                print(f"⚠️ Evaluation failed (extraction results still valid): {eval_err}")

        return {
            "baseline_results": baseline_results,
            "baseline_evaluation": baseline_evaluation,
            "field_counts": field_counts,
            "matches": matches,
            "aligned_records": aligned_records,
            "files_saved": {}
        }

    except Exception as e:
        print(f"❌ Error in async extraction: {e}")
        traceback.print_exc()
