from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Type

from schemas.base import SchemaDefinition

from dspy_components.tasks.patient_population.modules import AsyncPatientPopulationCharacteristicsPipeline
from dspy_components.tasks.patient_population.signatures import CombinePatientPopulationCharacteristics
from dspy_components.tasks.index_test.modules import AsyncIndexTestPipeline
from dspy_components.tasks.index_test.signatures import CombineIndexTestData
from dspy_components.tasks.outcomes_study.modules import AsyncOutcomesPipeline
from dspy_components.tasks.outcomes_study.signatures import CombineOutcomesData
from dspy_components.tasks.missing_data_study.modules import AsyncMissingDataPipeline
from dspy_components.tasks.missing_data_study.signatures import CombineMissingData
from dspy_components.tasks.reference_standard.modules import AsyncReferenceStandardPipeline
from dspy_components.tasks.reference_standard.signatures import CombineReferenceStandardData


@dataclass(frozen=True)
class SchemaConfig:
    """Lightweight descriptor used to build SchemaDefinition instances."""

    name: str
    description: str
    signature_class: Any
    pipeline_class: Type
    output_field_name: str
    cache_file: str

    def build_pipeline_factory(self) -> Callable[[], Any]:
        pipeline_cls = self.pipeline_class

        def _factory():
            return pipeline_cls()

        return _factory

    def build_definition(self) -> SchemaDefinition:
        return SchemaDefinition(
            name=self.name,
            description=self.description,
            signature_class=self.signature_class,
            output_field_name=self.output_field_name,
            field_cache_file=self.cache_file,
            pipeline_factory=self.build_pipeline_factory(),
        )


SCHEMA_CONFIGS: Dict[str, SchemaConfig] = {
    "patient_population": SchemaConfig(
        name="patient_population",
        description="Patient population characteristics extraction schema.",
        signature_class=CombinePatientPopulationCharacteristics,
        pipeline_class=AsyncPatientPopulationCharacteristicsPipeline,
        output_field_name="complete_characteristics_json",
        cache_file="schemas/generated_fields/patient_population_fields.json",
    ),
    "index_test": SchemaConfig(
        name="index_test",
        description="Index test characteristics extraction schema.",
        signature_class=CombineIndexTestData,
        pipeline_class=AsyncIndexTestPipeline,
        output_field_name="complete_index_test_json",
        cache_file="schemas/generated_fields/index_test_fields.json",
    ),
    "outcomes_study": SchemaConfig(
        name="outcomes_study",
        description="Outcomes study diagnostic test performance extraction schema.",
        signature_class=CombineOutcomesData,
        pipeline_class=AsyncOutcomesPipeline,
        output_field_name="complete_outcomes_json",
        cache_file="schemas/generated_fields/outcomes_study_fields.json",
    ),
    "missing_data_study": SchemaConfig(
        name="missing_data_study",
        description="Missing data and partial verification extraction schema.",
        signature_class=CombineMissingData,
        pipeline_class=AsyncMissingDataPipeline,
        output_field_name="complete_missing_data_json",
        cache_file="schemas/generated_fields/missing_data_study_fields.json",
    ),
    "reference_standard": SchemaConfig(
        name="reference_standard",
        description="Reference standard and biopsy information extraction schema.",
        signature_class=CombineReferenceStandardData,
        pipeline_class=AsyncReferenceStandardPipeline,
        output_field_name="complete_reference_standard_json",
        cache_file="schemas/generated_fields/reference_standard_fields.json",
    ),
}


def get_schema_definition(name: str) -> SchemaDefinition:
    """Return a SchemaDefinition by name."""
    # Build dynamically from SCHEMA_CONFIGS so new schemas are always seen
    if name not in SCHEMA_CONFIGS:
        raise KeyError(f"Schema '{name}' not found in SCHEMA_CONFIGS")
    return SCHEMA_CONFIGS[name].build_definition()


def get_all_schema_definitions() -> Dict[str, SchemaDefinition]:
    """Return all schema definitions built dynamically from SCHEMA_CONFIGS."""
    # Build fresh from SCHEMA_CONFIGS each time so new schemas are always seen
    return {name: config.build_definition() for name, config in SCHEMA_CONFIGS.items()}


def list_schema_names() -> List[str]:
    """Helper for callers that only need the registered names."""
    return sorted(SCHEMA_CONFIGS.keys())
