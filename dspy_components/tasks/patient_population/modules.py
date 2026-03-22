import asyncio
import json
from typing import Dict, Any

import dspy

from utils.dspy_async import async_dspy_forward
from utils.json_parser import safe_json_parse
from dspy_components.tasks.patient_population.signatures import (
    ExtractPatientPopulation,
    ExtractPatientSelectionAndDemographics,
    ExtractAgeCharacteristics,
    ExtractBaselineCharacteristics,
    ExtractTargetCondition,
    CombinePatientPopulationCharacteristics,
)


class AsyncPatientPopulationExtractor(dspy.Module):
    """Async module to extract patient population categories."""

    def __init__(self):
        super().__init__()
        self.extract = dspy.ChainOfThought(ExtractPatientPopulation)

    async def __call__(self, markdown_content: str) -> Dict[str, Any]:
        try:
            outputs = await async_dspy_forward(self.extract, markdown_content=markdown_content)
            return safe_json_parse(outputs.get("patient_population_json", "{}"))
        except Exception as e:
            print(f"Error in patient population extraction: {e}")
            return {
                "population": {
                    "innocuous_lesions": {"selected": False, "comment": ""},
                    "suspicious_or_malignant_lesions": {"selected": False, "comment": ""},
                    "healthy_without_lesions": {"selected": False, "comment": ""},
                    "other": {"selected": False, "comment": ""},
                    "unclear": {"selected": False, "comment": ""},
                    "statement": "NR",
                }
            }

    def forward_sync(self, markdown_content: str) -> Dict[str, Any]:
        result = self.extract(markdown_content=markdown_content)
        return safe_json_parse(result.patient_population_json)


class AsyncPatientSelectionDemographicsExtractor(dspy.Module):
    """Async module to extract patient selection method and demographics."""

    def __init__(self):
        super().__init__()
        self.extract = dspy.ChainOfThought(
            ExtractPatientSelectionAndDemographics)

    async def __call__(self, markdown_content: str) -> Dict[str, Any]:
        try:
            outputs = await async_dspy_forward(self.extract, markdown_content=markdown_content)
            return safe_json_parse(outputs.get("selection_demographics_json", "{}"))
        except Exception as e:
            print(f"Error in selection/demographics extraction: {e}")
            return {
                "patient_selection_method": "NR",
                "population_ses": "NR",
                "population_ethnicity": "NR",
                "population_risk_factors": "NR",
            }

    def forward_sync(self, markdown_content: str) -> Dict[str, Any]:
        result = self.extract(markdown_content=markdown_content)
        return safe_json_parse(result.selection_demographics_json)


class AsyncAgeCharacteristicsExtractor(dspy.Module):
    """Async module to extract age characteristics."""

    def __init__(self):
        super().__init__()
        self.extract = dspy.ChainOfThought(ExtractAgeCharacteristics)

    async def __call__(self, markdown_content: str) -> Dict[str, Any]:
        try:
            outputs = await async_dspy_forward(self.extract, markdown_content=markdown_content)
            return safe_json_parse(outputs.get("age_characteristics_json", "{}"))
        except Exception as e:
            print(f"Error in age characteristics extraction: {e}")
            return {
                "age_central_tendency": {
                    "mean": {"selected": False, "value": ""},
                    "median": {"selected": False, "value": ""},
                    "not_reported": True,
                },
                "age_variability": {
                    "sd": {"selected": False, "value": ""},
                    "range": {"selected": False, "value": ""},
                    "not_reported": True,
                },
            }

    def forward_sync(self, markdown_content: str) -> Dict[str, Any]:
        result = self.extract(markdown_content=markdown_content)
        return safe_json_parse(result.age_characteristics_json)


class AsyncBaselineCharacteristicsExtractor(dspy.Module):
    """Async module to extract baseline characteristics."""

    def __init__(self):
        super().__init__()
        self.extract = dspy.ChainOfThought(ExtractBaselineCharacteristics)

    async def __call__(self, markdown_content: str) -> Dict[str, Any]:
        try:
            outputs = await async_dspy_forward(self.extract, markdown_content=markdown_content)
            return safe_json_parse(outputs.get("baseline_json", "{}"))
        except Exception as e:
            print(f"Error in baseline characteristics extraction: {e}")
            return {
                "baseline_participants": {
                    "total": {"selected": False, "value": ""},
                    "female_n": {"selected": False, "value": ""},
                    "female_percent": {"selected": False, "value": ""},
                    "male_n": {"selected": False, "value": ""},
                    "male_percent": {"selected": False, "value": ""},
                    "not_reported": {"selected": True, "value": ""},
                    "other": {"selected": False, "value": ""},
                }
            }

    def forward_sync(self, markdown_content: str) -> Dict[str, Any]:
        result = self.extract(markdown_content=markdown_content)
        return safe_json_parse(result.baseline_json)


