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
    FieldSpec(ai_col="reference_standard_type",          strategy=STRATEGY_SKIP),
    FieldSpec(ai_col="n_patients_rs",                    strategy=STRATEGY_NUMERIC_EXACT),
    FieldSpec(ai_col="n_patients_rs_analyzed",           strategy=STRATEGY_NUMERIC_EXACT),
    FieldSpec(ai_col="n_lesions_rs",                     strategy=STRATEGY_NUMERIC_EXACT),
    FieldSpec(ai_col="n_lesions_rs_analyzed",            strategy=STRATEGY_EXCLUDE),
    FieldSpec(ai_col="positivity_threshold_transformed", strategy=STRATEGY_EXCLUDE),
    FieldSpec(ai_col="site_of_biopsy",                   strategy=STRATEGY_LLM_JUDGE),
    FieldSpec(ai_col="final_diagnosis_patients",         strategy=STRATEGY_LLM_JUDGE),
    FieldSpec(ai_col="calibration_rs_examiners",         strategy=STRATEGY_LLM_JUDGE),
    FieldSpec(ai_col="blinding_rs_examiners",            strategy=STRATEGY_LLM_JUDGE),
    FieldSpec(ai_col="reference_standard_comment",              strategy=STRATEGY_EXCLUDE),
    FieldSpec(ai_col="positive_threshold_oral_cancer_comment",  strategy=STRATEGY_EXCLUDE),
    FieldSpec(ai_col="positive_threshold_opmd_comment",         strategy=STRATEGY_EXCLUDE),
]

CFG = {
    "fields":           FIELDS,
    "ai_filename":      "full_studies/oral_cancer/claude/form_reference_standard_long.csv",
    "ai_format":        "csv",
    "gt_section":       "REFERENCE STANDARD",
    "gt_aligned_sheet": "ref_standard",
    "level2":           False,
    "extra_col_keywords": [
        "Reference standard_comment",
        "Reference standard",
    ],
    "sheet_extra_cols": [
        "reference_standard_type",
        "reference_standard_comment",
        "positive_threshold_oral_cancer_comment",
        "positive_threshold_opmd_comment",
    ],
}


def run(use_llm=True, force_rematch=False):
    return run_form(
        form_name="reference_standard",
        fields=CFG["fields"],
        ai_path=os.path.join(AI_DATA_DIR, CFG["ai_filename"]),
        ai_format=CFG["ai_format"],
        gt_section=CFG["gt_section"],
        level2=CFG.get("level2", False),
        level2_cfg=CFG if CFG.get("level2") else None,
        use_llm=use_llm,
        force_rematch=force_rematch,
        extra_col_keywords=CFG.get("extra_col_keywords"),
        gt_aligned_sheet=CFG.get("gt_aligned_sheet"),
        aligned_gt_path=ALIGNED_GT_PATH,
    )
