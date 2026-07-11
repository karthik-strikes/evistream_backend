"""
Antibiotic Prophylaxis dataset — form configs + ANTIBIOTIC_REGISTRY.

Parallel to the oral-cancer `FORM_REGISTRY` (forms/__init__.py) but for the
antibiotic-prophylaxis review. The GT lives in a single already-aligned workbook
(sheets/gt sheets/antibiotic_prophylaxis.xlsx) whose column names already equal the
ai_col names, with two quirks the shared loader now handles via params:
  - the study-id key column is `study_id` (not `Paper`)        → gt_key_col
  - row 1 is a directive row (EXTRACT/OPTIONAL/NEGLECT/META)    → gt_skiprows=[1]

Field strategies follow that directive row: EXTRACT/OPTIONAL fields are scored
with a type-appropriate strategy; NEGLECT/META fields are marked `skip` (the
notebook's INCLUDE_SKIPPED toggle can still promote them to llm_judge per-run).

Form names are prefixed `abx_` so their cached match tables / per-form reports
never collide with the oral-cancer forms. `abx_study_char` is the canonical
study-matching table the other three antibiotic forms inherit from.
"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from config.field_config import (
    FieldSpec,
    STRATEGY_EXACT_NORMALIZE, STRATEGY_NUMERIC_EXACT, STRATEGY_NUMERIC_TOLERANCE,
    STRATEGY_LLM_JUDGE, STRATEGY_SKIP, STRATEGY_EXCLUDE,
)

ABX_GT_KEY_COL = "study_id"
ABX_GT_SKIPROWS = [1]            # drop the EXTRACT/NEGLECT/OPTIONAL/META directive row
ABX_CANONICAL_FORM = "abx_study_char"

_ABX_COMMON = {
    "ai_format":        "csv",
    "gt_key_col":       ABX_GT_KEY_COL,
    "gt_skiprows":      ABX_GT_SKIPROWS,
    "canonical_form":   ABX_CANONICAL_FORM,
}


# ── abx_study_char (level 1, canonical) ──────────────────────────────────────────
STUDY_CHAR_FIELDS = [
    FieldSpec(ai_col="country",             strategy=STRATEGY_LLM_JUDGE),
    FieldSpec(ai_col="setting_institution", strategy=STRATEGY_LLM_JUDGE),
    FieldSpec(ai_col="n_enrolled",          strategy=STRATEGY_NUMERIC_EXACT),
    FieldSpec(ai_col="publication_type",    strategy=STRATEGY_EXACT_NORMALIZE),
    FieldSpec(ai_col="surgery_type",        strategy=STRATEGY_LLM_JUDGE),
    # NEGLECT in GT → skip (INCLUDE_SKIPPED can promote)
    FieldSpec(ai_col="year",                strategy=STRATEGY_SKIP),
    FieldSpec(ai_col="design",              strategy=STRATEGY_SKIP),
]
STUDY_CHAR_CFG = {
    **_ABX_COMMON,
    "fields":           STUDY_CHAR_FIELDS,
    "ai_filename":      "full_studies/antibiotic/claude/form_study_characteristics_long.csv",
    "gt_section":       "study_char",
    "gt_aligned_sheet": "study_char",
    "level2":           False,
}


# ── abx_patient_pop (level 1) ────────────────────────────────────────────────────
PATIENT_POP_FIELDS = [
    FieldSpec(ai_col="age_central_tendency_type", strategy=STRATEGY_EXACT_NORMALIZE),
    FieldSpec(ai_col="age_value",                 strategy=STRATEGY_NUMERIC_TOLERANCE, tolerance=0.5),
    FieldSpec(ai_col="age_sd",                    strategy=STRATEGY_NUMERIC_EXACT),
    FieldSpec(ai_col="age_range",                 strategy=STRATEGY_EXACT_NORMALIZE),
    FieldSpec(ai_col="pct_male",                  strategy=STRATEGY_NUMERIC_TOLERANCE, tolerance=1.0),
    FieldSpec(ai_col="inclusion_criteria",        strategy=STRATEGY_LLM_JUDGE),
    FieldSpec(ai_col="exclusion_criteria",        strategy=STRATEGY_LLM_JUDGE),
    # NEGLECT in GT → skip
    FieldSpec(ai_col="pct_female",                strategy=STRATEGY_SKIP),
]
PATIENT_POP_CFG = {
    **_ABX_COMMON,
    "fields":           PATIENT_POP_FIELDS,
    "ai_filename":      "full_studies/antibiotic/claude/form_patient_population_long.csv",
    "gt_section":       "patient_pop",
    "gt_aligned_sheet": "patient_pop",
    "level2":           False,
}


# ── abx_interventions (level 2 — one row per study arm) ───────────────────────────
INTERVENTIONS_FIELDS = [
    FieldSpec(ai_col="antibiotic_name",      strategy=STRATEGY_LLM_JUDGE),
    FieldSpec(ai_col="dose",                 strategy=STRATEGY_LLM_JUDGE),
    FieldSpec(ai_col="route",                strategy=STRATEGY_EXACT_NORMALIZE),
    # Free-text dosing schedule with heavy abbreviation/verbosity variance
    # ("q6h" vs "every 6 hours", multi-phase regimens) — exact string match
    # scored 0.22; semantic judge handles units/abbreviations/detail level.
    FieldSpec(ai_col="frequency",            strategy=STRATEGY_LLM_JUDGE),
    FieldSpec(ai_col="duration",             strategy=STRATEGY_LLM_JUDGE),
    FieldSpec(ai_col="n_in_arm",             strategy=STRATEGY_NUMERIC_EXACT),
    FieldSpec(ai_col="allergy_substitution", strategy=STRATEGY_LLM_JUDGE),
    # arm matching keys — not scored as fields
    FieldSpec(ai_col="arm_label",            strategy=STRATEGY_SKIP),
    FieldSpec(ai_col="arm_category",         strategy=STRATEGY_SKIP),
]
INTERVENTIONS_CFG = {
    **_ABX_COMMON,
    "fields":           INTERVENTIONS_FIELDS,
    "ai_filename":      "full_studies/antibiotic/claude/form_intervention_characteristics_long.csv",
    "gt_section":       "interventions",
    "gt_aligned_sheet": "interventions",
    "level2":           True,
    "level2_type_key":  "arm_category",
    "level2_label_key": "arm_label",
}


# ── abx_outcomes (level 2 — one row per comparison/outcome) ───────────────────────
OUTCOMES_FIELDS = [
    FieldSpec(ai_col="outcome",               strategy=STRATEGY_EXACT_NORMALIZE),
    FieldSpec(ai_col="comparison_label",      strategy=STRATEGY_EXACT_NORMALIZE),
    FieldSpec(ai_col="arm1_label",            strategy=STRATEGY_LLM_JUDGE),
    FieldSpec(ai_col="arm1_events",           strategy=STRATEGY_NUMERIC_EXACT),
    FieldSpec(ai_col="arm1_n",                strategy=STRATEGY_NUMERIC_EXACT),
    FieldSpec(ai_col="arm2_label",            strategy=STRATEGY_LLM_JUDGE),
    FieldSpec(ai_col="arm2_events",           strategy=STRATEGY_NUMERIC_EXACT),
    FieldSpec(ai_col="arm2_n",                strategy=STRATEGY_NUMERIC_EXACT),
    FieldSpec(ai_col="ssi_definition_source", strategy=STRATEGY_EXACT_NORMALIZE),
    FieldSpec(ai_col="follow_up_duration",    strategy=STRATEGY_LLM_JUDGE),
    # OPTIONAL free-text catch-all with no consistent GT — EXCLUDE so it can't be
    # promoted to llm_judge and drag the macro F1 (scored 0.30 when promoted).
    FieldSpec(ai_col="notes",                 strategy=STRATEGY_EXCLUDE),
]
OUTCOMES_CFG = {
    **_ABX_COMMON,
    "fields":           OUTCOMES_FIELDS,
    "ai_filename":      "full_studies/antibiotic/claude/form_dichotomous_outcomes_long.csv",
    "gt_section":       "outcomes",
    "gt_aligned_sheet": "outcomes",
    "level2":           True,
    "level2_type_key":  "outcome",
    "level2_label_key": "comparison_label",
}


ANTIBIOTIC_REGISTRY: dict[str, dict] = {
    "abx_study_char":   STUDY_CHAR_CFG,
    "abx_patient_pop":  PATIENT_POP_CFG,
    "abx_interventions": INTERVENTIONS_CFG,
    "abx_outcomes":     OUTCOMES_CFG,
}