class AsyncTargetConditionExtractor(dspy.Module):
    """Async module to extract target condition details."""

    def __init__(self):
        super().__init__()
        self.extract = dspy.ChainOfThought(ExtractTargetCondition)

    async def __call__(self, markdown_content: str) -> Dict[str, Any]:
        try:
            outputs = await async_dspy_forward(self.extract, markdown_content=markdown_content)
            return safe_json_parse(outputs.get("target_condition_json", "{}"))
        except Exception as e:
            print(f"Error in target condition extraction: {e}")
            return {
                "target_condition": {
                    "opmd": {"selected": False, "comment": ""},
                    "oral_cancer": {"selected": False, "comment": ""},
                    "other": {"selected": False, "comment": ""},
                },
                "target_condition_severity": "NR",
                "target_condition_site": "NR",
                "filename": "Unknown_0000",
            }

    def forward_sync(self, markdown_content: str) -> Dict[str, Any]:
        result = self.extract(markdown_content=markdown_content)
        return safe_json_parse(result.target_condition_json)


class AsyncPatientCharacteristicsCombiner(dspy.Module):
    """Async module to combine all patient population characteristics."""

    def __init__(self):
        super().__init__()
        self.combiner = dspy.ChainOfThought(
            CombinePatientPopulationCharacteristics)

    async def __call__(self, patient_population: Dict, selection_demographics: Dict,
                       age_characteristics: Dict, baseline: Dict, target_condition: Dict) -> Dict[str, Any]:
        try:
            outputs = await async_dspy_forward(
                self.combiner,
                patient_population_json=json.dumps(patient_population),
                selection_demographics_json=json.dumps(selection_demographics),
                age_characteristics_json=json.dumps(age_characteristics),
                baseline_json=json.dumps(baseline),
                target_condition_json=json.dumps(target_condition)
            )
            return safe_json_parse(outputs.get("complete_characteristics_json", "{}"))
        except Exception as e:
            print(
                f"Error in combining characteristics: {e}, using fallback merge")
            combined = {}
            combined.update(patient_population)
            combined.update(selection_demographics)
            combined.update(age_characteristics)
            combined.update(baseline)
            combined.update(target_condition)
            return combined

    def forward_sync(self, patient_population: Dict, selection_demographics: Dict,
                     age_characteristics: Dict, baseline: Dict, target_condition: Dict) -> Dict[str, Any]:
        result = self.combiner(
            patient_population_json=json.dumps(patient_population),
            selection_demographics_json=json.dumps(selection_demographics),
            age_characteristics_json=json.dumps(age_characteristics),
            baseline_json=json.dumps(baseline),
            target_condition_json=json.dumps(target_condition)
        )
        return safe_json_parse(result.complete_characteristics_json)


class AsyncPatientPopulationCharacteristicsPipeline(dspy.Module):
    """Complete async pipeline for extracting patient population characteristics."""

    def __init__(self, max_concurrent: int = 5):
        super().__init__()

        self.patient_population_extractor = AsyncPatientPopulationExtractor()
        self.selection_demographics_extractor = AsyncPatientSelectionDemographicsExtractor()
        self.age_characteristics_extractor = AsyncAgeCharacteristicsExtractor()
        self.baseline_extractor = AsyncBaselineCharacteristicsExtractor()
        self.target_condition_extractor = AsyncTargetConditionExtractor()
        self.combiner = AsyncPatientCharacteristicsCombiner()

        self.max_concurrent = max_concurrent
        self._semaphore = None

    def _get_semaphore(self):
        if self._semaphore is None:
            self._semaphore = asyncio.Semaphore(self.max_concurrent)
        return self._semaphore

    async def forward(self, markdown_content: str):
        patient_population_task = self.patient_population_extractor(
            markdown_content)
        selection_demographics_task = self.selection_demographics_extractor(
            markdown_content)
        age_characteristics_task = self.age_characteristics_extractor(
            markdown_content)
        baseline_task = self.baseline_extractor(markdown_content)
        target_condition_task = self.target_condition_extractor(
            markdown_content)

        patient_population, selection_demographics, age_characteristics, baseline, target_condition = await asyncio.gather(
            patient_population_task,
            selection_demographics_task,
            age_characteristics_task,
            baseline_task,
            target_condition_task
        )

        complete_characteristics = await self.combiner(
            patient_population,
            selection_demographics,
            age_characteristics,
            baseline,
            target_condition
        )

        return dspy.Prediction(
            characteristics=complete_characteristics,
            success=True
        )

    async def __call__(self, markdown_content: str):
        return await self.forward(markdown_content)


class SyncPatientPopulationCharacteristicsPipeline(dspy.Module):
    """Synchronous wrapper for async patient population characteristics pipeline."""

    def __init__(self):
        super().__init__()
        self.async_pipeline = AsyncPatientPopulationCharacteristicsPipeline()

        self.patient_population_extractor = self.async_pipeline.patient_population_extractor
        self.selection_demographics_extractor = self.async_pipeline.selection_demographics_extractor
        self.age_characteristics_extractor = self.async_pipeline.age_characteristics_extractor
        self.baseline_extractor = self.async_pipeline.baseline_extractor
        self.target_condition_extractor = self.async_pipeline.target_condition_extractor
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
        return SyncPatientPopulationCharacteristicsPipeline()


__all__ = [
    "AsyncPatientPopulationExtractor",
    "AsyncPatientSelectionDemographicsExtractor",
    "AsyncAgeCharacteristicsExtractor",
    "AsyncBaselineCharacteristicsExtractor",
    "AsyncTargetConditionExtractor",
    "AsyncPatientCharacteristicsCombiner",
    "AsyncPatientPopulationCharacteristicsPipeline",
    "SyncPatientPopulationCharacteristicsPipeline",
]
