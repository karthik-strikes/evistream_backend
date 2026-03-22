import asyncio
import json
from typing import Dict, Any

import dspy

from utils.dspy_async import async_dspy_forward
from utils.json_parser import safe_json_parse
from dspy_components.tasks.reference_standard.signatures import (
    ExtractReferenceStandardType,
    ExtractBiopsySite,
    ExtractPatientsLesionsReferenceStandard,
    ExtractPositivityThreshold,
    ExtractTrainingCalibration,
    ExtractBlindingReferenceStandard,
    CombineReferenceStandardData,
)


class AsyncReferenceStandardTypeExtractor(dspy.Module):
    """Async module to extract reference standard type and biopsy details."""

    def __init__(self):
        super().__init__()
        self.extract = dspy.ChainOfThought(ExtractReferenceStandardType)

    async def __call__(self, markdown_content: str) -> Dict[str, Any]:
        try:
            outputs = await async_dspy_forward(self.extract, markdown_content=markdown_content)
            return safe_json_parse(outputs.get("reference_standard_type_json", "{}"))
        except Exception as e:
            print(f"Error in reference standard type extraction: {e}")
            return {
                "biopsy_and_histopathological_assessment": {"selected": True, "comment": "NR"},
                "other": {"selected": False, "comment": ""}
            }

    def forward_sync(self, markdown_content: str) -> Dict[str, Any]:
        result = self.extract(markdown_content=markdown_content)
        return safe_json_parse(result.reference_standard_type_json)


class AsyncBiopsySiteExtractor(dspy.Module):
    """Async module to extract biopsy site information."""

    def __init__(self):
        super().__init__()
        self.extract = dspy.ChainOfThought(ExtractBiopsySite)

    async def __call__(self, markdown_content: str) -> Dict[str, Any]:
        try:
            outputs = await async_dspy_forward(self.extract, markdown_content=markdown_content)
            return safe_json_parse(outputs.get("site_of_biopsy_json", "{}"))
        except Exception as e:
            print(f"Error in biopsy site extraction: {e}")
            return {
                "site_of_biopsy": "NR",
                "site_of_biopsy_full_description": "NR"
            }

    def forward_sync(self, markdown_content: str) -> Dict[str, Any]:
        result = self.extract(markdown_content=markdown_content)
        return safe_json_parse(result.site_of_biopsy_json)


class AsyncPatientsLesionsReferenceStandardExtractor(dspy.Module):
    """Async module to extract patient and lesion counts for reference standard."""

    def __init__(self):
        super().__init__()
        self.extract = dspy.ChainOfThought(ExtractPatientsLesionsReferenceStandard)

    async def __call__(self, markdown_content: str) -> Dict[str, Any]:
        try:
            outputs = await async_dspy_forward(self.extract, markdown_content=markdown_content)
            return safe_json_parse(outputs.get("patients_lesions_reference_standard_json", "{}"))
        except Exception as e:
            print(f"Error in patients/lesions reference standard extraction: {e}")
            return {
                "num_patients_received_reference_standard": "NR",
                "num_patients_analyzed_reference_standard": "NR",
                "num_lesions_received_reference_standard": "NR",
                "num_lesions_analyzed_reference_standard": "NR"
            }

    def forward_sync(self, markdown_content: str) -> Dict[str, Any]:
        result = self.extract(markdown_content=markdown_content)
        return safe_json_parse(result.patients_lesions_reference_standard_json)


