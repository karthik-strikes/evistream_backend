import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from config.field_config import (
    FieldSpec,
    STRATEGY_EXACT_NORMALIZE, STRATEGY_NUMERIC_EXACT, STRATEGY_NUMERIC_TOLERANCE,
    STRATEGY_PARSE_PERCENT, STRATEGY_SKIP,
)
from config.paths import AI_DATA_DIR, ALIGNED_GT_PATH
from forms.base_form import run_form

FIELDS: list[FieldSpec] = [
    FieldSpec(ai_col="unit_of_analysis",         strategy=STRATEGY_EXACT_NORMALIZE),
    FieldSpec(ai_col="linked_index_test",        strategy=STRATEGY_EXACT_NORMALIZE),
    FieldSpec(ai_col="outcome_target_condition", strategy=STRATEGY_EXACT_NORMALIZE),
    FieldSpec(ai_col="tp", strategy=STRATEGY_NUMERIC_EXACT),
    FieldSpec(ai_col="fp", strategy=STRATEGY_NUMERIC_EXACT),
    FieldSpec(ai_col="fn", strategy=STRATEGY_NUMERIC_EXACT),
    FieldSpec(ai_col="tn", strategy=STRATEGY_NUMERIC_EXACT),
    FieldSpec(ai_col="reported_sensitivity", strategy=STRATEGY_NUMERIC_TOLERANCE, tolerance=0.5),
    FieldSpec(ai_col="reported_specificity", strategy=STRATEGY_NUMERIC_TOLERANCE, tolerance=0.5),
    FieldSpec(ai_col="n_patients_rs_not_it", strategy=STRATEGY_NUMERIC_EXACT),
    FieldSpec(ai_col="n_patients_it_not_rs", strategy=STRATEGY_NUMERIC_EXACT),
    FieldSpec(ai_col="n_lesions_it_not_rs",  strategy=STRATEGY_NUMERIC_EXACT),
    FieldSpec(ai_col="n_lesions_rs_not_it",  strategy=STRATEGY_NUMERIC_EXACT),
    FieldSpec(ai_col="time_interval_rs_it",  strategy=STRATEGY_EXACT_NORMALIZE),
    FieldSpec(ai_col="sequence_of_tests",    strategy=STRATEGY_EXACT_NORMALIZE),
    FieldSpec(ai_col="reported_prevalence",  strategy=STRATEGY_PARSE_PERCENT),
    # skip
    FieldSpec(ai_col="general_notes",            strategy=STRATEGY_SKIP),
    FieldSpec(ai_col="comments_outcomes",        strategy=STRATEGY_SKIP),
    FieldSpec(ai_col="data_source",              strategy=STRATEGY_SKIP),
    FieldSpec(ai_col="reported_sensitivity_ci",  strategy=STRATEGY_SKIP),
    FieldSpec(ai_col="reported_specificity_ci",  strategy=STRATEGY_SKIP),
]

CFG = {
    "fields":           FIELDS,
    "ai_filename":      "full_studies/oral_cancer/claude/form_outcomes_long.json",
    "ai_format":        "json",
    "gt_section":       "OUTCOMES - SUBFORM",
    "gt_aligned_sheet": "outcomes",
    "level2":           False,
    "sheet_all_rows":   True,
}


def run(use_llm=True, force_rematch=False):
    return run_form(
        form_name="outcomes",
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
