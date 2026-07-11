"""
Score the periodontitis field-spec ablation variants (D1) against ground truth.

Reuses the EXACT scoring harness the system, agent baseline, and user study use
(forms/base_form.run_form + forms/periodontitis.PERIODONTITIS_REGISTRY, scored vs
sheets/gt sheets/periodontitis.xlsx). No new metric code. One row per variant; the
headline is macro/micro F1 climbing V1 → V5 as spec layers are added back.

Reads the variant CSVs written by run_perio_ablation.py from
`eval/sheets/ai sheets/desc_only/periodontitis/_variants/patient_population_<variant>_long.csv`.

Usage:
    python -m eval.studies.ablation.score_perio_ablation --form patient_population
    python -m eval.studies.ablation.score_perio_ablation --form patient_population --no-llm
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

REPO_ROOT = Path("/home/ubuntu/evistream")
EVAL_ROOT = REPO_ROOT / "eval"
sys.path.insert(0, str(EVAL_ROOT))
sys.path.insert(0, str(EVAL_ROOT / "engine"))
sys.path.insert(0, str(REPO_ROOT))

import pandas as pd

from .config import VARIANTS

AI_DIR = EVAL_ROOT / "sheets/ai sheets" / "desc_only" / "periodontitis" / "claude"
OUT_DIR = EVAL_ROOT / "outputs" / "desc_only" / "periodontitis" / "claude"

# form key -> registry key in forms/periodontitis.PERIODONTITIS_REGISTRY
FORM_TO_REGISTRY = {
    "patient_population": "perio_patient_pop",
    "study_characteristics": "perio_study_char",
    "interventions": "perio_interventions",
    "outcomes": "perio_outcomes",
}


def _load_env() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    for p in (EVAL_ROOT / ".env", REPO_ROOT / "backend" / ".env"):
        if p.exists():
            load_dotenv(p, override=False)


def _variant_csv(form: str, variant_name: str) -> Path:
    return AI_DIR / f"{form}_{variant_name}_long.csv"


def _score_variant(form: str, variant_name: str, use_llm: bool) -> dict:
    from config.paths import GT_DATA_DIR
    from forms.periodontitis import PERIODONTITIS_REGISTRY
    from forms.base_form import run_form

    csv_path = _variant_csv(form, variant_name)
    if not csv_path.exists():
        print(f"  SKIP {variant_name}: {csv_path} not found")
        return {}

    cfg = PERIODONTITIS_REGISTRY[FORM_TO_REGISTRY[form]]
    aligned_gt = f"{GT_DATA_DIR}/periodontitis.xlsx"

    # consolidated metrics workbook at the per-variant folder root
    rep = OUT_DIR / "scoring" / variant_name
    os.environ["EVAL_METRICS_DIR"] = str(rep)   # → rep/all_forms_metrics.xlsx
    rep.mkdir(parents=True, exist_ok=True)

    result = run_form(
        form_name        = f"{FORM_TO_REGISTRY[form]}__{variant_name}",
        fields           = cfg["fields"],
        ai_path          = str(csv_path),
        ai_format        = cfg["ai_format"],
        gt_section       = cfg["gt_section"],
        level2           = cfg.get("level2", False),
        level2_cfg       = cfg if cfg.get("level2") else None,
        use_llm          = use_llm,
        gt_aligned_sheet = cfg.get("gt_aligned_sheet"),
        aligned_gt_path  = aligned_gt,
        gt_key_col       = cfg["gt_key_col"],
        gt_skiprows      = cfg["gt_skiprows"],
        canonical_form   = cfg["canonical_form"],
    )
    summary = result.get("summary", {})
    summary["variant"] = variant_name
    return summary


def _write_summary(form: str, summaries: list[dict]) -> Path:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"{form}_metrics.xlsx"
    headline = ["variant", "n_scored_fields", "macro_f1", "macro_precision",
                "macro_recall", "macro_kappa", "micro_f1", "micro_precision",
                "micro_recall", "total_tp", "total_fp", "total_fn"]
    df = pd.DataFrame(summaries)
    cols = [c for c in headline if c in df.columns]
    extras = [c for c in df.columns if c not in cols]
    df = df[cols + extras]
    df.to_excel(out, index=False)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--form", default="patient_population", choices=sorted(FORM_TO_REGISTRY))
    ap.add_argument("--variants", nargs="*", default=None)
    ap.add_argument("--no-llm", action="store_true",
                    help="Skip LLM-as-judge fields (faster; interpretive fields show SKIP)")
    args = ap.parse_args()

    _load_env()
    selected = args.variants or [v.name for v in VARIANTS]
    summaries: list[dict] = []
    for vname in selected:
        s = _score_variant(args.form, vname, use_llm=not args.no_llm)
        if s:
            summaries.append(s)

    if not summaries:
        print("\n  No variants scored. Did run_perio_ablation.py write the CSVs?")
        return 1

    out = _write_summary(args.form, summaries)
    print(f"\n  {'variant':<36}{'fields':>7}{'macroF1':>9}{'microF1':>9}{'prec':>7}{'recall':>8}")
    print("  " + "-" * 76)
    def n(x): return f"{x:.3f}" if isinstance(x, (int, float)) else "—"
    for s in summaries:
        print(f"  {s['variant']:<36}{s.get('n_scored_fields','—'):>7}"
              f"{n(s.get('macro_f1')):>9}{n(s.get('micro_f1')):>9}"
              f"{n(s.get('macro_precision')):>7}{n(s.get('macro_recall')):>8}")
    print(f"\n  Full sheet: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
