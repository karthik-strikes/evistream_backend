import asyncio
import json
from typing import Dict, Any

import dspy

from utils.dspy_async import async_dspy_forward
from utils.json_parser import safe_json_parse
from dspy_components.tasks.index_test.signatures import (
    ExtractIndexTestType,
    ExtractIndexTestBrandAndSite,
    ExtractSpecimenCollection,
    ExtractTechniqueAndAnalysis,
    ExtractPatientsLesionsIndexTest,
    ExtractPositivityThreshold,
    ExtractAssessorTrainingAndBlinding,
    ExtractAdditionalComments,
    CombineIndexTestData,
)


class AsyncIndexTestTypeExtractor(dspy.Module):
    """Async module to extract index test type."""

    def __init__(self):
        super().__init__()
        self.extract = dspy.ChainOfThought(ExtractIndexTestType)

    async def __call__(self, markdown_content: str) -> Dict[str, Any]:
        try:
            outputs = await async_dspy_forward(self.extract, markdown_content=markdown_content)
            return safe_json_parse(outputs.get("index_test_type_json", "{}"))
        except Exception as e:
            print(f"Error in index test type extraction: {e}")
            return {
                "cytology": {"selected": False, "comment": ""},
                "vital_staining": {"selected": False, "comment": ""},
                "autofluorescence": {"selected": False, "comment": ""},
                "tissue_reflectance": {"selected": False, "comment": ""},
                "other": {"selected": False, "comment": ""}
            }

    def forward_sync(self, markdown_content: str) -> Dict[str, Any]:
        result = self.extract(markdown_content=markdown_content)
        return safe_json_parse(result.index_test_type_json)


class AsyncIndexTestBrandAndSiteExtractor(dspy.Module):
    """Async module to extract brand name and site selection."""

    def __init__(self):
        super().__init__()
        self.extract = dspy.ChainOfThought(ExtractIndexTestBrandAndSite)

    async def __call__(self, markdown_content: str) -> Dict[str, Any]:
        try:
            outputs = await async_dspy_forward(self.extract, markdown_content=markdown_content)
            return safe_json_parse(outputs.get("brand_and_site_json", "{}"))
        except Exception as e:
            print(f"Error in brand/site extraction: {e}")
            return {
                "brand_name": "NR",
                "site_selection": "NR"
            }

    def forward_sync(self, markdown_content: str) -> Dict[str, Any]:
        result = self.extract(markdown_content=markdown_content)
        return safe_json_parse(result.brand_and_site_json)


class AsyncSpecimenCollectionExtractor(dspy.Module):
    """Async module to extract specimen collection methodology."""

    def __init__(self):
        super().__init__()
        self.extract = dspy.ChainOfThought(ExtractSpecimenCollection)

    async def __call__(self, markdown_content: str, index_test_type: str) -> str:
        try:
            outputs = await async_dspy_forward(self.extract, markdown_content=markdown_content, index_test_type=index_test_type)
            return outputs.get("specimen_collection", "")
        except Exception as e:
            print(f"Error in specimen collection extraction: {e}")
            return ""

    def forward_sync(self, markdown_content: str, index_test_type: str) -> str:
        result = self.extract(
            markdown_content=markdown_content,
            index_test_type=index_test_type
        )
        return result.specimen_collection


class AsyncTechniqueAndAnalysisExtractor(dspy.Module):
    """Async module to extract technique and analysis methods."""

    def __init__(self):
        super().__init__()
        self.extract = dspy.ChainOfThought(ExtractTechniqueAndAnalysis)

    async def __call__(self, markdown_content: str, index_test_type: str) -> str:
        try:
            outputs = await async_dspy_forward(self.extract, markdown_content=markdown_content, index_test_type=index_test_type)
            return outputs.get("technique", "")
        except Exception as e:
            print(f"Error in technique/analysis extraction: {e}")
            return ""

    def forward_sync(self, markdown_content: str, index_test_type: str) -> str:
        result = self.extract(
            markdown_content=markdown_content,
            index_test_type=index_test_type
        )
        return result.technique


class AsyncPatientsLesionsIndexTestExtractor(dspy.Module):
    """Async module to extract patient and lesion counts for index test."""

    def __init__(self):
        super().__init__()
        self.extract = dspy.ChainOfThought(ExtractPatientsLesionsIndexTest)

    async def __call__(self, markdown_content: str) -> Dict[str, Any]:
        try:
            outputs = await async_dspy_forward(self.extract, markdown_content=markdown_content)
            return safe_json_parse(outputs.get("patients_lesions_index_test_json", "{}"))
        except Exception as e:
            print(f"Error in patients/lesions index test extraction: {e}")
            return {
                "patients_received_n": "NR",
                "patients_analyzed_n": "NR",
                "lesions_received_n": "NR",
                "lesions_analyzed_n": "NR"
            }

    def forward_sync(self, markdown_content: str) -> Dict[str, Any]:
        result = self.extract(markdown_content=markdown_content)
        return safe_json_parse(result.patients_lesions_index_test_json)


