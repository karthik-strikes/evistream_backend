"""Exercise the real field scorer with synthetic values and no API calls."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "engine"))
from core.comparator import compare_field, compare_exact_normalize, MATCH_YES
from core.metrics import compute_field_metrics
from config.paths import AI_DATA_DIR, GT_DATA_DIR


def main():
    # Correct value; wrong value; omission; spurious value; both unreported.
    cases = [(10, 10), (12, 10), ("NR", 10), (10, "NR"), ("NR", "NR")]
    results = [compare_field(ai, gt, "numeric_exact") for ai, gt in cases]
    m = compute_field_metrics(results, "synthetic_count")
    assert (m["tp"], m["fp"], m["fn"]) == (1, 2, 2), m
    assert m["macro_f1"] == 0.333, m
    assert compare_exact_normalize("United Kingdom", "united kingdom").match == MATCH_YES
    assert Path(AI_DATA_DIR).is_dir(), AI_DATA_DIR
    assert Path(GT_DATA_DIR).is_dir(), GT_DATA_DIR
    print("PASS: correct values, errors, omissions, extra values, NR policy, normalization, portable paths")


if __name__ == "__main__":
    main()
