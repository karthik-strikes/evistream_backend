"""Score the single-call PROMPT arm vs the production DECOMPOSED arm (scalar forms, D3a).

decomposed arm = production multi-signature extraction (full_studies/<slug>/claude/...).
prompt arm     = the single-call CSV from eval.studies.stage_ablation.run_prompt_arm.
Both scored with the SAME harness (forms.base_form.run_form, scalar path level2=False)
against the project's GT workbook.

Usage:
    python -m eval.studies.stage_ablation.score_prompt_arm --form perio_study_characteristics
    python -m eval.studies.stage_ablation.score_prompt_arm --form oc_reference_standard --no-llm
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

from studies.stage_ablation.prompt_arm_forms import FORMS

ARMS = ("decomposed", "prompt")


def _load_env() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    for p in (EVAL_ROOT / ".env", REPO_ROOT / "backend" / ".env"):
        if p.exists():
            load_dotenv(p, override=False)


def _out_dir(spec) -> Path:
    return EVAL_ROOT / "outputs" / "staged" / spec.slug / "claude"


def _arm_csv(spec, arm: str) -> Path:
    return spec.production_csv if arm == "decomposed" else spec.out_csv


def _score_arm(spec, arm: str, use_llm: bool) -> dict:
    from forms.base_form import run_form

    csv_path = _arm_csv(spec, arm)
    if not Path(csv_path).exists():
        print(f"  SKIP {arm}: {csv_path} not found")
        return {}

    cfg = spec.load_registry_cfg()
    out_dir = _out_dir(spec)
    rep = out_dir / "scoring" / arm
    os.environ["EVAL_METRICS_DIR"] = str(rep)
    rep.mkdir(parents=True, exist_ok=True)

    result = run_form(
        form_name        = f"{spec.registry_key}__{arm}",
        fields           = cfg["fields"],
        ai_path          = str(csv_path),
        ai_format        = cfg["ai_format"],
        gt_section       = cfg["gt_section"],
        level2           = cfg.get("level2", False),
        level2_cfg       = cfg if cfg.get("level2") else None,
        use_llm          = use_llm,
        gt_aligned_sheet = cfg.get("gt_aligned_sheet"),
        aligned_gt_path  = str(spec.gt_xlsx),
        gt_key_col       = cfg.get("gt_key_col", "Paper"),
        gt_skiprows      = cfg.get("gt_skiprows"),
        canonical_form   = cfg.get("canonical_form", "study_characteristics"),
    )
    summary = result.get("summary", {})
    summary["arm"] = arm
    return summary


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--form", required=True, choices=sorted(FORMS))
    ap.add_argument("--arms", nargs="*", default=None, choices=ARMS)
    ap.add_argument("--no-llm", action="store_true", help="skip LLM-as-judge fields")
    args = ap.parse_args()

    spec = FORMS[args.form]
    _load_env()
    selected = list(args.arms or ARMS)
    summaries: list[dict] = []
    for arm in selected:
        s = _score_arm(spec, arm, use_llm=not args.no_llm)
        if s:
            summaries.append(s)
    if not summaries:
        print("\n  No arms scored. Did run_prompt_arm.py write the prompt CSV?")
        return 1

    out_dir = _out_dir(spec)
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{spec.key}_metrics.xlsx"
    headline = ["arm", "n_scored_fields", "macro_f1", "macro_precision", "macro_recall",
                "macro_kappa", "micro_f1", "micro_precision", "micro_recall",
                "total_tp", "total_fp", "total_fn"]
    df = pd.DataFrame(summaries)
    df = df[[c for c in headline if c in df.columns] + [c for c in df.columns if c not in headline]]
    df.to_excel(out, index=False)

    print(f"\n  {'arm':<14}{'fields':>7}{'macroF1':>9}{'microF1':>9}{'prec':>7}{'recall':>8}")
    print("  " + "-" * 54)
    def n(x): return f"{x:.3f}" if isinstance(x, (int, float)) else "—"
    by = {s["arm"]: s for s in summaries}
    for arm in selected:
        s = by.get(arm)
        if s:
            print(f"  {arm:<14}{s.get('n_scored_fields','—'):>7}{n(s.get('macro_f1')):>9}"
                  f"{n(s.get('micro_f1')):>9}{n(s.get('macro_precision')):>7}{n(s.get('macro_recall')):>8}")
    if "decomposed" in by and "prompt" in by:
        d, p = by["decomposed"], by["prompt"]
        if isinstance(d.get("macro_f1"), (int, float)) and isinstance(p.get("macro_f1"), (int, float)):
            print(f"\n  Δ macro_f1 (decomposed − prompt) = {d['macro_f1'] - p['macro_f1']:+.3f}")
    print(f"\n  Full sheet: {out}")
    return 0


if __name__ == "__main__":
    _rc = main()
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(_rc)
