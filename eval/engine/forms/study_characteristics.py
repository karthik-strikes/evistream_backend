import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from config.field_config import (
    FieldSpec,
    STRATEGY_EXACT_NORMALIZE, STRATEGY_NUMERIC_EXACT, STRATEGY_DATE_RANGE,
    STRATEGY_LLM_JUDGE, STRATEGY_SKIP, STRATEGY_EXCLUDE,
)
from config.paths import AI_DATA_DIR, ALIGNED_GT_PATH
from forms.base_form import run_form

FIELDS: list[FieldSpec] = [
    FieldSpec(ai_col="year_of_study",             strategy=STRATEGY_NUMERIC_EXACT),
    FieldSpec(ai_col="study_period",              strategy=STRATEGY_DATE_RANGE),
    FieldSpec(ai_col="study_setting_original",    strategy=STRATEGY_EXACT_NORMALIZE),
    FieldSpec(ai_col="study_setting_transformed", strategy=STRATEGY_EXCLUDE),
    FieldSpec(ai_col="number_of_centers",         strategy=STRATEGY_EXACT_NORMALIZE),
    FieldSpec(ai_col="country",                   strategy=STRATEGY_LLM_JUDGE),
    FieldSpec(ai_col="funding_category",          strategy=STRATEGY_EXACT_NORMALIZE),
    FieldSpec(ai_col="conflicts_of_interest",     strategy=STRATEGY_LLM_JUDGE),
    # skip
    FieldSpec(ai_col="study_setting_verbatim",          strategy=STRATEGY_SKIP),
    FieldSpec(ai_col="study_setting_comment",           strategy=STRATEGY_SKIP),
    FieldSpec(ai_col="funding_comment",                 strategy=STRATEGY_SKIP),
    FieldSpec(ai_col="conflicts_of_interest_comment",   strategy=STRATEGY_SKIP),
    FieldSpec(ai_col="refid",                           strategy=STRATEGY_SKIP),
    FieldSpec(ai_col="Paper",                           strategy=STRATEGY_SKIP),
]

CFG = {
    "fields":           FIELDS,
    "ai_filename":      "full_studies/oral_cancer/claude/form_study_characteristics_long.csv",
    "ai_format":        "csv",
    "gt_section":       "STUDY CHARACTERISTICS",
    "gt_aligned_sheet": "study_char",
    "level2":           False,
}


def run(use_llm=True, force_rematch=False):
    return run_form(
        form_name="study_characteristics",
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