class AsyncPositivityThresholdExtractor(dspy.Module):
    """Async module to extract positivity threshold criteria."""

    def __init__(self):
        super().__init__()
        self.extract = dspy.ChainOfThought(ExtractPositivityThreshold)

    async def __call__(self, markdown_content: str, index_test_type: str) -> str:
        try:
            outputs = await async_dspy_forward(self.extract, markdown_content=markdown_content, index_test_type=index_test_type)
            return outputs.get("positivity_threshold", "NR")
        except Exception as e:
            print(f"Error in positivity threshold extraction: {e}")
            return "NR"

    def forward_sync(self, markdown_content: str, index_test_type: str) -> str:
        result = self.extract(
            markdown_content=markdown_content,
            index_test_type=index_test_type
        )
        return result.positivity_threshold


class AsyncAssessorTrainingBlindingExtractor(dspy.Module):
    """Async module to extract assessor training and blinding information."""

    def __init__(self):
        super().__init__()
        self.extract = dspy.ChainOfThought(ExtractAssessorTrainingAndBlinding)

    async def __call__(self, markdown_content: str) -> Dict[str, Any]:
        try:
            outputs = await async_dspy_forward(self.extract, markdown_content=markdown_content)
            return safe_json_parse(outputs.get("assessor_training_blinding_json", "{}"))
        except Exception as e:
            print(f"Error in assessor training/blinding extraction: {e}")
            return {
                "assessor_training": "NR",
                "assessor_blinding": "NR",
                "examiner_blinding": "NR"
            }

    def forward_sync(self, markdown_content: str) -> Dict[str, Any]:
        result = self.extract(markdown_content=markdown_content)
        return safe_json_parse(result.assessor_training_blinding_json)


class AsyncAdditionalCommentsExtractor(dspy.Module):
    """Async module to extract additional comments."""

    def __init__(self):
        super().__init__()
        self.extract = dspy.ChainOfThought(ExtractAdditionalComments)

    async def __call__(self, markdown_content: str) -> str:
        try:
            outputs = await async_dspy_forward(self.extract, markdown_content=markdown_content)
            return outputs.get("additional_comments", "")
        except Exception as e:
            print(f"Error in additional comments extraction: {e}")
            return ""

    def forward_sync(self, markdown_content: str) -> str:
        result = self.extract(markdown_content=markdown_content)
        return result.additional_comments


class AsyncIndexTestCombiner(dspy.Module):
    """Async module to combine all index test data."""

    def __init__(self):
        super().__init__()
        self.combiner = dspy.ChainOfThought(CombineIndexTestData)

    async def __call__(
        self,
        index_test_type: Dict[str, Any],
        brand_and_site: Dict[str, Any],
        specimen_collection: str,
        technique: str,
        patients_lesions_index_test: Dict[str, Any],
        positivity_threshold: str,
        assessor_training_blinding: Dict[str, Any],
        additional_comments: str
    ) -> Dict[str, Any]:
        try:
            outputs = await async_dspy_forward(
                self.combiner,
                index_test_type_json=json.dumps(index_test_type),
                brand_and_site_json=json.dumps(brand_and_site),
                specimen_collection=specimen_collection,
                technique=technique,
                patients_lesions_index_test_json=json.dumps(patients_lesions_index_test),
                positivity_threshold=positivity_threshold,
                assessor_training_blinding_json=json.dumps(assessor_training_blinding),
                additional_comments=additional_comments
            )
            return safe_json_parse(outputs.get("complete_index_test_json", "{}"))
        except Exception as e:
            print(f"Error in combining index test data: {e}, using fallback merge")
            combined = {
                "type": index_test_type,
                "specimen_collection": specimen_collection,
                "technique": technique,
                "positivity_threshold": positivity_threshold,
                "additional_comments": additional_comments
            }
            combined.update(brand_and_site)
            combined.update(patients_lesions_index_test)
            combined.update(assessor_training_blinding)
            return combined

    def forward_sync(
        self,
        index_test_type: Dict[str, Any],
        brand_and_site: Dict[str, Any],
        specimen_collection: str,
        technique: str,
        patients_lesions_index_test: Dict[str, Any],
        positivity_threshold: str,
        assessor_training_blinding: Dict[str, Any],
        additional_comments: str
    ) -> Dict[str, Any]:
        result = self.combiner(
            index_test_type_json=json.dumps(index_test_type),
            brand_and_site_json=json.dumps(brand_and_site),
            specimen_collection=specimen_collection,
            technique=technique,
            patients_lesions_index_test_json=json.dumps(patients_lesions_index_test),
            positivity_threshold=positivity_threshold,
            assessor_training_blinding_json=json.dumps(assessor_training_blinding),
            additional_comments=additional_comments
        )
        return safe_json_parse(result.complete_index_test_json)


