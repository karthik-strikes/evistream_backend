import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from config.field_config import (
    FieldSpec,
    STRATEGY_EXACT_NORMALIZE, STRATEGY_NUMERIC_EXACT, STRATEGY_NUMERIC_TOLERANCE,
    STRATEGY_SET_TERMS, STRATEGY_LLM_JUDGE, STRATEGY_SKIP, STRATEGY_EXCLUDE,
)
from config.paths import AI_DATA_DIR, ALIGNED_GT_PATH
from forms.base_form import run_form

FIELDS: list[FieldSpec] = [
    FieldSpec(ai_col="population_transformed",         strategy=STRATEGY_EXCLUDE),
    FieldSpec(ai_col="age_central_tendency_type",      strategy=STRATEGY_EXACT_NORMALIZE),
    FieldSpec(ai_col="age_central_tendency_value",     strategy=STRATEGY_NUMERIC_TOLERANCE, tolerance=0.5),
    FieldSpec(ai_col="age_sd",                         strategy=STRATEGY_NUMERIC_EXACT),
    FieldSpec(ai_col="age_range",                      strategy=STRATEGY_EXACT_NORMALIZE),
    FieldSpec(ai_col="n_total_patients",               strategy=STRATEGY_NUMERIC_EXACT),
    FieldSpec(ai_col="n_female",                       strategy=STRATEGY_NUMERIC_EXACT),
    FieldSpec(ai_col="pct_female",                     strategy=STRATEGY_NUMERIC_TOLERANCE, tolerance=1.0),
    FieldSpec(ai_col="n_male",                         strategy=STRATEGY_NUMERIC_EXACT),
    FieldSpec(ai_col="pct_male",                       strategy=STRATEGY_NUMERIC_TOLERANCE, tolerance=1.0),
    FieldSpec(ai_col="patient_population_categories",  strategy=STRATEGY_SET_TERMS, vocab="POPULATION_CATEGORIES"),
    FieldSpec(ai_col="severity_target_condition",      strategy=STRATEGY_LLM_JUDGE),
    FieldSpec(ai_col="site_target_condition",          strategy=STRATEGY_SET_TERMS, vocab="ANATOMY_SITES"),
    FieldSpec(ai_col="risk_factors_original",          strategy=STRATEGY_SET_TERMS, vocab="RISK_FACTORS"),
    FieldSpec(ai_col="ses_population",                 strategy=STRATEGY_LLM_JUDGE),
    FieldSpec(ai_col="ethnicity_population",           strategy=STRATEGY_LLM_JUDGE),
    FieldSpec(ai_col="method_of_patient_selection",    strategy=STRATEGY_LLM_JUDGE),
    FieldSpec(ai_col="patient_population_innocuous_comment",  strategy=STRATEGY_EXCLUDE),
    FieldSpec(ai_col="patient_population_suspicious_comment", strategy=STRATEGY_EXCLUDE),
    FieldSpec(ai_col="target_condition_opmd_comment",         strategy=STRATEGY_EXCLUDE),
    FieldSpec(ai_col="target_condition_oscc_comment",         strategy=STRATEGY_EXCLUDE),
    # exclude / skip
    FieldSpec(ai_col="risk_factors_transformed",              strategy=STRATEGY_EXCLUDE),
]

CFG = {
    "fields":           FIELDS,
    "ai_filename":      "full_studies/oral_cancer/claude/form_patient_population_long.csv",
    "ai_format":        "csv",
    "gt_section":       "PATIENT POPULATION CHARACTERISTICS",
    "gt_aligned_sheet": "patient_pop",
    "level2":           False,
    "sheet_extra_cols": [
        "patient_population_innocuous_comment",
        "patient_population_suspicious_comment",
        "target_condition_opmd_comment",
        "target_condition_oscc_comment",
    ],
}


def run(use_llm=True, force_rematch=False):
    return run_form(
        form_name="patient_population",
        fields=CFG["fields"],
        ai_path=os.path.join(AI_DATA_DIR, CFG["ai_filename"]),
        ai_format=CFG["ai_format"],
        gt_section=CFG["gt_section"],
        level2=CFG.get("level2", False),
        level2_cfg=CFG if CFG.get("level2") else None,
        use_llm=use_llm,
        force_rematch=force_rematch,
        gt_aligned_sheet=CFG.get("gt_aligned_sheet"),
        aligned_gt_path=ALIGNED_GT_PATH,
    )
