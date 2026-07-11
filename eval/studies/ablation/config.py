"""
Central config for the field-spec ablation experiment.

Variants strip composition components cumulatively from production v3 forms:
    V1: description + options only
    V2: + extraction hints
    V3: + rules
    V4: + examples
    V5: + source-grounding  (== production v3, already extracted)
    V6: V5 + dependency-filtering OFF (optional architecture-uniqueness check)
"""

from dataclasses import dataclass, field
from pathlib import Path

# Hard-coded — eval scripts always run from repo root anyway
REPO_ROOT      = Path("/home/ubuntu/evistream")
EVAL_ROOT      = REPO_ROOT / "eval"
CACHE_DIR      = EVAL_ROOT / "sheets" / "markdown"
MANIFEST_PATH  = EVAL_ROOT / "sheets" / "manifest.json"
AI_SHEETS_DIR  = EVAL_ROOT / "sheets/ai sheets" / "desc_only" / "oral_cancer" / "claude"
OUTPUTS_DIR    = EVAL_ROOT / "outputs" / "desc_only" / "oral_cancer" / "claude"
GT_EXCEL_PATH  = EVAL_ROOT / "sheets/gt sheets" / "combined_oral_cancer_data.xlsx"


@dataclass(frozen=True)
class Variant:
    """One field-spec ablation variant."""
    name: str                   # e.g., "abl1_desc"
    keeps: tuple[str, ...]      # subset of ("description", "hints", "rules", "examples", "source_grounded")
    # strip[*] are passed to signature_splicer.update_schema_def_field as []
    # description and options are always kept (description is the field itself, options are
    # part of the schema's enum semantics — stripping them would make multi-select fields meaningless)


# Cumulative-add ordering: each variant ADDS one component to the previous.
VARIANTS: tuple[Variant, ...] = (
    Variant("abl1_desc",                   keeps=("description",)),
    Variant("abl2_desc_hints",             keeps=("description", "hints")),
    Variant("abl3_desc_hints_rules",       keeps=("description", "hints", "rules")),
    Variant("abl4_desc_hints_rules_examples", keeps=("description", "hints", "rules", "examples")),
    Variant("abl5_full",                   keeps=("description", "hints", "rules", "examples", "source_grounded")),
)

# Components that get stripped per variant (computed from keeps for convenience)
STRIPPABLE_COMPONENTS = ("hints", "rules", "examples", "source_grounded")


def strip_list_for(variant: Variant) -> list[str]:
    """Return component keys to remove for this variant."""
    return [c for c in STRIPPABLE_COMPONENTS if c not in variant.keeps]


# The 4 oral-cancer forms in scope. The base_schema_name is the production
# schema registered in Supabase `schemas` table.
ORAL_CANCER_FORMS: dict[str, dict] = {
    "patient_population": {
        "base_schema_name": "dynamic_db0fd9e3_OralCancerPatientPopulationCharacteristicsV3",
        "eval_module": "eval.engine.forms.patient_population",
        "ai_sheet_full": EVAL_ROOT / "sheets/ai sheets" / "form_Oral_Cancer_—_Patient_Population_Characteristics_v3_long.csv",
    },
    # Phase B forms — uncomment & fill base_schema_name after looking up in DB
    # "study_characteristics": {...},
    # "reference_standard":    {...},
    # "index_test":            {...},
}


def variant_schema_name(base: str, variant: Variant) -> str:
    """Composed schema_name for a registered ablation variant."""
    return f"{base}__{variant.name}"


def variant_csv_path(form: str, variant: Variant) -> Path:
    return AI_SHEETS_DIR / f"{form}_{variant.name}_long.csv"
