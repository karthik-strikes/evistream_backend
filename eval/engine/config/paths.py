"""
Data paths for the eval pipeline. Single source of truth — imported by both
forms/<form>.py modules and eval_walkthrough.ipynb.
"""

AI_DATA_DIR     = "/home/ubuntu/evistream/eval/sheets/ai sheets"
OUTPUTS_DIR     = "/home/ubuntu/evistream/eval/outputs"
GT_DATA_DIR     = "/home/ubuntu/evistream/eval/sheets/gt sheets"
ALIGNED_GT_PATH = f"{GT_DATA_DIR}/aligned_forms.xlsx"
RAW_GT_PATH     = f"{GT_DATA_DIR}/combined_oral_cancer_data.xlsx"

# --- Variant-first experiment layout (single source of truth) ---
# Inputs (sheets/ai sheets). Corpus sits inside each variant; full_studies also splits by model.
#   full_studies/<corpus>/<model>/form_*.csv   = production full-spec extraction (the shared
#                                                reference every other variant compares against)
#   desc_only/<corpus>/form_*.csv              = description-only arm
#   staged/<corpus>/*_single_long.csv          = single-prompt arm (decomposition experiment)
#   table/<corpus>/                            = single-prompt table arm (table experiment)
#   user_study/<pid>/                          = participant submissions
FULL_STUDIES_DIR = f"{AI_DATA_DIR}/full_studies"
DESC_ONLY_DIR    = f"{AI_DATA_DIR}/desc_only"
STAGED_DIR       = f"{AI_DATA_DIR}/staged"
TABLE_DIR        = f"{AI_DATA_DIR}/table"
USER_STUDY_DIR   = f"{AI_DATA_DIR}/user_study"

# Outputs (scored) — mirror the inputs, plus _baselines/ for the agent baseline.
OUT_FULL_STUDIES = f"{OUTPUTS_DIR}/full_studies"
OUT_DESC_ONLY    = f"{OUTPUTS_DIR}/desc_only"
OUT_STAGED       = f"{OUTPUTS_DIR}/staged"
OUT_TABLE        = f"{OUTPUTS_DIR}/table"
OUT_USER_STUDY   = f"{OUTPUTS_DIR}/user_study"
OUT_BASELINES    = f"{OUTPUTS_DIR}/_baselines"
