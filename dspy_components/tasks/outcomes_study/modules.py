import asyncio
import json
from typing import Dict, Any, List

import dspy

from utils.dspy_async import async_dspy_forward
from utils.json_parser import safe_json_parse
from dspy_components.tasks.outcomes_study.signatures import (
    ExtractIndexTests,
    ExtractOutcomeTargetCondition,
    ExtractConfusionMatrixMetrics,
    ExtractSensitivitySpecificity,
    ExtractOutcomesComments,
    CombineOutcomesData,
)


class AsyncIndexTestsExtractor(dspy.Module):
    """Async module to extract all index tests."""

    def __init__(self):
        super().__init__()
        self.extract = dspy.ChainOfThought(ExtractIndexTests)

    async def __call__(self, markdown_content: str) -> List[str]:
        try:
            outputs = await async_dspy_forward(self.extract, markdown_content=markdown_content)
            return safe_json_parse(outputs.get("index_tests_json", "{}"))
        except Exception as e:
            print(f"Error in index tests extraction: {e}")
            return []

    def forward_sync(self, markdown_content: str) -> List[str]:
        result = self.extract(markdown_content=markdown_content)
        return safe_json_parse(result.index_tests_json)


class AsyncOutcomeTargetConditionExtractor(dspy.Module):
    """Async module to extract outcome target condition."""

    def __init__(self):
        super().__init__()
        self.extract = dspy.ChainOfThought(ExtractOutcomeTargetCondition)

    async def __call__(self, markdown_content: str, linking_index_test: str) -> str:
        try:
            outputs = await async_dspy_forward(self.extract, markdown_content=markdown_content, linking_index_test=linking_index_test)
            return outputs.get("outcome_target_condition", "")
        except Exception as e:
            print(f"Error in outcome target condition extraction: {e}")
            return ""

    def forward_sync(self, markdown_content: str, linking_index_test: str) -> str:
        result = self.extract(
            markdown_content=markdown_content,
            linking_index_test=linking_index_test
        )
        return result.outcome_target_condition


class AsyncConfusionMatrixExtractor(dspy.Module):
    """Async module to extract confusion matrix metrics."""

    def __init__(self):
        super().__init__()
        self.extract = dspy.ChainOfThought(ExtractConfusionMatrixMetrics)

    async def __call__(
        self,
        markdown_content: str,
        linking_index_test: str,
        outcome_target_condition: str
    ) -> Dict[str, Any]:
        try:
            outputs = await async_dspy_forward(
                self.extract,
                markdown_content=markdown_content,
                linking_index_test=linking_index_test,
                outcome_target_condition=outcome_target_condition
            )
            return safe_json_parse(outputs.get("confusion_matrix_json", "{}"))
        except Exception as e:
            print(f"Error in confusion matrix extraction: {e}")
            return {
                "true_positives": "NR",
                "false_positives": "NR",
                "false_negatives": "NR",
                "true_negatives": "NR",
            }

    def forward_sync(
        self,
        markdown_content: str,
        linking_index_test: str,
        outcome_target_condition: str
    ) -> Dict[str, Any]:
        result = self.extract(
            markdown_content=markdown_content,
            linking_index_test=linking_index_test,
            outcome_target_condition=outcome_target_condition
        )
        return safe_json_parse(result.confusion_matrix_json)


class AsyncSensitivitySpecificityExtractor(dspy.Module):
    """Async module to extract sensitivity and specificity metrics."""

    def __init__(self):
        super().__init__()
        self.extract = dspy.ChainOfThought(ExtractSensitivitySpecificity)

    async def __call__(
        self,
        markdown_content: str,
        linking_index_test: str,
        outcome_target_condition: str
    ) -> Dict[str, Any]:
        try:
            outputs = await async_dspy_forward(
                self.extract,
                markdown_content=markdown_content,
                linking_index_test=linking_index_test,
                outcome_target_condition=outcome_target_condition
            )
            return safe_json_parse(outputs.get("sensitivity_specificity_json", "{}"))
        except Exception as e:
            print(f"Error in sensitivity/specificity extraction: {e}")
            return {
                "reported_sensitivity": "NR",
                "reported_sensitivity_ci": "NR",
                "reported_specificity": "NR",
                "reported_specificity_ci": "NR",
            }

    def forward_sync(
        self,
        markdown_content: str,
        linking_index_test: str,
        outcome_target_condition: str
    ) -> Dict[str, Any]:
        result = self.extract(
            markdown_content=markdown_content,
            linking_index_test=linking_index_test,
            outcome_target_condition=outcome_target_condition
        )
        return safe_json_parse(result.sensitivity_specificity_json)


