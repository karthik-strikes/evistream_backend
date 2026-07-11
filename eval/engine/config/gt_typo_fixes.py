"""Known typos and label mismatches in the ground-truth Excel to fix before comparison."""

# Applied to GT values after lowercasing + stripping.
# Keys are the bad GT strings; values are the corrected canonical form.
GT_FIXES: dict[str, str] = {
    "single ernter": "single center",      # typo in # of centers field
    "multi center":  "multiple center",    # label mismatch with AI
    "unclear/ secondary": "unclear or not reported",  # study setting variant
}


def apply_gt_fixes(value: str) -> str:
    lowered = value.strip().lower()
    return GT_FIXES.get(lowered, lowered)