class AsyncIndexTestPipeline(dspy.Module):
    """Complete async pipeline for extracting index test information."""

    def __init__(self, max_concurrent: int = 5):
        super().__init__()

        self.index_test_type_extractor = AsyncIndexTestTypeExtractor()
        self.brand_and_site_extractor = AsyncIndexTestBrandAndSiteExtractor()
        self.specimen_collection_extractor = AsyncSpecimenCollectionExtractor()
        self.technique_extractor = AsyncTechniqueAndAnalysisExtractor()
        self.patients_lesions_extractor = AsyncPatientsLesionsIndexTestExtractor()
        self.positivity_threshold_extractor = AsyncPositivityThresholdExtractor()
        self.assessor_training_blinding_extractor = AsyncAssessorTrainingBlindingExtractor()
        self.additional_comments_extractor = AsyncAdditionalCommentsExtractor()
        self.combiner = AsyncIndexTestCombiner()

        self.max_concurrent = max_concurrent
        self._semaphore = None

    def _get_semaphore(self):
        if self._semaphore is None:
            self._semaphore = asyncio.Semaphore(self.max_concurrent)
        return self._semaphore

    async def forward(self, markdown_content: str):
        # Step 1: Extract index test type first (needed for context)
        index_test_type = await self.index_test_type_extractor(markdown_content)
        index_test_type_str = json.dumps(index_test_type)

        # Step 2: Extract components that don't depend on index test type (parallel)
        brand_and_site_task = self.brand_and_site_extractor(markdown_content)
        patients_lesions_task = self.patients_lesions_extractor(markdown_content)
        assessor_training_blinding_task = self.assessor_training_blinding_extractor(markdown_content)
        additional_comments_task = self.additional_comments_extractor(markdown_content)

        # Step 3: Extract components that need index test type context (parallel)
        specimen_collection_task = self.specimen_collection_extractor(markdown_content, index_test_type_str)
        technique_task = self.technique_extractor(markdown_content, index_test_type_str)
        positivity_threshold_task = self.positivity_threshold_extractor(markdown_content, index_test_type_str)

        (brand_and_site, patients_lesions, assessor_training_blinding, additional_comments,
         specimen_collection, technique, positivity_threshold) = await asyncio.gather(
            brand_and_site_task,
            patients_lesions_task,
            assessor_training_blinding_task,
            additional_comments_task,
            specimen_collection_task,
            technique_task,
            positivity_threshold_task
        )

        # Step 4: Combine all data
        complete_index_test = await self.combiner(
            index_test_type,
            brand_and_site,
            specimen_collection,
            technique,
            patients_lesions,
            positivity_threshold,
            assessor_training_blinding,
            additional_comments
        )

        return dspy.Prediction(
            index_test=complete_index_test,
            success=True
        )

    async def __call__(self, markdown_content: str):
        return await self.forward(markdown_content)


class SyncIndexTestPipeline(dspy.Module):
    """Synchronous wrapper for async index test pipeline."""

    def __init__(self):
        super().__init__()
        self.async_pipeline = AsyncIndexTestPipeline()

        # Expose extractors for optimizer access
        self.index_test_type_extractor = self.async_pipeline.index_test_type_extractor
        self.brand_and_site_extractor = self.async_pipeline.brand_and_site_extractor
        self.specimen_collection_extractor = self.async_pipeline.specimen_collection_extractor
        self.technique_extractor = self.async_pipeline.technique_extractor
        self.patients_lesions_extractor = self.async_pipeline.patients_lesions_extractor
        self.positivity_threshold_extractor = self.async_pipeline.positivity_threshold_extractor
        self.assessor_training_blinding_extractor = self.async_pipeline.assessor_training_blinding_extractor
        self.additional_comments_extractor = self.async_pipeline.additional_comments_extractor
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
        return SyncIndexTestPipeline()


__all__ = [
    "AsyncIndexTestTypeExtractor",
    "AsyncIndexTestBrandAndSiteExtractor",
    "AsyncSpecimenCollectionExtractor",
    "AsyncTechniqueAndAnalysisExtractor",
    "AsyncPatientsLesionsIndexTestExtractor",
    "AsyncPositivityThresholdExtractor",
    "AsyncAssessorTrainingBlindingExtractor",
    "AsyncAdditionalCommentsExtractor",
    "AsyncIndexTestCombiner",
    "AsyncIndexTestPipeline",
    "SyncIndexTestPipeline",
]