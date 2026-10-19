"""
Periodontitis dataset (Cochrane review CD004714) — form configs + PERIODONTITIS_REGISTRY.

Parallel to forms/antibiotic.py but for the periodontal-treatment / glycaemic-control
review (continuous outcomes — HbA1c, CAL, PPD, BOP, PI, GI). GT lives in
`sheets/gt sheets/periodontitis.xlsx`, one already-aligned workbook whose column names equal
the ai_col names, with the same two quirks the shared loader handles via params:
  - the study-id key column is `study_id` (not `Paper`)        → gt_key_col
  - row 1 is a directive row (EXTRACT/OPTIONAL/NEGLECT/META)    → gt_skiprows=[1]

Field strategies follow that directive row: EXTRACT fields get a type-appropriate
strategy; OPTIONAL / NEGLECT / META fields are marked `skip` (the notebook's
INCLUDE_SKIPPED toggle can still promote them to llm_judge per-run).

Form names are prefixed `perio_` so caches / per-form reports never collide with the
other datasets. `perio_study_char` is the canonical study-matching table the other
four forms inherit from.
"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from config.field_config import (
    FieldSpec,
    STRATEGY_EXACT_NORMALIZE, STRATEGY_NUMERIC_EXACT, STRATEGY_NUMERIC_TOLERANCE,
    STRATEGY_LLM_JUDGE, STRATEGY_SKIP, STRATEGY_EXCLUDE, STRATEGY_DURATION,
)

PERIO_GT_KEY_COL = "study_id"
PERIO_GT_SKIPROWS = [1]            # drop the EXTRACT/NEGLECT/OPTIONAL/META directive row
PERIO_CANONICAL_FORM = "perio_study_char"

_PERIO_COMMON = {
    "ai_format":        "csv",
    "gt_key_col":       PERIO_GT_KEY_COL,
    "gt_skiprows":      PERIO_GT_SKIPROWS,
    "canonical_form":   PERIO_CANONICAL_FORM,
}


# ── perio_study_char (level 1, canonical) ────────────────────────────────────────
STUDY_CHAR_FIELDS = [
    FieldSpec(ai_col="country",            strategy=STRATEGY_LLM_JUDGE),
    FieldSpec(ai_col="setting",            strategy=STRATEGY_LLM_JUDGE),
    FieldSpec(ai_col="number_of_centres",  strategy=STRATEGY_EXACT_NORMALIZE),
    FieldSpec(ai_col="trial_design",       strategy=STRATEGY_LLM_JUDGE),
    # OPTIONAL in GT → skip (INCLUDE_SKIPPED can promote)
    FieldSpec(ai_col="recruitment_period", strategy=STRATEGY_SKIP),
    FieldSpec(ai_col="funding_source",     strategy=STRATEGY_SKIP),
    # Free-text catch-all with no consistent GT — never scorable; EXCLUDE so the
    # notebook's INCLUDE_SKIPPED toggle can't promote it to llm_judge.
    FieldSpec(ai_col="notes",              strategy=STRATEGY_EXCLUDE),
]
STUDY_CHAR_CFG = {
    **_PERIO_COMMON,
    "fields":           STUDY_CHAR_FIELDS,
    "ai_filename":      "full_studies/periodontitis/claude/form_study_characteristics_long.csv",
    "gt_section":       "study_char",
    "gt_aligned_sheet": "study_char",
    "level2":           False,
}


# ── perio_patient_pop (level 1) ──────────────────────────────────────────────────
PATIENT_POP_FIELDS = [
    FieldSpec(ai_col="age_arm_a",             strategy=STRATEGY_NUMERIC_TOLERANCE, tolerance=0.5),
    FieldSpec(ai_col="age_arm_a_sd",          strategy=STRATEGY_NUMERIC_EXACT),
    FieldSpec(ai_col="age_arm_b",             strategy=STRATEGY_NUMERIC_TOLERANCE, tolerance=0.5),
    FieldSpec(ai_col="age_arm_b_sd",          strategy=STRATEGY_NUMERIC_EXACT),
    FieldSpec(ai_col="pct_female_arm_a",      strategy=STRATEGY_NUMERIC_TOLERANCE, tolerance=1.0),
    FieldSpec(ai_col="pct_female_arm_b",      strategy=STRATEGY_NUMERIC_TOLERANCE, tolerance=1.0),
    FieldSpec(ai_col="diabetes_type",         strategy=STRATEGY_EXACT_NORMALIZE),
    FieldSpec(ai_col="baseline_hba1c_arm_a",  strategy=STRATEGY_NUMERIC_TOLERANCE, tolerance=0.1),
    FieldSpec(ai_col="baseline_hba1c_arm_b",  strategy=STRATEGY_NUMERIC_TOLERANCE, tolerance=0.1),
    FieldSpec(ai_col="inclusion_criteria",    strategy=STRATEGY_LLM_JUDGE),
    FieldSpec(ai_col="exclusion_criteria",    strategy=STRATEGY_LLM_JUDGE),
    FieldSpec(ai_col="n_randomised",          strategy=STRATEGY_NUMERIC_EXACT),
    FieldSpec(ai_col="n_evaluated",           strategy=STRATEGY_LLM_JUDGE),
    # OPTIONAL in GT → skip
    FieldSpec(ai_col="metabolic_control_level",   strategy=STRATEGY_SKIP),
    FieldSpec(ai_col="duration_since_diabetes_dx", strategy=STRATEGY_SKIP),
    FieldSpec(ai_col="tobacco_use",               strategy=STRATEGY_SKIP),
    FieldSpec(ai_col="alcohol_consumption",       strategy=STRATEGY_SKIP),
]
PATIENT_POP_CFG = {
    **_PERIO_COMMON,
    "fields":           PATIENT_POP_FIELDS,
    "ai_filename":      "full_studies/periodontitis/claude/form_patient_population_long.csv",
    "gt_section":       "patient_pop",
    "gt_aligned_sheet": "patient_pop",
    "level2":           False,
}


# ── perio_interventions (level 2 — one row per study arm) ─────────────────────────
INTERVENTIONS_FIELDS = [
    FieldSpec(ai_col="comparison_summary",       strategy=STRATEGY_LLM_JUDGE),
    FieldSpec(ai_col="intervention_description", strategy=STRATEGY_LLM_JUDGE),
    FieldSpec(ai_col="n_in_arm",                 strategy=STRATEGY_NUMERIC_EXACT),
    FieldSpec(ai_col="duration_of_followup",     strategy=STRATEGY_DURATION, tolerance=15),
    # arm-matching keys / OPTIONAL — not scored as fields
    FieldSpec(ai_col="arm_label",                strategy=STRATEGY_SKIP),
    FieldSpec(ai_col="cochrane_subgroup_category", strategy=STRATEGY_SKIP),
]
INTERVENTIONS_CFG = {
    **_PERIO_COMMON,
    "fields":           INTERVENTIONS_FIELDS,
    "ai_filename":      "full_studies/periodontitis/claude/form_intervention_characteristics_long.csv",
    "gt_section":       "interventions",
    "gt_aligned_sheet": "interventions",
    "level2":           True,
    "level2_type_key":  "cochrane_subgroup_category",
    "level2_label_key": "arm_label",
}


# ── perio_outcomes (level 2 — one row per outcome × timepoint × subgroup) ─────────
OUTCOMES_FIELDS = [
    FieldSpec(ai_col="outcome_type",  strategy=STRATEGY_EXACT_NORMALIZE),
    FieldSpec(ai_col="timepoint",     strategy=STRATEGY_EXACT_NORMALIZE),
    FieldSpec(ai_col="mean_arm1",     strategy=STRATEGY_NUMERIC_TOLERANCE, tolerance=0.1),
    FieldSpec(ai_col="sd_arm1",       strategy=STRATEGY_NUMERIC_TOLERANCE, tolerance=0.1),
    FieldSpec(ai_col="n_arm1",        strategy=STRATEGY_NUMERIC_EXACT),
    FieldSpec(ai_col="mean_arm2",     strategy=STRATEGY_NUMERIC_TOLERANCE, tolerance=0.1),
    FieldSpec(ai_col="sd_arm2",       strategy=STRATEGY_NUMERIC_TOLERANCE, tolerance=0.1),
    FieldSpec(ai_col="n_arm2",        strategy=STRATEGY_NUMERIC_EXACT),
    # subgroup is the matching label key / OPTIONAL; effect_* are NEGLECT → skip
    FieldSpec(ai_col="subgroup",      strategy=STRATEGY_SKIP),
    FieldSpec(ai_col="effect_measure", strategy=STRATEGY_SKIP),
    FieldSpec(ai_col="effect_value",   strategy=STRATEGY_SKIP),
    FieldSpec(ai_col="ci_95_low",      strategy=STRATEGY_SKIP),
    FieldSpec(ai_col="ci_95_high",     strategy=STRATEGY_SKIP),
]
OUTCOMES_CFG = {
    **_PERIO_COMMON,
    "fields":           OUTCOMES_FIELDS,
    "ai_filename":      "full_studies/periodontitis/claude/form_continuous_outcomes_long.csv",
    "gt_section":       "outcomes",
    "gt_aligned_sheet": "outcomes",
    "level2":           True,
    "level2_type_key":  "outcome_type",
    "level2_label_key": "subgroup",
    # subgroup is uniform within many studies, so fold timepoint into the
    # arm-assignment similarity to disambiguate outcome rows (fixes alignment).
    "level2_extra_keys": ["timepoint"],
}


# ── perio_risk_of_bias (level 1 — 8 Cochrane ROB domains) ─────────────────────────
_ROB_DOMAINS = [
    "random_sequence_generation", "allocation_concealment",
    "blinding_participants", "blinding_clinical_operator",
    "blinding_outcome_assessor", "incomplete_outcome_data",
    "selective_reporting", "other_bias",
]
RISK_OF_BIAS_FIELDS = []
for _d in _ROB_DOMAINS:
    RISK_OF_BIAS_FIELDS.append(FieldSpec(ai_col=f"{_d}_judgment", strategy=STRATEGY_EXACT_NORMALIZE))
    RISK_OF_BIAS_FIELDS.append(FieldSpec(ai_col=f"{_d}_reason",   strategy=STRATEGY_LLM_JUDGE))
RISK_OF_BIAS_CFG = {
    **_PERIO_COMMON,
    "fields":           RISK_OF_BIAS_FIELDS,
    "ai_filename":      "full_studies/periodontitis/claude/form_risk_of_bias_long.csv",
    "gt_section":       "risk_of_bias",
    "gt_aligned_sheet": "risk_of_bias",
    "level2":           False,
}


PERIODONTITIS_REGISTRY: dict[str, dict] = {
    "perio_study_char":    STUDY_CHAR_CFG,
    "perio_patient_pop":   PATIENT_POP_CFG,
    "perio_interventions": INTERVENTIONS_CFG,
    "perio_outcomes":      OUTCOMES_CFG,
    "perio_risk_of_bias":  RISK_OF_BIAS_CFG,
}