class AsyncOutcomesCommentsExtractor(dspy.Module):
    """Async module to extract outcomes comments."""

    def __init__(self):
        super().__init__()
        self.extract = dspy.ChainOfThought(ExtractOutcomesComments)

    async def __call__(
        self,
        markdown_content: str,
        linking_index_test: str,
        outcome_target_condition: str,
        confusion_matrix: Dict[str, Any],
        sensitivity_specificity: Dict[str, Any]
    ) -> str:
        try:
            outputs = await async_dspy_forward(
                self.extract,
                markdown_content=markdown_content,
                linking_index_test=linking_index_test,
                outcome_target_condition=outcome_target_condition,
                confusion_matrix_json=json.dumps(confusion_matrix),
                sensitivity_specificity_json=json.dumps(sensitivity_specificity)
            )
            return outputs.get("outcomes_comment", "")
        except Exception as e:
            print(f"Error in outcomes comments extraction: {e}")
            return ""

    def forward_sync(
        self,
        markdown_content: str,
        linking_index_test: str,
        outcome_target_condition: str,
        confusion_matrix: Dict[str, Any],
        sensitivity_specificity: Dict[str, Any]
    ) -> str:
        result = self.extract(
            markdown_content=markdown_content,
            linking_index_test=linking_index_test,
            outcome_target_condition=outcome_target_condition,
            confusion_matrix_json=json.dumps(confusion_matrix),
            sensitivity_specificity_json=json.dumps(sensitivity_specificity)
        )
        return result.outcomes_comment


class AsyncOutcomesCombiner(dspy.Module):
    """Async module to combine all outcomes data."""

    def __init__(self):
        super().__init__()
        self.combiner = dspy.ChainOfThought(CombineOutcomesData)

    async def __call__(
        self,
        linking_index_test: str,
        outcome_target_condition: str,
        confusion_matrix: Dict[str, Any],
        sensitivity_specificity: Dict[str, Any],
        outcomes_comment: str,
    ) -> Dict[str, Any]:
        try:
            outputs = await async_dspy_forward(
                self.combiner,
                linking_index_test=linking_index_test,
                outcome_target_condition=outcome_target_condition,
                confusion_matrix_json=json.dumps(confusion_matrix),
                sensitivity_specificity_json=json.dumps(sensitivity_specificity),
                outcomes_comment=outcomes_comment,
            )
            return safe_json_parse(outputs.get("complete_outcomes_json", "{}"))
        except Exception as e:
            print(f"Error in combining outcomes: {e}, using fallback merge")
            combined = {
                "linking_index_test": linking_index_test,
                "outcome_target_condition": outcome_target_condition,
                "outcomes_comment": outcomes_comment,
            }
            combined.update(confusion_matrix)
            combined.update(sensitivity_specificity)
            return combined

    def forward_sync(
        self,
        linking_index_test: str,
        outcome_target_condition: str,
        confusion_matrix: Dict[str, Any],
        sensitivity_specificity: Dict[str, Any],
        outcomes_comment: str,
    ) -> Dict[str, Any]:
        result = self.combiner(
            linking_index_test=linking_index_test,
            outcome_target_condition=outcome_target_condition,
            confusion_matrix_json=json.dumps(confusion_matrix),
            sensitivity_specificity_json=json.dumps(sensitivity_specificity),
            outcomes_comment=outcomes_comment,

        )
        return safe_json_parse(result.complete_outcomes_json)


