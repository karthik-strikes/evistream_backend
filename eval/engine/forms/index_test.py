import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from config.field_config import (
    FieldSpec,
    STRATEGY_EXACT_NORMALIZE, STRATEGY_NUMERIC_EXACT,
    STRATEGY_LLM_JUDGE, STRATEGY_SKIP, STRATEGY_EXCLUDE,
)
from config.paths import AI_DATA_DIR, ALIGNED_GT_PATH
from forms.base_form import run_form

FIELDS: list[FieldSpec] = [
    FieldSpec(ai_col="index_test_type_transformed",   strategy=STRATEGY_EXCLUDE),
    FieldSpec(ai_col="n_patients_received",           strategy=STRATEGY_NUMERIC_EXACT),
    FieldSpec(ai_col="n_patients_analyzed",           strategy=STRATEGY_NUMERIC_EXACT),
    FieldSpec(ai_col="n_lesions_received",            strategy=STRATEGY_NUMERIC_EXACT),
    FieldSpec(ai_col="n_lesions_analyzed",            strategy=STRATEGY_NUMERIC_EXACT),
    FieldSpec(ai_col="positivity_threshold_transformed", strategy=STRATEGY_EXCLUDE),
    FieldSpec(ai_col="site_selection_index_test",     strategy=STRATEGY_LLM_JUDGE),
    FieldSpec(ai_col="calibration_of_assessors",      strategy=STRATEGY_LLM_JUDGE),
    FieldSpec(ai_col="blinding_index_test_assessors", strategy=STRATEGY_LLM_JUDGE),
    FieldSpec(ai_col="blinding_index_test_examiner",  strategy=STRATEGY_LLM_JUDGE),
    # skip
    FieldSpec(ai_col="technique",                    strategy=STRATEGY_SKIP),
    FieldSpec(ai_col="specimen_collection",          strategy=STRATEGY_SKIP),
    FieldSpec(ai_col="positivity_threshold",         strategy=STRATEGY_SKIP),
    FieldSpec(ai_col="comments_index_test",          strategy=STRATEGY_EXCLUDE),
    FieldSpec(ai_col="index_test_arm_label",         strategy=STRATEGY_SKIP),
    FieldSpec(ai_col="index_test_original_category", strategy=STRATEGY_SKIP),
    FieldSpec(ai_col="index_test_reagent_name",      strategy=STRATEGY_SKIP),
    FieldSpec(ai_col="index_test_commercial_name",   strategy=STRATEGY_SKIP),
    FieldSpec(ai_col="n_index_tests_in_study",       strategy=STRATEGY_SKIP),
]

CFG = {
    "fields":           FIELDS,
    "ai_filename":      "full_studies/oral_cancer/claude/form_index_test_long.csv",
    "ai_format":        "csv",
    "gt_section":       "INDEX TEST - SUBFORMS",
    "gt_aligned_sheet": "index_test",
    "level2":           True,
    "level2_type_key":  "index_test_type_transformed",
    "level2_label_key": "index_test_arm_label",
    "sheet_extra_cols": [
        "index_test_arm_label",
        "technique",
        "specimen_collection",
        "comments_index_test",
    ],
}


def run(use_llm=True, force_rematch=False):
    return run_form(
        form_name="index_test",
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
