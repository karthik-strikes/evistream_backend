import asyncio, json, traceback
from itertools import zip_longest
from typing import Dict, List


# =====================================================
# PRINT HELPERS
# =====================================================

def print_extracted_vs_ground_truth(baseline_results, one_study_records, max_width: int = 120):
    """Pretty print extracted vs ground truth JSONs side-by-side."""
    left = json.dumps(baseline_results, indent=4).splitlines()
    right = json.dumps(one_study_records, indent=4).splitlines()

    width = min(max(len(line) for line in left), max_width) + 4
    print(f"{'Extracted Records'.ljust(width)}Ground Truth Records")
    print("-" * (width * 2))

    for l, r in zip_longest(left, right, fillvalue=""):
        l = (l[:max_width - 3] + "...") if len(l) > max_width else l
        r = (r[:max_width - 3] + "...") if len(r) > max_width else r
        print(f"{l.ljust(width)}{r}")


def print_field_level_table(field_counts: Dict):
    """Print a formatted table of field-level counts."""
    print("\n" + "=" * 170)
    print("FIELD-LEVEL ANALYSIS")
    print("=" * 170)

    header = (f"{'Field Name':<50} | {'Present in GT':>15} | "
              f"{'Present in Extracted':>20} | {'Matched':>10} | "
              f"{'Not Matched':>12} | {'Missing':>10} | {'Extra':>10}")
    print(header)
    print("-" * 170)

    for field in sorted(field_counts.keys()):
        c = field_counts[field]
        row = (f"{field:<50} | {c['gt_count']:>15} | {c['extracted_count']:>20} | "
               f"{c['matched']:>10} | {c['incorrect']:>12} | {c['missing']:>10} | {c['extra']:>10}")
        print(row)

    print("=" * 170)
    
    


def print_evaluation_summary(baseline_evaluation: Dict):
    """Print compact summary of evaluation results."""
    print(f"\n{'='*60}")
    print("BASELINE EVALUATION RESULTS:")
    print(f"{'='*60}")
    for key, value in baseline_evaluation.items():
        if key not in ["field_accuracies", "field_errors"]:
            if isinstance(value, float):
                print(f"{key:.<40} {value:.4f}")
            else:
                print(f"{key:.<40} {value}")
    print(f"{'='*60}\n")


import collections.abc

def flatten_json(d, parent_key='', sep='.'):
    """
    Recursively flattens a nested dictionary.
    
    Example:
    {'a': {'b': 1, 'c': 2}} 
    becomes 
    {'a.b': 1, 'a.c': 2}
    """
    items = []
    for k, v in d.items():
        new_key = f"{parent_key}{sep}{k}" if parent_key else k
        if isinstance(v, collections.abc.MutableMapping):
            # If value is a dict, recurse
            items.extend(flatten_json(v, new_key, sep=sep).items())
        else:
            # If value is a leaf node (str, int, bool, etc.)
            items.append((new_key, v))
    return dict(items)