class AsyncPositivityThresholdExtractor(dspy.Module):
    """Async module to extract positivity threshold criteria."""

    def __init__(self):
        super().__init__()
        self.extract = dspy.ChainOfThought(ExtractPositivityThreshold)

    async def __call__(self, markdown_content: str) -> Dict[str, Any]:
        try:
            outputs = await async_dspy_forward(self.extract, markdown_content=markdown_content)
            return safe_json_parse(outputs.get("positivity_threshold_json", "{}"))
        except Exception as e:
            print(f"Error in positivity threshold extraction: {e}")
            return {
                "oral_cavity_cancer": {"selected": False, "comment": "NA"},
                "potentially_malignant_disorder": {"selected": False, "comment": "NA"},
                "squamous_cell_carcinoma": {"selected": False, "comment": "NA"},
                "other": {"selected": False, "comment": "NA"},
                "positivity_threshold_summary": "NR",
                "final_diagnosis_categories": "NR"
            }

    def forward_sync(self, markdown_content: str) -> Dict[str, Any]:
        result = self.extract(markdown_content=markdown_content)
        return safe_json_parse(result.positivity_threshold_json)


class AsyncTrainingCalibrationExtractor(dspy.Module):
    """Async module to extract training/calibration information."""

    def __init__(self):
        super().__init__()
        self.extract = dspy.ChainOfThought(ExtractTrainingCalibration)

    async def __call__(self, markdown_content: str) -> str:
        try:
            outputs = await async_dspy_forward(self.extract, markdown_content=markdown_content)
            return outputs.get("training_calibration", "NR")
        except Exception as e:
            print(f"Error in training/calibration extraction: {e}")
            return "NR"

    def forward_sync(self, markdown_content: str) -> str:
        result = self.extract(markdown_content=markdown_content)
        return result.training_calibration


class AsyncBlindingReferenceStandardExtractor(dspy.Module):
    """Async module to extract blinding information."""

    def __init__(self):
        super().__init__()
        self.extract = dspy.ChainOfThought(ExtractBlindingReferenceStandard)

    async def __call__(self, markdown_content: str) -> str:
        try:
            outputs = await async_dspy_forward(self.extract, markdown_content=markdown_content)
            return outputs.get("blinding_reference_standard", "NR")
        except Exception as e:
            print(f"Error in blinding reference standard extraction: {e}")
            return "NR"

    def forward_sync(self, markdown_content: str) -> str:
        result = self.extract(markdown_content=markdown_content)
        return result.blinding_reference_standard


class AsyncReferenceStandardCombiner(dspy.Module):
    """Async module to combine all reference standard data."""

    def __init__(self):
        super().__init__()
        self.combiner = dspy.ChainOfThought(CombineReferenceStandardData)

    async def __call__(
        self,
        reference_standard_type: Dict[str, Any],
        site_of_biopsy: Dict[str, Any],
        patients_lesions_reference_standard: Dict[str, Any],
        positivity_threshold: Dict[str, Any],
        training_calibration: str,
        blinding_reference_standard: str
    ) -> Dict[str, Any]:
        try:
            outputs = await async_dspy_forward(
                self.combiner,
                reference_standard_type_json=json.dumps(reference_standard_type),
                site_of_biopsy_json=json.dumps(site_of_biopsy),
                patients_lesions_reference_standard_json=json.dumps(patients_lesions_reference_standard),
                positivity_threshold_json=json.dumps(positivity_threshold),
                training_calibration=training_calibration,
                blinding_reference_standard=blinding_reference_standard
            )
            return safe_json_parse(outputs.get("complete_reference_standard_json", "{}"))
        except Exception as e:
            print(f"Error in combining reference standard data: {e}, using fallback merge")
            combined = {
                "reference_standard_type": reference_standard_type,
                "training_calibration_reference_standard_examiners": training_calibration,
                "blinding_reference_standard_examiners_to_index_test": blinding_reference_standard
            }
            combined.update(site_of_biopsy)
            combined.update(patients_lesions_reference_standard)
            combined.update(positivity_threshold)
            return combined

    def forward_sync(
        self,
        reference_standard_type: Dict[str, Any],
        site_of_biopsy: Dict[str, Any],
        patients_lesions_reference_standard: Dict[str, Any],
        positivity_threshold: Dict[str, Any],
        training_calibration: str,
        blinding_reference_standard: str
    ) -> Dict[str, Any]:
        result = self.combiner(
            reference_standard_type_json=json.dumps(reference_standard_type),
            site_of_biopsy_json=json.dumps(site_of_biopsy),
            patients_lesions_reference_standard_json=json.dumps(patients_lesions_reference_standard),
            positivity_threshold_json=json.dumps(positivity_threshold),
            training_calibration=training_calibration,
            blinding_reference_standard=blinding_reference_standard
        )
        return safe_json_parse(result.complete_reference_standard_json)


