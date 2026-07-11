"""Registry of the 7 scalar (decomposed) forms for the single-call PROMPT-arm (D3a).

Scalar analogue of eval/studies/table_ablation/forms_registry.py. Each entry bundles what the
runner needs (schema_def JSON + markdown cache) and what the scorer needs (scoring
registry key + GT workbook + production "decomposed" CSV). The scoring registry is
lazy-loaded (see eval/studies/stage_ablation/projects.py for why importing forms.* at module
top is unsafe when building pipelines — the runner never builds one).
"""

from __future__ import annotations

import importlib
import os
from dataclasses import dataclass
from pathlib import Path


def _model_tag() -> str:
    """Output model subdir (claude | gpt | …). Set via PROMPT_ARM_MODEL_TAG; default 'claude'."""
    return os.environ.get("PROMPT_ARM_MODEL_TAG", "claude")

REPO_ROOT = Path("/home/ubuntu/evistream")
EVAL_ROOT = REPO_ROOT / "eval"
BASE_SCHEMAS = EVAL_ROOT / "studies" / "ablation" / "base_schemas"
GT_DIR = EVAL_ROOT / "sheets/gt sheets"
CACHE_DIR = EVAL_ROOT / "sheets"
AIS = EVAL_ROOT / "sheets/ai sheets"
FULL_STUDIES = AIS / "full_studies"
PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"


@dataclass(frozen=True)
class ScalarFormSpec:
    key: str                 # CLI key
    slug: str                # project slug (path component)
    form_label: str          # human label
    schema_json: Path        # base schema_def (multi-signature scalar form)
    registry_module: str     # dotted module holding the scoring registry
    registry_attr: str       # registry attribute name
    registry_key: str        # key into the registry dict
    gt_xlsx: Path            # ground-truth workbook
    markdown_dir: Path       # cached *.md papers (file stem = paper key)
    production_csv: Path      # decomposed arm (production multi-signature extraction on disk)

    @property
    def prompt_path(self) -> Path:
        return PROMPTS_DIR / f"{self.slug}__{self.key}.md"

    @property
    def out_csv(self) -> Path:
        """Value-only CSV (for scoring). Model subdir from PROMPT_ARM_MODEL_TAG (default 'claude')."""
        return AIS / "staged" / self.slug / _model_tag() / f"{self.key}_prompt_long.csv"

    @property
    def grounded_csv(self) -> Path:
        """Grounded CSV (value + per-field source_text); sheets/ai sheets/source_grounding/<slug>/<model>/."""
        return AIS / "source_grounding" / self.slug / _model_tag() / f"{self.key}_prompt_grounded.csv"

    def load_registry_cfg(self) -> dict:
        mod = importlib.import_module(self.registry_module)
        return getattr(mod, self.registry_attr)[self.registry_key]


def _spec(key, slug, label, schema, mod, attr, rkey, gt, cache, prod_name) -> ScalarFormSpec:
    return ScalarFormSpec(
        key=key, slug=slug, form_label=label,
        schema_json=BASE_SCHEMAS / schema,
        registry_module=mod, registry_attr=attr, registry_key=rkey,
        gt_xlsx=GT_DIR / gt,
        markdown_dir=CACHE_DIR / cache,
        production_csv=FULL_STUDIES / slug / "claude" / prod_name,
    )


FORMS: dict[str, ScalarFormSpec] = {
    "perio_study_characteristics": _spec(
        "perio_study_characteristics", "periodontitis", "Study Characteristics",
        "dynamic_99c46506_StudyCharacteristics.json",
        "forms.periodontitis", "PERIODONTITIS_REGISTRY", "perio_study_char",
        "periodontitis.xlsx", "markdown_perio", "form_study_characteristics_long.csv"),
    "perio_patient_population": _spec(
        "perio_patient_population", "periodontitis", "Patient Population",
        "dynamic_f0625cf9_PatientPopulation.json",
        "forms.periodontitis", "PERIODONTITIS_REGISTRY", "perio_patient_pop",
        "periodontitis.xlsx", "markdown_perio", "form_patient_population_long.csv"),
    "abx_study_characteristics": _spec(
        "abx_study_characteristics", "antibiotic", "Study Characteristics",
        "antibiotic_study_characteristics.json",
        "forms.antibiotic", "ANTIBIOTIC_REGISTRY", "abx_study_char",
        "antibiotic_prophylaxis.xlsx", "markdown_antibiotic", "form_study_characteristics_long.csv"),
    "abx_patient_population": _spec(
        "abx_patient_population", "antibiotic", "Patient Population",
        "antibiotic_patient_population.json",
        "forms.antibiotic", "ANTIBIOTIC_REGISTRY", "abx_patient_pop",
        "antibiotic_prophylaxis.xlsx", "markdown_antibiotic", "form_patient_population_long.csv"),
    "oc_study_characteristics": _spec(
        "oc_study_characteristics", "oral_cancer", "Study Characteristics",
        "oral_cancer_study_characteristics.json",
        "forms", "FORM_REGISTRY", "study_characteristics",
        "aligned_forms.xlsx", "markdown_oral_cancer", "form_study_characteristics_long.csv"),
    "oc_patient_population": _spec(
        "oc_patient_population", "oral_cancer", "Patient Population Characteristics",
        "oral_cancer_patient_population.json",
        "forms", "FORM_REGISTRY", "patient_population",
        "aligned_forms.xlsx", "markdown_oral_cancer", "form_patient_population_long.csv"),
    "oc_reference_standard": _spec(
        "oc_reference_standard", "oral_cancer", "Reference Standard",
        "oral_cancer_reference_standard.json",
        "forms", "FORM_REGISTRY", "reference_standard",
        "aligned_forms.xlsx", "markdown_oral_cancer", "form_reference_standard_long.csv"),
    "ibu_study_characteristics": _spec(
        "ibu_study_characteristics", "ibuprofen", "Study Characteristics",
        "ibuprofen_study_characteristics.json",
        "forms.ibuprofen", "IBUPROFEN_REGISTRY", "ibu_study_char",
        "ibuprofen.xlsx", "markdown_Ibuprofen",
        "form_CD015432_—_Study_Characteristics_v3_long.csv"),
    "ibu_patient_population": _spec(
        "ibu_patient_population", "ibuprofen", "Patient Population",
        "ibuprofen_patient_population.json",
        "forms.ibuprofen", "IBUPROFEN_REGISTRY", "ibu_patient_pop",
        "ibuprofen.xlsx", "markdown_Ibuprofen",
        "form_CD015432_—_Patient_Population_long.csv"),
}
