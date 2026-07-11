"""Registry of the 5 table forms for the single-call PROMPT-arm ablation (D3b).

Each entry bundles everything the runner needs (schema_def JSON + markdown cache)
and the scorer needs (scoring registry key + GT workbook + production CSV).

The scoring registries (forms.* ) are **lazy-loaded** via `FormSpec.load_registry_cfg()`,
not imported at module top — importing `forms.*` requires the eval root on sys.path
where eval's `config` package would shadow the backend's `config` during any pipeline
build. The runner never needs the registry (it only calls litellm); only the scorer does.
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
PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"
FULL_STUDIES = AIS / "full_studies"


@dataclass(frozen=True)
class FormSpec:
    key: str                 # CLI key
    slug: str                # project slug (path component): periodontitis|antibiotic|oral_cancer
    form_label: str          # human label
    schema_json: Path        # base schema_def (row_then_columns table form)
    registry_module: str     # dotted module holding the scoring registry
    registry_attr: str       # registry attribute name in that module
    registry_key: str        # key into the registry dict
    gt_xlsx: Path            # ground-truth workbook
    markdown_dir: Path       # cached *.md papers (file stem = paper key)
    production_csv: Path      # row_then_columns arm (production extraction on disk)

    @property
    def prompt_path(self) -> Path:
        return PROMPTS_DIR / f"{self.slug}__{self.key}.md"

    @property
    def out_csv(self) -> Path:
        """Single-call PROMPT arm CSV (value-only, for scoring). Model subdir from PROMPT_ARM_MODEL_TAG."""
        return AIS / "table" / self.slug / _model_tag() / f"{self.key}_prompt_long.csv"

    @property
    def grounded_csv(self) -> Path:
        """Grounded CSV (value + per-cell source_text); sheets/ai sheets/source_grounding/<slug>/<model>/."""
        return AIS / "source_grounding" / self.slug / _model_tag() / f"{self.key}_prompt_grounded.csv"

    def load_registry_cfg(self) -> dict:
        """Lazy-import the scoring registry and return this form's config dict."""
        mod = importlib.import_module(self.registry_module)
        return getattr(mod, self.registry_attr)[self.registry_key]


FORMS: dict[str, FormSpec] = {
    "oc_index_test": FormSpec(
        key="oc_index_test", slug="oral_cancer", form_label="Index Test",
        schema_json=BASE_SCHEMAS / "oral_cancer_index_test.json",
        registry_module="forms", registry_attr="FORM_REGISTRY", registry_key="index_test",
        gt_xlsx=GT_DIR / "aligned_forms.xlsx",
        markdown_dir=CACHE_DIR / "markdown_oral_cancer",
        production_csv=FULL_STUDIES / "oral_cancer" / "claude" / "form_index_test_long.csv",
    ),
    "abx_interventions": FormSpec(
        key="abx_interventions", slug="antibiotic", form_label="Intervention Characteristics",
        schema_json=BASE_SCHEMAS / "antibiotic_interventions.json",
        registry_module="forms.antibiotic", registry_attr="ANTIBIOTIC_REGISTRY",
        registry_key="abx_interventions",
        gt_xlsx=GT_DIR / "antibiotic_prophylaxis.xlsx",
        markdown_dir=CACHE_DIR / "markdown_antibiotic",
        production_csv=FULL_STUDIES / "antibiotic" / "claude" / "form_intervention_characteristics_long.csv",
    ),
    "abx_dichotomous_outcomes": FormSpec(
        key="abx_dichotomous_outcomes", slug="antibiotic", form_label="Dichotomous Outcomes",
        schema_json=BASE_SCHEMAS / "antibiotic_outcomes.json",
        registry_module="forms.antibiotic", registry_attr="ANTIBIOTIC_REGISTRY",
        registry_key="abx_outcomes",
        gt_xlsx=GT_DIR / "antibiotic_prophylaxis.xlsx",
        markdown_dir=CACHE_DIR / "markdown_antibiotic",
        production_csv=FULL_STUDIES / "antibiotic" / "claude" / "form_dichotomous_outcomes_long.csv",
    ),
    "perio_continuous_outcomes": FormSpec(
        key="perio_continuous_outcomes", slug="periodontitis", form_label="Continuous Outcomes",
        schema_json=BASE_SCHEMAS / "dynamic_6c8eecce_ContinuousOutcomesV2.json",
        registry_module="forms.periodontitis", registry_attr="PERIODONTITIS_REGISTRY",
        registry_key="perio_outcomes",
        gt_xlsx=GT_DIR / "periodontitis.xlsx",
        markdown_dir=CACHE_DIR / "markdown_perio",
        production_csv=FULL_STUDIES / "periodontitis" / "claude" / "form_continuous_outcomes_long.csv",
    ),
    "perio_interventions": FormSpec(
        key="perio_interventions", slug="periodontitis", form_label="Intervention Characteristics",
        schema_json=BASE_SCHEMAS / "dynamic_7d186a2f_InterventionCharacteristics.json",
        registry_module="forms.periodontitis", registry_attr="PERIODONTITIS_REGISTRY",
        registry_key="perio_interventions",
        gt_xlsx=GT_DIR / "periodontitis.xlsx",
        markdown_dir=CACHE_DIR / "markdown_perio",
        production_csv=FULL_STUDIES / "periodontitis" / "claude" / "form_intervention_characteristics_long.csv",
    ),
    "ibu_interventions": FormSpec(
        key="ibu_interventions", slug="ibuprofen", form_label="Intervention Characteristics",
        schema_json=BASE_SCHEMAS / "ibuprofen_interventions.json",
        registry_module="forms.ibuprofen", registry_attr="IBUPROFEN_REGISTRY",
        registry_key="ibu_interventions",
        gt_xlsx=GT_DIR / "ibuprofen.xlsx",
        markdown_dir=CACHE_DIR / "markdown_Ibuprofen",
        production_csv=FULL_STUDIES / "ibuprofen" / "claude" / "form_CD015432_—_Intervention_Characteristics_long.csv",
    ),
    "ibu_continuous_outcomes": FormSpec(
        key="ibu_continuous_outcomes", slug="ibuprofen", form_label="Continuous Outcomes",
        schema_json=BASE_SCHEMAS / "ibuprofen_continuous_outcomes.json",
        registry_module="forms.ibuprofen", registry_attr="IBUPROFEN_REGISTRY",
        registry_key="ibu_continuous_outcomes",
        gt_xlsx=GT_DIR / "ibuprofen.xlsx",
        markdown_dir=CACHE_DIR / "markdown_Ibuprofen",
        production_csv=FULL_STUDIES / "ibuprofen" / "claude" / "form_CD015432_—_Continuous_Outcomes_v2_long.csv",
    ),
    "ibu_dichotomous_outcomes": FormSpec(
        key="ibu_dichotomous_outcomes", slug="ibuprofen", form_label="Dichotomous Outcomes",
        schema_json=BASE_SCHEMAS / "ibuprofen_dichotomous_outcomes.json",
        registry_module="forms.ibuprofen", registry_attr="IBUPROFEN_REGISTRY",
        registry_key="ibu_dichotomous_outcomes",
        gt_xlsx=GT_DIR / "ibuprofen.xlsx",
        markdown_dir=CACHE_DIR / "markdown_Ibuprofen",
        production_csv=FULL_STUDIES / "ibuprofen" / "claude" / "form_CD015432_—_Dichotomous_Outcomes_long.csv",
    ),
}
