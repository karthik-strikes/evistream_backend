"""Project registry for the stage ablation (D3a) — makes run/score project-agnostic.

One entry per project bundles everything the run side (schema_def JSON + cached
markdowns) and the score side (scoring registry + GT workbook) need. Add a new
project by appending a `ProjectConfig` here; nothing else changes.

Schema JSONs live in `eval/studies/ablation/base_schemas/`. Periodontitis reuses the four
existing `dynamic_*.json` files (so its output stays identical); other projects use
deterministic `<slug>_<form_label>.json` names written by
`eval.studies.stage_ablation.export_schema_def`.

IMPORTANT: the scoring registries (forms.periodontitis etc.) are **lazy-loaded** via
`ProjectConfig.load_registry()`, NOT imported at module top. Importing `forms.*`
requires the eval root on `sys.path`, where eval's `config` package would shadow the
**backend's** `config` package and break `DynamicSchemaConfig.build_pipeline()` during
extraction. The run side / notebook never need the registry — only the scorer does, and
the scorer already runs with the eval root on path and never builds pipelines.
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass
from pathlib import Path


REPO_ROOT = Path("/home/ubuntu/evistream")
EVAL_ROOT = REPO_ROOT / "eval"
BASE_SCHEMAS_DIR = EVAL_ROOT / "studies" / "ablation" / "base_schemas"
GT_DIR = EVAL_ROOT / "sheets/gt sheets"
CACHE_DIR = EVAL_ROOT / "sheets"


@dataclass(frozen=True)
class FormSpec:
    """A single form: where its base schema_def JSON lives + its scoring-registry key."""
    schema_json: Path
    registry_key: str


@dataclass(frozen=True)
class ProjectConfig:
    slug: str                       # path component: appears in sheets/ai sheets/outputs paths
    markdown_dir: Path              # cached *.md papers; paper key = file stem (matches GT key col)
    gt_xlsx: Path                   # ground-truth workbook
    registry_module: str            # dotted module holding the scoring registry, e.g. "forms.periodontitis"
    registry_attr: str              # attribute name in that module, e.g. "PERIODONTITIS_REGISTRY"
    forms: dict                     # form label -> FormSpec

    def load_registry(self) -> dict:
        """Lazy-import the scoring registry (scorer-side only — see module docstring)."""
        mod = importlib.import_module(self.registry_module)
        return getattr(mod, self.registry_attr)


def _abx(form_label: str, registry_key: str) -> FormSpec:
    return FormSpec(BASE_SCHEMAS_DIR / f"antibiotic_{form_label}.json", registry_key)


def _oc(form_label: str, registry_key: str) -> FormSpec:
    return FormSpec(BASE_SCHEMAS_DIR / f"oral_cancer_{form_label}.json", registry_key)


PROJECT_REGISTRY: dict[str, ProjectConfig] = {
    "periodontitis": ProjectConfig(
        slug="periodontitis",
        markdown_dir=CACHE_DIR / "markdown_perio",
        gt_xlsx=GT_DIR / "periodontitis.xlsx",
        registry_module="forms.periodontitis",
        registry_attr="PERIODONTITIS_REGISTRY",
        forms={
            "study_characteristics": FormSpec(
                BASE_SCHEMAS_DIR / "dynamic_99c46506_StudyCharacteristics.json",
                "perio_study_char"),
            "patient_population": FormSpec(
                BASE_SCHEMAS_DIR / "dynamic_f0625cf9_PatientPopulation.json",
                "perio_patient_pop"),
            "interventions": FormSpec(
                BASE_SCHEMAS_DIR / "dynamic_7d186a2f_InterventionCharacteristics.json",
                "perio_interventions"),
            "outcomes": FormSpec(
                BASE_SCHEMAS_DIR / "dynamic_6c8eecce_ContinuousOutcomesV2.json",
                "perio_outcomes"),
        },
    ),
    "antibiotic": ProjectConfig(
        slug="antibiotic",
        markdown_dir=CACHE_DIR / "markdown_abx",
        gt_xlsx=GT_DIR / "antibiotic_prophylaxis.xlsx",
        registry_module="forms.antibiotic",
        registry_attr="ANTIBIOTIC_REGISTRY",
        forms={
            "study_characteristics": _abx("study_characteristics", "abx_study_char"),
            "patient_population":     _abx("patient_population", "abx_patient_pop"),
            "interventions":          _abx("interventions", "abx_interventions"),
            "outcomes":               _abx("outcomes", "abx_outcomes"),
        },
    ),
    "oral_cancer": ProjectConfig(
        slug="oral_cancer",
        markdown_dir=CACHE_DIR / "markdown",
        gt_xlsx=GT_DIR / "aligned_forms.xlsx",
        registry_module="forms",
        registry_attr="FORM_REGISTRY",
        forms={
            "study_characteristics": _oc("study_characteristics", "study_characteristics"),
            "patient_population":     _oc("patient_population", "patient_population"),
            "reference_standard":     _oc("reference_standard", "reference_standard"),
            "index_test":             _oc("index_test", "index_test"),
            "outcomes":               _oc("outcomes", "outcomes"),
        },
    ),
}


def get_project(slug: str) -> ProjectConfig:
    if slug not in PROJECT_REGISTRY:
        raise KeyError(f"Unknown project {slug!r}; known: {sorted(PROJECT_REGISTRY)}")
    return PROJECT_REGISTRY[slug]
