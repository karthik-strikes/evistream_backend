"""
Score each ablation variant against ground truth and produce a single
paper-ready table per form (one row per variant).

Reuses the existing eval harness: `eval/forms/<form>.py` defines field
specs + comparison strategies, and `eval/forms/base_form.run_form` does
the heavy lifting. We just call run_form with the ablation variant CSV
path swapped in, then roll up macro/micro F1 across variants.

Usage:
    python -m eval.studies.ablation.score_variants --form patient_population
    python -m eval.studies.ablation.score_variants --form patient_population --no-llm  # skip LLM-as-judge for speed
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, "/home/ubuntu/evistream/eval")
sys.path.insert(0, "/home/ubuntu/evistream/eval/engine")
sys.path.insert(0, "/home/ubuntu/evistream")

import pandas as pd

from .config import (
    AI_SHEETS_DIR, ORAL_CANCER_FORMS, OUTPUTS_DIR, VARIANTS,
    variant_csv_path,
)
from .fetch_markdown import _load_env


def _run_variant(form: str, variant_name: str, csv_path: Path, use_llm: bool, force_rematch: bool) -> dict:
    """Call run_form on the variant CSV; return the summary dict."""
    if not csv_path.exists():
        print(f"  SKIP {variant_name}: {csv_path} not found")
        return {}

    # Import the form module and call run_form directly with a custom ai_path.
    # We can't just call <form>.run() — that hardcodes the production ai_path.
    mod_name = f"eval.engine.forms.{form}"
    import importlib
    form_mod = importlib.import_module(mod_name)

    from forms.base_form import run_form  # noqa: E402  — base_form lives on sys.path[0]
    from config.paths import ALIGNED_GT_PATH

    cfg = form_mod.CFG
    result = run_form(
        form_name        = f"{form}__{variant_name}",
        fields           = cfg["fields"],
        ai_path          = str(csv_path),
        ai_format        = cfg["ai_format"],
        gt_section       = cfg["gt_section"],
        level2           = cfg.get("level2", False),
        level2_cfg       = cfg if cfg.get("level2") else None,
        use_llm          = use_llm,
        force_rematch    = force_rematch,
        gt_aligned_sheet = cfg.get("gt_aligned_sheet"),
        aligned_gt_path  = ALIGNED_GT_PATH,
    )
    summary = result.get("summary", {})
    summary["variant"] = variant_name
    return summary


def _write_summary_xlsx(form: str, summaries: list[dict]) -> Path:
    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
    out = OUTPUTS_DIR / f"{form}_metrics.xlsx"

    # Order columns: variant first, then headline metrics, then everything else.
    headline = ["variant", "n_scored_fields", "macro_f1", "macro_precision",
                "macro_recall", "macro_kappa", "micro_f1", "micro_precision",
                "micro_recall", "total_tp", "total_fp", "total_fn",
                "total_fp_over_extraction", "total_fp_wrong_value"]
    df = pd.DataFrame(summaries)
    cols = [c for c in headline if c in df.columns]
    extras = [c for c in df.columns if c not in cols]
    df = df[cols + extras]
    df.to_excel(out, index=False)
    print(f"\n  ✓ wrote summary → {out}")
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--form", required=True, choices=sorted(ORAL_CANCER_FORMS))
    ap.add_argument("--variants", nargs="*", default=None,
                    help="Variant names to score (default: all)")
    ap.add_argument("--no-llm", action="store_true",
                    help="Skip LLM-as-judge fields (faster, but interpretive fields show as SKIP)")
    ap.add_argument("--force-rematch", action="store_true",
                    help="Recompute author→GT matching tables")
    args = ap.parse_args()

    _load_env()

    selected = args.variants or [v.name for v in VARIANTS]
    summaries: list[dict] = []
    for vname in selected:
        csv_path = variant_csv_path(args.form, next(v for v in VARIANTS if v.name == vname))
        summary = _run_variant(
            form=args.form,
            variant_name=vname,
            csv_path=csv_path,
            use_llm=not args.no_llm,
            force_rematch=args.force_rematch,
        )
        if summary:
            summaries.append(summary)

    if not summaries:
        print("\n  No variants produced summaries. Did extraction CSVs get written?")
        return 1

    out = _write_summary_xlsx(args.form, summaries)

    # One-glance comparison
    print("\n  ── Headline F1 across variants ──")
    for s in summaries:
        print(f"    {s['variant']:<35}  macro_f1={s.get('macro_f1')!s:<7}  "
              f"micro_f1={s.get('micro_f1')!s:<7}  "
              f"n_fields={s.get('n_scored_fields')}")
    print(f"\n  Full sheet: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