class AsyncReferenceStandardPipeline(dspy.Module):
    """Complete async pipeline for extracting reference standard information."""

    def __init__(self, max_concurrent: int = 5):
        super().__init__()

        self.reference_standard_type_extractor = AsyncReferenceStandardTypeExtractor()
        self.biopsy_site_extractor = AsyncBiopsySiteExtractor()
        self.patients_lesions_extractor = AsyncPatientsLesionsReferenceStandardExtractor()
        self.positivity_threshold_extractor = AsyncPositivityThresholdExtractor()
        self.training_calibration_extractor = AsyncTrainingCalibrationExtractor()
        self.blinding_extractor = AsyncBlindingReferenceStandardExtractor()
        self.combiner = AsyncReferenceStandardCombiner()

        self.max_concurrent = max_concurrent
        self._semaphore = None

    def _get_semaphore(self):
        if self._semaphore is None:
            self._semaphore = asyncio.Semaphore(self.max_concurrent)
        return self._semaphore

    async def forward(self, markdown_content: str):
        # Extract all components in parallel (all are independent)
        reference_standard_type_task = self.reference_standard_type_extractor(markdown_content)
        biopsy_site_task = self.biopsy_site_extractor(markdown_content)
        patients_lesions_task = self.patients_lesions_extractor(markdown_content)
        positivity_threshold_task = self.positivity_threshold_extractor(markdown_content)
        training_calibration_task = self.training_calibration_extractor(markdown_content)
        blinding_task = self.blinding_extractor(markdown_content)

        (reference_standard_type, biopsy_site, patients_lesions,
         positivity_threshold, training_calibration, blinding) = await asyncio.gather(
            reference_standard_type_task,
            biopsy_site_task,
            patients_lesions_task,
            positivity_threshold_task,
            training_calibration_task,
            blinding_task
        )

        # Combine all data
        complete_reference_standard = await self.combiner(
            reference_standard_type,
            biopsy_site,
            patients_lesions,
            positivity_threshold,
            training_calibration,
            blinding
        )

        return dspy.Prediction(
            reference_standard=complete_reference_standard,
            success=True
        )

    async def __call__(self, markdown_content: str):
        return await self.forward(markdown_content)


class SyncReferenceStandardPipeline(dspy.Module):
    """Synchronous wrapper for async reference standard pipeline."""

    def __init__(self):
        super().__init__()
        self.async_pipeline = AsyncReferenceStandardPipeline()

        # Expose extractors for optimizer access
        self.reference_standard_type_extractor = self.async_pipeline.reference_standard_type_extractor
        self.biopsy_site_extractor = self.async_pipeline.biopsy_site_extractor
        self.patients_lesions_extractor = self.async_pipeline.patients_lesions_extractor
        self.positivity_threshold_extractor = self.async_pipeline.positivity_threshold_extractor
        self.training_calibration_extractor = self.async_pipeline.training_calibration_extractor
        self.blinding_extractor = self.async_pipeline.blinding_extractor
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
        return SyncReferenceStandardPipeline()


__all__ = [
    "AsyncReferenceStandardTypeExtractor",
    "AsyncBiopsySiteExtractor",
    "AsyncPatientsLesionsReferenceStandardExtractor",
    "AsyncPositivityThresholdExtractor",
    "AsyncTrainingCalibrationExtractor",
    "AsyncBlindingReferenceStandardExtractor",
    "AsyncReferenceStandardCombiner",
    "AsyncReferenceStandardPipeline",
    "SyncReferenceStandardPipeline",
]