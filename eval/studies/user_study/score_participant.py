"""
Score one user-study participant's filled workbook against the periodontitis ground truth,
using the EXACT same harness the system and agent baseline are scored with.

Pure orchestration over the existing harness — no new metric code. Mirrors
agent_baseline/build_summary.py:score_source, but reads a participant directory whose CSVs are
named by form (study_char.csv, patient_pop.csv, interventions.csv, outcomes.csv, risk_of_bias.csv).

Usage:
    python eval/studies/user_study/score_participant.py P01
    python eval/studies/user_study/score_participant.py P01 --dir /path/to/participant/dir

Looks for CSVs in (in order):
    <--dir>                                  if given
    eval/sheets/ai sheets/user_study/<pid>/         preferred
    eval/studies/user_study/participants/<pid>/      fallback
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

_EVAL = Path("/home/ubuntu/evistream/eval")
sys.path.insert(0, str(_EVAL))
sys.path.insert(0, str(_EVAL / "engine"))

# study_char FIRST — it builds the canonical study match table the other forms inherit.
FORM_ORDER = [
    "perio_study_char",
    "perio_patient_pop",
    "perio_interventions",
    "perio_outcomes",
    "perio_risk_of_bias",
]
# registry key -> participant CSV filename
FORM_FILE = {
    "perio_study_char":    "study_char.csv",
    "perio_patient_pop":   "patient_pop.csv",
    "perio_interventions": "interventions.csv",
    "perio_outcomes":      "outcomes.csv",
    "perio_risk_of_bias":  "risk_of_bias.csv",
}
FORM_LABEL = {
    "perio_study_char":    "Study Characteristics",
    "perio_patient_pop":   "Patient Population",
    "perio_interventions": "Interventions",
    "perio_outcomes":      "Continuous Outcomes",
    "perio_risk_of_bias":  "Risk of Bias",
}


def _load_env() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    for p in (_EVAL / ".env", _EVAL.parent / "backend" / ".env"):
        if p.exists():
            load_dotenv(p, override=False)


def _resolve_dir(pid: str, explicit: str | None) -> Path:
    if explicit:
        return Path(explicit)
    for cand in (_EVAL / "sheets/ai sheets" / "user_study" / pid,
                 _EVAL / "user_study" / "participants" / pid):
        if cand.exists():
            return cand
    raise SystemExit(f"No participant directory found for {pid!r}")


def score_participant(pid: str, base: Path) -> dict:
    from config.paths import GT_DATA_DIR
    from forms.periodontitis import PERIODONTITIS_REGISTRY
    from forms.base_form import run_form

    aligned_gt = f"{GT_DATA_DIR}/periodontitis.xlsx"

    scoring_dir = _EVAL / "outputs" / "user_study" / pid
    os.environ["EVAL_METRICS_DIR"] = str(scoring_dir)   # → scoring_dir/all_forms_metrics.xlsx
    scoring_dir.mkdir(parents=True, exist_ok=True)

    results = {}
    for form in FORM_ORDER:
        cfg = PERIODONTITIS_REGISTRY[form]
        ai_path = base / FORM_FILE[form]
        if not ai_path.exists():
            print(f"  ⚠ missing {ai_path} — skipping {FORM_LABEL[form]}")
            continue
        results[form] = run_form(
            form_name=form,
            fields=cfg["fields"],
            ai_path=str(ai_path),
            ai_format=cfg["ai_format"],
            gt_section=cfg["gt_section"],
            level2=cfg.get("level2", False),
            level2_cfg=cfg if cfg.get("level2") else None,
            use_llm=True,
            gt_aligned_sheet=cfg.get("gt_aligned_sheet"),
            aligned_gt_path=aligned_gt,
            gt_key_col=cfg["gt_key_col"],
            gt_skiprows=cfg["gt_skiprows"],
            canonical_form=cfg["canonical_form"],
        )
    return results


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("pid", help="participant id, e.g. P01")
    ap.add_argument("--dir", default=None, help="explicit participant directory")
    args = ap.parse_args()

    _load_env()
    base = _resolve_dir(args.pid, args.dir)
    print(f"Scoring participant {args.pid}  ({base})")
    results = score_participant(args.pid, base)

    print(f"\n{'Form':<24}{'Fields':>7}{'macroF1':>9}{'microF1':>9}{'Prec':>7}{'Recall':>8}")
    print("-" * 64)
    for form in FORM_ORDER:
        res = results.get(form)
        if not res:
            continue
        s = res["summary"]
        def n(x): return f"{x:.3f}" if isinstance(x, (int, float)) else "—"
        print(f"{FORM_LABEL[form]:<24}{s.get('n_scored_fields','—'):>7}"
              f"{n(s.get('macro_f1')):>9}{n(s.get('micro_f1')):>9}"
              f"{n(s.get('macro_precision')):>7}{n(s.get('macro_recall')):>8}")
    print(f"\nByproduct reports → eval/outputs/user_study/{args.pid}/")


if __name__ == "__main__":
    main()
