"""
Ibuprofen dataset (Cochrane review CD015432) — form configs + IBUPROFEN_REGISTRY.

Parallel to forms/periodontitis.py and forms/antibiotic.py but for the
"Ibuprofen for acute postoperative pain in children" review (CD015432.pub2, 2024).
GT lives in `sheets/gt sheets/ibuprofen.xlsx`, one already-aligned workbook whose
column names equal the ai_col names, with the same two quirks the shared loader
handles via params:
  - the study-id key column is `study_id` (not `Paper`)        → gt_key_col
  - row 1 is a directive row (EXTRACT/OPTIONAL/NEGLECT/META)    → gt_skiprows=[1]

Unlike the other two datasets this review reports BOTH continuous (pain intensity,
opioid consumption, time-to-rescue) and dichotomous (adverse events, rescue
medication, nausea/vomiting, bleeding, renal dysfunction) outcomes, so it has two
outcome forms:
  - ibu_continuous_outcomes  (mean/sd/n per arm)   → mirrors perio_outcomes
  - ibu_dichotomous_outcomes (events/n per arm)    → mirrors abx_outcomes

Field strategies follow the directive row: EXTRACT fields get a type-appropriate
strategy; OPTIONAL / NEGLECT / META fields are marked `skip` (the notebook's
INCLUDE_SKIPPED toggle can still promote them to llm_judge per-run).

Form names are prefixed `ibu_` so caches / per-form reports never collide with the
other datasets. `ibu_study_char` is the canonical study-matching table the other
five forms inherit from.
"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from config.field_config import (
    FieldSpec,
    STRATEGY_EXACT_NORMALIZE, STRATEGY_NUMERIC_EXACT, STRATEGY_NUMERIC_TOLERANCE,
    STRATEGY_LLM_JUDGE, STRATEGY_SKIP, STRATEGY_EXCLUDE,
)

IBU_GT_KEY_COL = "study_id"
IBU_GT_SKIPROWS = [1]            # drop the EXTRACT/NEGLECT/OPTIONAL/META directive row
IBU_CANONICAL_FORM = "ibu_study_char"

_IBU_COMMON = {
    "ai_format":        "csv",
    "gt_key_col":       IBU_GT_KEY_COL,
    "gt_skiprows":      IBU_GT_SKIPROWS,
    "canonical_form":   IBU_CANONICAL_FORM,
}


# ── ibu_study_char (level 1, canonical) ──────────────────────────────────────────
STUDY_CHAR_FIELDS = [
    FieldSpec(ai_col="country",                 strategy=STRATEGY_LLM_JUDGE),
    FieldSpec(ai_col="surgery_type",            strategy=STRATEGY_LLM_JUDGE),
    FieldSpec(ai_col="route_of_administration", strategy=STRATEGY_EXACT_NORMALIZE),
    # Outcomes the study measured (the review's Table 1 "Outcomes" column) — free text
    FieldSpec(ai_col="outcomes_measured",       strategy=STRATEGY_LLM_JUDGE),
    FieldSpec(ai_col="trial_design",            strategy=STRATEGY_LLM_JUDGE),
    FieldSpec(ai_col="number_of_centres",       strategy=STRATEGY_EXACT_NORMALIZE),
    # OPTIONAL — funder is reported for most studies but is free-text; not scored by default
    FieldSpec(ai_col="funding_source",          strategy=STRATEGY_SKIP),
    # NB: CD015432's Characteristics tables don't report a care-setting field, and the
    # free-text `notes` catch-all had no consistent GT — both were 100% NR, so dropped.
]
STUDY_CHAR_CFG = {
    **_IBU_COMMON,
    "fields":           STUDY_CHAR_FIELDS,
    "ai_filename":      "full_studies/ibuprofen/claude/form_study_characteristics_long.csv",
    "gt_section":       "study_char",
    "gt_aligned_sheet": "study_char",
    "level2":           False,
}


# ── ibu_patient_pop (level 1) ────────────────────────────────────────────────────
PATIENT_POP_FIELDS = [
    FieldSpec(ai_col="age_central_tendency_type", strategy=STRATEGY_EXACT_NORMALIZE),
    FieldSpec(ai_col="age_value",                 strategy=STRATEGY_NUMERIC_TOLERANCE, tolerance=0.5),
    FieldSpec(ai_col="age_sd",                    strategy=STRATEGY_NUMERIC_EXACT),
    # free-text range ("3-5.2 years") — semantic judge is robust to format variance
    # ("3 to 5.2 y") where exact_normalize would spuriously fail.
    FieldSpec(ai_col="age_range",                 strategy=STRATEGY_LLM_JUDGE),
    FieldSpec(ai_col="n_randomised",              strategy=STRATEGY_NUMERIC_EXACT),
    # NB: CD015432's Participants tables report only N, age, and surgery type — not sex %,
    # eligibility criteria, ASA class, or analysed-N — so those columns (100% NR) are dropped.
]
PATIENT_POP_CFG = {
    **_IBU_COMMON,
    "fields":           PATIENT_POP_FIELDS,
    "ai_filename":      "full_studies/ibuprofen/claude/form_patient_population_long.csv",
    "gt_section":       "patient_pop",
    "gt_aligned_sheet": "patient_pop",
    "level2":           False,
}


# ── ibu_interventions (level 2 — one row per study arm) ───────────────────────────
INTERVENTIONS_FIELDS = [
    FieldSpec(ai_col="drug_name",          strategy=STRATEGY_LLM_JUDGE),
    FieldSpec(ai_col="dose",               strategy=STRATEGY_LLM_JUDGE),
    FieldSpec(ai_col="route",              strategy=STRATEGY_EXACT_NORMALIZE),
    # Free-text dosing schedule with heavy abbreviation/verbosity variance
    # ("q6h" vs "every 6 hours") — semantic judge handles units/abbreviations.
    FieldSpec(ai_col="frequency",          strategy=STRATEGY_LLM_JUDGE),
    FieldSpec(ai_col="n_in_arm",           strategy=STRATEGY_NUMERIC_EXACT),
    FieldSpec(ai_col="comparison_summary", strategy=STRATEGY_LLM_JUDGE),
    # arm-matching keys — not scored as fields
    FieldSpec(ai_col="arm_label",          strategy=STRATEGY_SKIP),
    FieldSpec(ai_col="arm_category",       strategy=STRATEGY_SKIP),
]
INTERVENTIONS_CFG = {
    **_IBU_COMMON,
    "fields":           INTERVENTIONS_FIELDS,
    "ai_filename":      "full_studies/ibuprofen/claude/form_intervention_characteristics_long.csv",
    "gt_section":       "interventions",
    "gt_aligned_sheet": "interventions",
    "level2":           True,
    "level2_type_key":  "arm_category",
    "level2_label_key": "arm_label",
}


# ── ibu_continuous_outcomes (level 2 — pain intensity / opioid use / time-to-rescue)
CONTINUOUS_OUTCOMES_FIELDS = [
    FieldSpec(ai_col="comparison",   strategy=STRATEGY_EXACT_NORMALIZE),
    FieldSpec(ai_col="outcome_type", strategy=STRATEGY_EXACT_NORMALIZE),
    FieldSpec(ai_col="reporter",     strategy=STRATEGY_EXACT_NORMALIZE),
    FieldSpec(ai_col="timepoint",    strategy=STRATEGY_EXACT_NORMALIZE),
    FieldSpec(ai_col="mean_arm1",    strategy=STRATEGY_NUMERIC_TOLERANCE, tolerance=0.1),
    FieldSpec(ai_col="sd_arm1",      strategy=STRATEGY_NUMERIC_TOLERANCE, tolerance=0.1),
    FieldSpec(ai_col="n_arm1",       strategy=STRATEGY_NUMERIC_EXACT),
    FieldSpec(ai_col="mean_arm2",    strategy=STRATEGY_NUMERIC_TOLERANCE, tolerance=0.1),
    FieldSpec(ai_col="sd_arm2",      strategy=STRATEGY_NUMERIC_TOLERANCE, tolerance=0.1),
    FieldSpec(ai_col="n_arm2",       strategy=STRATEGY_NUMERIC_EXACT),
    # OPTIONAL — scale is not in the forest plots (SMD used precisely b/c scales vary)
    FieldSpec(ai_col="scale",        strategy=STRATEGY_SKIP),
]
CONTINUOUS_OUTCOMES_CFG = {
    **_IBU_COMMON,
    "fields":           CONTINUOUS_OUTCOMES_FIELDS,
    "ai_filename":      "full_studies/ibuprofen/claude/form_continuous_outcomes_long.csv",
    "gt_section":       "continuous_outcomes",
    "gt_aligned_sheet": "continuous_outcomes",
    "level2":           True,
    "level2_type_key":  "outcome_type",
    "level2_label_key": "comparison",
    # outcome_type+comparison alone don't disambiguate rows (same outcome recurs
    # across timepoints/reporters), so fold both into arm-assignment similarity.
    "level2_extra_keys": ["timepoint", "reporter"],
}


# ── ibu_dichotomous_outcomes (level 2 — adverse events / rescue med / AEs) ─────────
DICHOTOMOUS_OUTCOMES_FIELDS = [
    FieldSpec(ai_col="comparison",   strategy=STRATEGY_EXACT_NORMALIZE),
    FieldSpec(ai_col="outcome",      strategy=STRATEGY_EXACT_NORMALIZE),
    FieldSpec(ai_col="timepoint",    strategy=STRATEGY_EXACT_NORMALIZE),
    FieldSpec(ai_col="arm1_label",   strategy=STRATEGY_LLM_JUDGE),
    FieldSpec(ai_col="arm1_events",  strategy=STRATEGY_NUMERIC_EXACT),
    FieldSpec(ai_col="arm1_n",       strategy=STRATEGY_NUMERIC_EXACT),
    FieldSpec(ai_col="arm2_label",   strategy=STRATEGY_LLM_JUDGE),
    FieldSpec(ai_col="arm2_events",  strategy=STRATEGY_NUMERIC_EXACT),
    FieldSpec(ai_col="arm2_n",       strategy=STRATEGY_NUMERIC_EXACT),
]
DICHOTOMOUS_OUTCOMES_CFG = {
    **_IBU_COMMON,
    "fields":           DICHOTOMOUS_OUTCOMES_FIELDS,
    "ai_filename":      "full_studies/ibuprofen/claude/form_dichotomous_outcomes_long.csv",
    "gt_section":       "dichotomous_outcomes",
    "gt_aligned_sheet": "dichotomous_outcomes",
    "level2":           True,
    "level2_type_key":  "outcome",
    "level2_label_key": "comparison",
    "level2_extra_keys": ["timepoint"],
}


# ── ibu_risk_of_bias (level 1 — 7 Cochrane RoB-1 domains) ─────────────────────────
_ROB_DOMAINS = [
    "random_sequence_generation", "allocation_concealment",
    "blinding_participants_personnel", "blinding_outcome_assessment",
    "incomplete_outcome_data", "selective_reporting", "other_bias",
]
RISK_OF_BIAS_FIELDS = []
for _d in _ROB_DOMAINS:
    RISK_OF_BIAS_FIELDS.append(FieldSpec(ai_col=f"{_d}_judgment", strategy=STRATEGY_EXACT_NORMALIZE))
    RISK_OF_BIAS_FIELDS.append(FieldSpec(ai_col=f"{_d}_reason",   strategy=STRATEGY_LLM_JUDGE))
RISK_OF_BIAS_CFG = {
    **_IBU_COMMON,
    "fields":           RISK_OF_BIAS_FIELDS,
    "ai_filename":      "full_studies/ibuprofen/claude/form_risk_of_bias_long.csv",
    "gt_section":       "risk_of_bias",
    "gt_aligned_sheet": "risk_of_bias",
    "level2":           False,
}


IBUPROFEN_REGISTRY: dict[str, dict] = {
    "ibu_study_char":            STUDY_CHAR_CFG,
    "ibu_patient_pop":           PATIENT_POP_CFG,
    "ibu_interventions":         INTERVENTIONS_CFG,
    "ibu_continuous_outcomes":   CONTINUOUS_OUTCOMES_CFG,
    "ibu_dichotomous_outcomes":  DICHOTOMOUS_OUTCOMES_CFG,
    "ibu_risk_of_bias":          RISK_OF_BIAS_CFG,
}