class AsyncOutcomesPipeline(dspy.Module):
    """Complete async pipeline for extracting outcomes data (Multi-Record)."""

    def __init__(self, max_concurrent: int = 5):
        super().__init__()

        self.index_tests_extractor = AsyncIndexTestsExtractor()
        self.target_condition_extractor = AsyncOutcomeTargetConditionExtractor()
        self.confusion_matrix_extractor = AsyncConfusionMatrixExtractor()
        self.sensitivity_specificity_extractor = AsyncSensitivitySpecificityExtractor()
        self.comments_extractor = AsyncOutcomesCommentsExtractor()
        self.combiner = AsyncOutcomesCombiner()

        self.max_concurrent = max_concurrent
        self._semaphore = None

    def _get_semaphore(self):
        if self._semaphore is None:
            self._semaphore = asyncio.Semaphore(self.max_concurrent)
        return self._semaphore

    async def process_single_test(self, markdown_content: str, linking_index_test: str):
        """Process a single index test to extract its outcomes."""
        print(f"  Processing index test: {linking_index_test}...")
        
        # Step 2: Extract target condition (depends on index test)
        outcome_target_condition = await self.target_condition_extractor(
            markdown_content, linking_index_test
        )

        # Step 3: Extract confusion matrix and sensitivity/specificity in parallel
        confusion_matrix_task = self.confusion_matrix_extractor(
            markdown_content, linking_index_test, outcome_target_condition
        )
        sensitivity_specificity_task = self.sensitivity_specificity_extractor(
            markdown_content, linking_index_test, outcome_target_condition
        )

        confusion_matrix, sensitivity_specificity = await asyncio.gather(
            confusion_matrix_task,
            sensitivity_specificity_task
        )

        # Step 4: Extract comments (depends on previous extractions)
        outcomes_comment = await self.comments_extractor(
            markdown_content,
            linking_index_test,
            outcome_target_condition,
            confusion_matrix,
            sensitivity_specificity
        )

        # Step 5: Combine all data
        complete_outcomes = await self.combiner(
            linking_index_test,
            outcome_target_condition,
            confusion_matrix,
            sensitivity_specificity,
            outcomes_comment,
        )
        
        return complete_outcomes

    async def forward(self, markdown_content: str):
        # Step 1: Extract ALL index tests
        print("  [1/2] Extracting all index tests...")
        index_tests = await self.index_tests_extractor(markdown_content)
        
        if not index_tests:
            print("  ⚠️ No index tests found. Attempting fallback extraction...")
            # Fallback: Try to extract a single generic test if list is empty
            index_tests = ["Clear Response"]
        
        print(f"  ✓ Found {len(index_tests)} index tests: {index_tests}")

        # Step 2: Process each test in parallel
        print(f"  [2/2] Processing {len(index_tests)} index tests in parallel...")
        
        tasks = [self.process_single_test(markdown_content, test_name) for test_name in index_tests]
        results = await asyncio.gather(*tasks)
        
        print(f"  ✓ Extracted {len(results)} outcome records!")

        return dspy.Prediction(
            extracted_records=results, # Standard field for list of records
            success=True
        )

    async def __call__(self, markdown_content: str):
        return await self.forward(markdown_content)


class SyncOutcomesPipeline(dspy.Module):
    """Synchronous wrapper for async outcomes pipeline."""

    def __init__(self):
        super().__init__()
        self.async_pipeline = AsyncOutcomesPipeline()

        # Expose extractors for optimizer access
        self.index_tests_extractor = self.async_pipeline.index_tests_extractor
        self.target_condition_extractor = self.async_pipeline.target_condition_extractor
        self.confusion_matrix_extractor = self.async_pipeline.confusion_matrix_extractor
        self.sensitivity_specificity_extractor = self.async_pipeline.sensitivity_specificity_extractor
        self.comments_extractor = self.async_pipeline.comments_extractor
        self.combiner = self.async_pipeline.combiner

    def forward(self, markdown_content: str):
        try:
            loop = asyncio.get_running_loop()
            if loop.is_running():
                try:
                    import nest_asyncio
                    nest_asyncio.apply()
                except ImportError:
                    raise ImportError(
                        "Please install nest_asyncio: pip install nest_asyncio")
        except RuntimeError:
            pass

        self.async_pipeline._semaphore = None
        result = asyncio.run(self.async_pipeline(markdown_content))
        return result

    def __deepcopy__(self, memo):
        return SyncOutcomesPipeline()


class AsyncMultipleOutcomesExtractor(dspy.Module):
    """Extract multiple outcomes from a single paper (for studies with multiple test arms)."""

    def __init__(self, max_concurrent: int = 3):
        super().__init__()
        self.single_outcome_pipeline = AsyncOutcomesPipeline(max_concurrent)
        self.max_concurrent = max_concurrent

    async def forward(self, markdown_content: str, num_outcomes: int = 1) -> List[Dict[str, Any]]:
        """
        Extract multiple outcomes from a single paper.
        
        Args:
            markdown_content: Full paper content
            num_outcomes: Number of distinct outcomes to extract (default 1)
        
        Returns:
            List of outcome dictionaries
        """
        # Create tasks for each outcome extraction
        tasks = [
            self.single_outcome_pipeline(markdown_content)
            for _ in range(num_outcomes)
        ]

        # Execute in parallel with concurrency control
        results = await asyncio.gather(*tasks)

        # Extract records from each result - pipeline returns 'extracted_records' field
        outcome_records = []
        for result in results:
            if result.success and hasattr(result, 'extracted_records'):
                if isinstance(result.extracted_records, list):
                    outcome_records.extend(result.extracted_records)
                else:
                    outcome_records.append(result.extracted_records)
        return outcome_records

    async def __call__(self, markdown_content: str, num_outcomes: int = 1):
        return await self.forward(markdown_content, num_outcomes)


__all__ = [
    "AsyncIndexTestsExtractor",
    "AsyncOutcomeTargetConditionExtractor",
    "AsyncConfusionMatrixExtractor",
    "AsyncSensitivitySpecificityExtractor",
    "AsyncOutcomesCommentsExtractor",
    "AsyncOutcomesCombiner",
    "AsyncOutcomesPipeline",
    "SyncOutcomesPipeline",
    "AsyncMultipleOutcomesExtractor",
]
