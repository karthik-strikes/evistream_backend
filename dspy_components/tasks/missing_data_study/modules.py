import asyncio
import json
from typing import Dict, Any

import dspy

from utils.dspy_async import async_dspy_forward
from utils.json_parser import safe_json_parse
from dspy_components.tasks.missing_data_study.signatures import (
    ExtractPatientsPartialVerification,
    ExtractLesionsPartialVerification,
    ExtractTimeInterval,
    CombineMissingData,
)


class AsyncPatientsPartialVerificationExtractor(dspy.Module):
    """Async module to extract patient-level partial verification data."""

    def __init__(self):
        super().__init__()
        self.extract = dspy.ChainOfThought(ExtractPatientsPartialVerification)

    async def __call__(self, markdown_content: str) -> Dict[str, Any]:
        try:
            outputs = await async_dspy_forward(self.extract, markdown_content=markdown_content)
            return safe_json_parse(outputs.get("patients_partial_verification_json", "{}"))
        except Exception as e:
            print(f"Error in patients partial verification extraction: {e}")
            return {
                "num_patients_received_index_test_but_not_reference_standard": "NR",
                "num_patients_received_reference_standard_but_not_index_test": "NR",
            }

    def forward_sync(self, markdown_content: str) -> Dict[str, Any]:
        result = self.extract(markdown_content=markdown_content)
        return safe_json_parse(result.patients_partial_verification_json)


class AsyncLesionsPartialVerificationExtractor(dspy.Module):
    """Async module to extract lesion-level partial verification data."""

    def __init__(self):
        super().__init__()
        self.extract = dspy.ChainOfThought(ExtractLesionsPartialVerification)

    async def __call__(self, markdown_content: str) -> Dict[str, Any]:
        try:
            outputs = await async_dspy_forward(self.extract, markdown_content=markdown_content)
            return safe_json_parse(outputs.get("lesions_partial_verification_json", "{}"))
        except Exception as e:
            print(f"Error in lesions partial verification extraction: {e}")
            return {
                "num_lesions_received_index_test_but_not_reference_standard": "NR",
                "num_lesions_received_reference_standard_but_not_index_test": "NR",
            }

    def forward_sync(self, markdown_content: str) -> Dict[str, Any]:
        result = self.extract(markdown_content=markdown_content)
        return safe_json_parse(result.lesions_partial_verification_json)


class AsyncTimeIntervalExtractor(dspy.Module):
    """Async module to extract time interval between tests."""

    def __init__(self):
        super().__init__()
        self.extract = dspy.ChainOfThought(ExtractTimeInterval)

    async def __call__(self, markdown_content: str) -> str:
        try:
            outputs = await async_dspy_forward(self.extract, markdown_content=markdown_content)
            return outputs.get("time_interval", "NR")
        except Exception as e:
            print(f"Error in time interval extraction: {e}")
            return "NR"

    def forward_sync(self, markdown_content: str) -> str:
        result = self.extract(markdown_content=markdown_content)
        return result.time_interval


class AsyncMissingDataCombiner(dspy.Module):
    """Async module to combine all missing data components."""

    def __init__(self):
        super().__init__()
        self.combiner = dspy.ChainOfThought(CombineMissingData)

    async def __call__(
        self,
        patients_partial_verification: Dict[str, Any],
        lesions_partial_verification: Dict[str, Any],
        time_interval: str
    ) -> Dict[str, Any]:
        try:
            outputs = await async_dspy_forward(
                self.combiner,
                patients_partial_verification_json=json.dumps(patients_partial_verification),
                lesions_partial_verification_json=json.dumps(lesions_partial_verification),
                time_interval=time_interval
            )
            return safe_json_parse(outputs.get("complete_missing_data_json", "{}"))
        except Exception as e:
            print(f"Error in combining missing data: {e}, using fallback merge")
            combined = {}
            combined.update(patients_partial_verification)
            combined.update(lesions_partial_verification)
            combined["time_interval_between_index_test_and_reference_standard"] = time_interval
            return combined

    def forward_sync(
        self,
        patients_partial_verification: Dict[str, Any],
        lesions_partial_verification: Dict[str, Any],
        time_interval: str
    ) -> Dict[str, Any]:
        result = self.combiner(
            patients_partial_verification_json=json.dumps(patients_partial_verification),
            lesions_partial_verification_json=json.dumps(lesions_partial_verification),
            time_interval=time_interval
        )
        return safe_json_parse(result.complete_missing_data_json)


class AsyncMissingDataPipeline(dspy.Module):
    """Complete async pipeline for extracting missing data and partial verification information."""

    def __init__(self, max_concurrent: int = 5):
        super().__init__()

        self.patients_extractor = AsyncPatientsPartialVerificationExtractor()
        self.lesions_extractor = AsyncLesionsPartialVerificationExtractor()
        self.time_interval_extractor = AsyncTimeIntervalExtractor()
        self.combiner = AsyncMissingDataCombiner()

        self.max_concurrent = max_concurrent
        self._semaphore = None

    def _get_semaphore(self):
        if self._semaphore is None:
            self._semaphore = asyncio.Semaphore(self.max_concurrent)
        return self._semaphore

    async def forward(self, markdown_content: str):
        # Extract all components in parallel (all are independent)
        patients_task = self.patients_extractor(markdown_content)
        lesions_task = self.lesions_extractor(markdown_content)
        time_interval_task = self.time_interval_extractor(markdown_content)

        patients_partial, lesions_partial, time_interval = await asyncio.gather(
            patients_task,
            lesions_task,
            time_interval_task
        )

        # Combine all data
        complete_missing_data = await self.combiner(
            patients_partial,
            lesions_partial,
            time_interval
        )

        return dspy.Prediction(
            missing_data=complete_missing_data,
            success=True
        )

    async def __call__(self, markdown_content: str):
        return await self.forward(markdown_content)


class SyncMissingDataPipeline(dspy.Module):
    """Synchronous wrapper for async missing data pipeline."""

    def __init__(self):
        super().__init__()
        self.async_pipeline = AsyncMissingDataPipeline()

        # Expose extractors for optimizer access
        self.patients_extractor = self.async_pipeline.patients_extractor
        self.lesions_extractor = self.async_pipeline.lesions_extractor
        self.time_interval_extractor = self.async_pipeline.time_interval_extractor
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
        return SyncMissingDataPipeline()


__all__ = [
    "AsyncPatientsPartialVerificationExtractor",
    "AsyncLesionsPartialVerificationExtractor",
    "AsyncTimeIntervalExtractor",
    "AsyncMissingDataCombiner",
    "AsyncMissingDataPipeline",
    "SyncMissingDataPipeline",
]