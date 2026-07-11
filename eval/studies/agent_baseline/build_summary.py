"""
Score the agent-baseline sheets AND the system (evistream DSPy) sheets against the
periodontitis ground truth, then write ONE single-sheet .xlsx that puts them side by side:

  - TOP  — scorecard: one row per form (+ OVERALL), macro/micro F1·P·R, kappa, TP/FP/FN,
            #fields, #studies, agent vs system in paired columns + ΔF1.
  - BELOW — field detail: every scored field across the 4 forms (leading Form column),
            agent vs system paired (Compared, %Agree, TP/FP/FN, Precision, Recall, F1, ΔF1).

Pure orchestration over the existing harness — no new metric code. Scoring reuses
forms/base_form.py:run_form + core/metrics.py exactly as eval_walkthrough.ipynb does.
Nothing under eval/outputs/ or eval/sheets/ai sheets/ is modified; run_form's
byproduct workbooks are redirected to outputs/scoring/{source}/.

Usage:
    python eval/studies/agent_baseline/build_summary.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_EVAL = Path("/home/ubuntu/evistream/eval")
sys.path.insert(0, str(_EVAL))
sys.path.insert(0, str(_EVAL / "engine"))

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

OUT_DIR = Path(__file__).resolve().parent / "outputs"
SUMMARY_XLSX = OUT_DIR / "metrics_summary.xlsx"
AGENT_SHEETS = OUT_DIR / "sheets"
SYSTEM_SHEETS = _EVAL / "sheets/ai sheets" / "full_studies" / "periodontitis" / "claude"

# 4 forms, scored study_char FIRST (canonical match table the level-2 forms inherit).
FORM_ORDER = ["perio_study_char", "perio_patient_pop", "perio_interventions", "perio_outcomes"]

# Per-source AI filenames. Agent uses the clean harness names; system uses the
# download-suffixed names already on disk.
AGENT_FILES = {
    "perio_study_char":    "form_Study_Characteristics_long.csv",
    "perio_patient_pop":   "form_Patient_Population_long.csv",
    "perio_interventions": "form_Intervention_Characteristics_long.csv",
    "perio_outcomes":      "form_Continuous_Outcomes_long.csv",
}
SYSTEM_FILES = {
    "perio_study_char":    "form_study_characteristics_long.csv",
    "perio_patient_pop":   "form_patient_population_long.csv",
    "perio_interventions": "form_intervention_characteristics_long.csv",
    "perio_outcomes":      "form_continuous_outcomes_long.csv",
}
FORM_LABEL = {
    "perio_study_char":    "Study Characteristics",
    "perio_patient_pop":   "Patient Population",
    "perio_interventions": "Interventions",
    "perio_outcomes":      "Continuous Outcomes",
}


def _load_env() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    for p in (_EVAL / ".env", _EVAL.parent / "backend" / ".env"):
        if p.exists():
            load_dotenv(p, override=False)


def score_source(source: str, ai_files: dict) -> dict:
    """Run run_form for all 4 forms for one source. Returns {form: result_dict}."""
    from config.paths import GT_DATA_DIR
    from forms.periodontitis import PERIODONTITIS_REGISTRY
    from forms.base_form import run_form

    aligned_gt = f"{GT_DATA_DIR}/periodontitis.xlsx"
    base = AGENT_SHEETS if source == "agent" else SYSTEM_SHEETS

    # consolidated metrics workbook at the per-source folder root
    scoring_dir = OUT_DIR / "scoring" / source
    os.environ["EVAL_METRICS_DIR"] = str(scoring_dir)   # → scoring_dir/all_forms_metrics.xlsx
    scoring_dir.mkdir(parents=True, exist_ok=True)

    results = {}
    for form in FORM_ORDER:  # study_char first
        cfg = PERIODONTITIS_REGISTRY[form]
        ai_path = str(base / ai_files[form])
        if not Path(ai_path).exists():
            print(f"  ⚠ [{source}] missing {ai_path} — skipping {form}")
            continue
        results[form] = run_form(
            form_name=form,
            fields=cfg["fields"],
            ai_path=ai_path,
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


# ── xlsx styling ─────────────────────────────────────────────────────────────
HDR_FILL = PatternFill("solid", fgColor="1F2937")
HDR_FONT = Font(bold=True, color="FFFFFF", size=10)
SUB_FILL = PatternFill("solid", fgColor="374151")
TITLE_FONT = Font(bold=True, size=13)
SECTION_FONT = Font(bold=True, size=11, color="1F2937")
GREEN = PatternFill("solid", fgColor="C6EFCE")
AMBER = PatternFill("solid", fgColor="FFEB9C")
RED = PatternFill("solid", fgColor="FFC7CE")
POS = PatternFill("solid", fgColor="D8F0D8")
NEG = PatternFill("solid", fgColor="F8D8D8")
THIN = Side(style="thin", color="D0D0D0")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)


def _f1_fill(v):
    if v is None:
        return None
    if v >= 0.85:
        return GREEN
    if v >= 0.65:
        return AMBER
    return RED


def _num(v, nd=3):
    return round(v, nd) if isinstance(v, (int, float)) else None


def _delta(a, b):
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return round(a - b, 3)
    return None


def build_xlsx(agent: dict, system: dict) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "Summary"
    r = 1

    ws.cell(r, 1, "Periodontitis (CD004714) — Claude Code agent baseline vs evistream system").font = TITLE_FONT
    r += 2

    # ── Section A: scorecard ──────────────────────────────────────────────────
    ws.cell(r, 1, "SCORECARD — per form (agent vs system)").font = SECTION_FONT
    r += 1
    sc_cols = ["Form", "Fields", "Studies",
               "F1 (agent)", "F1 (system)", "ΔF1",
               "microF1 (agent)", "microF1 (system)",
               "Prec (agent)", "Prec (system)", "Recall (agent)", "Recall (system)",
               "kappa (agent)", "kappa (system)",
               "TP/FP/FN (agent)", "TP/FP/FN (system)"]
    hdr_row = r
    for c, name in enumerate(sc_cols, 1):
        cell = ws.cell(r, c, name)
        cell.fill = HDR_FILL; cell.font = HDR_FONT
        cell.alignment = Alignment(horizontal="center", wrap_text=True); cell.border = BORDER
    r += 1

    def _sum_block(res):
        s = res["summary"] if res else {}
        return s

    pooled = {"agent": [0, 0, 0], "system": [0, 0, 0]}  # tp,fp,fn
    macro_f1s = {"agent": [], "system": []}
    for form in FORM_ORDER:
        a, sy = _sum_block(agent.get(form)), _sum_block(system.get(form))
        af1, sf1 = _num(a.get("macro_f1")), _num(sy.get("macro_f1"))
        if af1 is not None: macro_f1s["agent"].append(af1)
        if sf1 is not None: macro_f1s["system"].append(sf1)
        for src, s in (("agent", a), ("system", sy)):
            pooled[src][0] += s.get("total_tp", 0) or 0
            pooled[src][1] += s.get("total_fp", 0) or 0
            pooled[src][2] += s.get("total_fn", 0) or 0
        vals = [
            FORM_LABEL[form],
            a.get("n_scored_fields"),
            a.get("n_scored_studies"),
            af1, sf1, _delta(af1, sf1),
            _num(a.get("micro_f1")), _num(sy.get("micro_f1")),
            _num(a.get("macro_precision")), _num(sy.get("macro_precision")),
            _num(a.get("macro_recall")), _num(sy.get("macro_recall")),
            _num(a.get("macro_kappa")), _num(sy.get("macro_kappa")),
            f"{a.get('total_tp',0)}/{a.get('total_fp',0)}/{a.get('total_fn',0)}",
            f"{sy.get('total_tp',0)}/{sy.get('total_fp',0)}/{sy.get('total_fn',0)}",
        ]
        for c, v in enumerate(vals, 1):
            cell = ws.cell(r, c, v); cell.border = BORDER
            cell.alignment = Alignment(horizontal="center")
        ws.cell(r, 4).fill = _f1_fill(af1) or PatternFill()
        ws.cell(r, 5).fill = _f1_fill(sf1) or PatternFill()
        d = _delta(af1, sf1)
        if d is not None:
            ws.cell(r, 6).fill = POS if d >= 0 else NEG
        r += 1

    # OVERALL row: macro = mean of per-form macro_F1; micro = pooled TP/FP/FN
    def _micro(tpfpfn):
        tp, fp, fn = tpfpfn
        p = tp / (tp + fp) if (tp + fp) else 0
        rc = tp / (tp + fn) if (tp + fn) else 0
        return round(2 * p * rc / (p + rc), 3) if (p + rc) else 0.0
    a_macro = round(sum(macro_f1s["agent"]) / len(macro_f1s["agent"]), 3) if macro_f1s["agent"] else None
    s_macro = round(sum(macro_f1s["system"]) / len(macro_f1s["system"]), 3) if macro_f1s["system"] else None
    overall = ["OVERALL", "", "",
               a_macro, s_macro, _delta(a_macro, s_macro),
               _micro(pooled["agent"]), _micro(pooled["system"]),
               "", "", "", "", "", "",
               "/".join(map(str, pooled["agent"])), "/".join(map(str, pooled["system"]))]
    for c, v in enumerate(overall, 1):
        cell = ws.cell(r, c, v); cell.border = BORDER; cell.font = Font(bold=True)
        cell.fill = SUB_FILL if c == 1 else PatternFill()
        cell.alignment = Alignment(horizontal="center")
    ws.cell(r, 1).font = Font(bold=True, color="FFFFFF")
    ws.cell(r, 4).fill = _f1_fill(a_macro) or PatternFill()
    ws.cell(r, 5).fill = _f1_fill(s_macro) or PatternFill()
    od = _delta(a_macro, s_macro)
    if od is not None:
        ws.cell(r, 6).fill = POS if od >= 0 else NEG
    r += 3

    # ── Section B: field detail ───────────────────────────────────────────────
    ws.cell(r, 1, "FIELD DETAIL — per field (agent vs system)").font = SECTION_FONT
    r += 1
    fd_cols = ["Form", "Field", "Strategy", "Compared",
               "%Agree (agent)", "%Agree (system)",
               "TP/FP/FN (agent)", "TP/FP/FN (system)",
               "Prec (agent)", "Prec (system)", "Recall (agent)", "Recall (system)",
               "F1 (agent)", "F1 (system)", "ΔF1"]
    for c, name in enumerate(fd_cols, 1):
        cell = ws.cell(r, c, name)
        cell.fill = HDR_FILL; cell.font = HDR_FONT
        cell.alignment = Alignment(horizontal="center", wrap_text=True); cell.border = BORDER
    r += 1

    def _field_map(res):
        if not res:
            return {}
        out = {}
        for fs in res["field_stats"]:
            # only scored fields (skip/exclude strategies never produce comparisons)
            if fs.get("macro_f1") is None and fs.get("pct_agreement") is None:
                continue
            out[fs["field"]] = fs
        return out

    for form in FORM_ORDER:
        amap, smap = _field_map(agent.get(form)), _field_map(system.get(form))
        fields = list(amap.keys()) + [f for f in smap if f not in amap]
        for fld in fields:
            af, sf = amap.get(fld, {}), smap.get(fld, {})
            af1, sf1 = _num(af.get("macro_f1")), _num(sf.get("macro_f1"))
            vals = [
                FORM_LABEL[form], fld, af.get("strategy") or sf.get("strategy"),
                af.get("n_compared") if af else sf.get("n_compared"),
                af.get("pct_agreement"), sf.get("pct_agreement"),
                f"{af.get('tp',0)}/{af.get('fp',0)}/{af.get('fn',0)}" if af else "",
                f"{sf.get('tp',0)}/{sf.get('fp',0)}/{sf.get('fn',0)}" if sf else "",
                _num(af.get("macro_precision")), _num(sf.get("macro_precision")),
                _num(af.get("macro_recall")), _num(sf.get("macro_recall")),
                af1, sf1, _delta(af1, sf1),
            ]
            for c, v in enumerate(vals, 1):
                cell = ws.cell(r, c, v); cell.border = BORDER
                if c >= 4:
                    cell.alignment = Alignment(horizontal="center")
            ws.cell(r, 13).fill = _f1_fill(af1) or PatternFill()
            ws.cell(r, 14).fill = _f1_fill(sf1) or PatternFill()
            d = _delta(af1, sf1)
            if d is not None:
                ws.cell(r, 15).fill = POS if d >= 0 else NEG
            r += 1

    # column widths
    widths = [22, 30, 22, 10] + [14] * (len(fd_cols) - 4)
    for c in range(1, len(fd_cols) + 1):
        ws.column_dimensions[get_column_letter(c)].width = widths[c - 1] if c <= len(widths) else 14
    ws.freeze_panes = ws.cell(hdr_row + 1, 1)

    _recall_sheet(wb, agent, system)
    build_oral_cancer_sheet(wb)
    build_antibiotic_sheet(wb)
    build_ablation_sheet(
        wb, "Antibiotic Ablation", "Antibiotic Prophylaxis — full system vs desc_only",
        ABX_FORM_LABEL,
        _EVAL / "outputs" / "antibiotic" / "claude" / "all_forms_metrics.xlsx",
        _EVAL / "outputs" / "antibiotic" / "desc_only" / "all_forms_metrics.xlsx",
    )
    build_ablation_sheet(
        wb, "Periodontitis Ablation", "Periodontitis (CD004714) — full system vs desc_only",
        FORM_LABEL,
        _EVAL / "outputs" / "periodontitis" / "claude" / "all_forms_metrics.xlsx",
        _EVAL / "outputs" / "periodontitis" / "desc_only" / "all_forms_metrics.xlsx",
    )

    SUMMARY_XLSX.parent.mkdir(parents=True, exist_ok=True)
    wb.save(SUMMARY_XLSX)


def _scored_fields(res) -> dict:
    """{field: stats} for fields that actually produced comparisons (skip/exclude excluded)."""
    if not res:
        return {}
    out = {}
    for fs in res["field_stats"]:
        if fs.get("macro_f1") is None and fs.get("pct_agreement") is None:
            continue
        out[fs["field"]] = fs
    return out


def _recall_sheet(wb: Workbook, agent: dict, system: dict) -> None:
    """Second worksheet, recall-primary. Surfaces FN and AI-missed-NR (fn_false_nr) so the
    over-conservative-NR failure mode is visible field by field."""
    ws = wb.create_sheet("Recall")
    r = 1
    ws.cell(r, 1, "Periodontitis — RECALL (agent baseline vs system)").font = TITLE_FONT
    r += 2

    # ── scorecard ──────────────────────────────────────────────────────────────
    ws.cell(r, 1, "RECALL SCORECARD — per form").font = SECTION_FONT
    r += 1
    cols = ["Form", "Fields", "Studies",
            "Recall (agent)", "Recall (system)", "ΔRecall",
            "microRecall (agent)", "microRecall (system)",
            "Prec (agent)", "Prec (system)", "F1 (agent)", "F1 (system)",
            "FN (agent)", "FN (system)", "AI-missed-NR (agent)", "AI-missed-NR (system)"]
    hdr_row = r
    for c, name in enumerate(cols, 1):
        cell = ws.cell(r, c, name)
        cell.fill = HDR_FILL; cell.font = HDR_FONT
        cell.alignment = Alignment(horizontal="center", wrap_text=True); cell.border = BORDER
    r += 1

    pooled = {"agent": [0, 0], "system": [0, 0]}  # tp, fn
    macro_rec = {"agent": [], "system": []}
    # AI-missed-NR per form = sum of fn_false_nr across that form's scored fields
    def _missed_nr(res):
        return sum((fs.get("fn_false_nr") or 0) for fs in _scored_fields(res).values())

    for form in FORM_ORDER:
        a = (agent.get(form) or {}).get("summary", {}) if agent.get(form) else {}
        sy = (system.get(form) or {}).get("summary", {}) if system.get(form) else {}
        ar, sr = _num(a.get("macro_recall")), _num(sy.get("macro_recall"))
        if ar is not None: macro_rec["agent"].append(ar)
        if sr is not None: macro_rec["system"].append(sr)
        pooled["agent"][0] += a.get("total_tp", 0) or 0
        pooled["agent"][1] += a.get("total_fn", 0) or 0
        pooled["system"][0] += sy.get("total_tp", 0) or 0
        pooled["system"][1] += sy.get("total_fn", 0) or 0
        vals = [
            FORM_LABEL[form], a.get("n_scored_fields"), a.get("n_scored_studies"),
            ar, sr, _delta(ar, sr),
            _num(a.get("micro_recall")), _num(sy.get("micro_recall")),
            _num(a.get("macro_precision")), _num(sy.get("macro_precision")),
            _num(a.get("macro_f1")), _num(sy.get("macro_f1")),
            a.get("total_fn", 0), sy.get("total_fn", 0),
            _missed_nr(agent.get(form)), _missed_nr(system.get(form)),
        ]
        for c, v in enumerate(vals, 1):
            cell = ws.cell(r, c, v); cell.border = BORDER
            cell.alignment = Alignment(horizontal="center")
        ws.cell(r, 4).fill = _f1_fill(ar) or PatternFill()
        ws.cell(r, 5).fill = _f1_fill(sr) or PatternFill()
        d = _delta(ar, sr)
        if d is not None:
            ws.cell(r, 6).fill = POS if d >= 0 else NEG
        r += 1

    def _micro_rec(tpfn):
        tp, fn = tpfn
        return round(tp / (tp + fn), 3) if (tp + fn) else 0.0
    a_macro = round(sum(macro_rec["agent"]) / len(macro_rec["agent"]), 3) if macro_rec["agent"] else None
    s_macro = round(sum(macro_rec["system"]) / len(macro_rec["system"]), 3) if macro_rec["system"] else None
    overall = ["OVERALL", "", "", a_macro, s_macro, _delta(a_macro, s_macro),
               _micro_rec(pooled["agent"]), _micro_rec(pooled["system"]),
               "", "", "", "", pooled["agent"][1], pooled["system"][1], "", ""]
    for c, v in enumerate(overall, 1):
        cell = ws.cell(r, c, v); cell.border = BORDER; cell.font = Font(bold=True)
        cell.fill = SUB_FILL if c == 1 else PatternFill()
        cell.alignment = Alignment(horizontal="center")
    ws.cell(r, 1).font = Font(bold=True, color="FFFFFF")
    ws.cell(r, 4).fill = _f1_fill(a_macro) or PatternFill()
    ws.cell(r, 5).fill = _f1_fill(s_macro) or PatternFill()
    od = _delta(a_macro, s_macro)
    if od is not None:
        ws.cell(r, 6).fill = POS if od >= 0 else NEG
    r += 3

    # ── field detail ─────────────────────────────────────────────────────────
    ws.cell(r, 1, "RECALL FIELD DETAIL — per field").font = SECTION_FONT
    r += 1
    fcols = ["Form", "Field", "Strategy", "Compared",
             "FN (agent)", "FN (system)", "AI-missed-NR (agent)", "AI-missed-NR (system)",
             "Recall (agent)", "Recall (system)", "ΔRecall"]
    for c, name in enumerate(fcols, 1):
        cell = ws.cell(r, c, name)
        cell.fill = HDR_FILL; cell.font = HDR_FONT
        cell.alignment = Alignment(horizontal="center", wrap_text=True); cell.border = BORDER
    r += 1

    for form in FORM_ORDER:
        amap, smap = _scored_fields(agent.get(form)), _scored_fields(system.get(form))
        fields = list(amap.keys()) + [f for f in smap if f not in amap]
        for fld in fields:
            af, sf = amap.get(fld, {}), smap.get(fld, {})
            ar, sr = _num(af.get("macro_recall")), _num(sf.get("macro_recall"))
            vals = [
                FORM_LABEL[form], fld, af.get("strategy") or sf.get("strategy"),
                af.get("n_compared") if af else sf.get("n_compared"),
                af.get("fn") if af else None, sf.get("fn") if sf else None,
                af.get("fn_false_nr") if af else None, sf.get("fn_false_nr") if sf else None,
                ar, sr, _delta(ar, sr),
            ]
            for c, v in enumerate(vals, 1):
                cell = ws.cell(r, c, v); cell.border = BORDER
                if c >= 4:
                    cell.alignment = Alignment(horizontal="center")
            ws.cell(r, 9).fill = _f1_fill(ar) or PatternFill()
            ws.cell(r, 10).fill = _f1_fill(sr) or PatternFill()
            d = _delta(ar, sr)
            if d is not None:
                ws.cell(r, 11).fill = POS if d >= 0 else NEG
            r += 1

    widths = [22, 30, 22, 10] + [16] * (len(fcols) - 4)
    for c in range(1, len(fcols) + 1):
        ws.column_dimensions[get_column_letter(c)].width = widths[c - 1] if c <= len(widths) else 14
    ws.freeze_panes = ws.cell(hdr_row + 1, 1)


# ── reading already-scored all_forms_metrics.xlsx (oral cancer, antibiotic) ──────
# Sheet per form; columns: Field, Compared, Both NR, GT=NR AI extracted, % Agreement,
# TP, FP, FN, Precision, Recall, F1, AI missed (NR), Strategy.
ORAL_MODELS = ["claude", "gpt", "gemini"]
ORAL_FORM_LABEL = {
    "study_characteristics": "Study Characteristics",
    "patient_population": "Patient Population",
    "reference_standard": "Reference Standard",
    "index_test": "Index Test",
}
ABX_FORM_LABEL = {
    "abx_study_char": "Study Characteristics",
    "abx_patient_pop": "Patient Population",
    "abx_interventions": "Interventions",
    "abx_outcomes": "Dichotomous Outcomes",
}


def _read_all_forms_metrics(path: Path) -> dict:
    """{form_sheet: [field_row_dict, ...]} from a scored all_forms_metrics.xlsx."""
    from openpyxl import load_workbook
    wb = load_workbook(path, read_only=True)
    out = {}
    for sheet in wb.sheetnames:
        ws = wb[sheet]
        rows = list(ws.iter_rows(values_only=True))
        if not rows:
            continue
        hdr = list(rows[0])
        out[sheet] = [dict(zip(hdr, r)) for r in rows[1:] if r and r[0]]
    return out


def _agg_form(field_rows: list) -> dict:
    """macro = mean of per-field F1/P/R; micro = pooled TP/FP/FN."""
    def col(name):
        return [r.get(name) for r in field_rows if isinstance(r.get(name), (int, float))]
    f1s, ps, rs = col("F1"), col("Precision"), col("Recall")
    tp = sum(r.get("TP") or 0 for r in field_rows)
    fp = sum(r.get("FP") or 0 for r in field_rows)
    fn = sum(r.get("FN") or 0 for r in field_rows)
    mp = tp / (tp + fp) if (tp + fp) else 0.0
    mr = tp / (tp + fn) if (tp + fn) else 0.0
    return {
        "n_fields": len(f1s),
        "macro_f1": round(sum(f1s) / len(f1s), 3) if f1s else None,
        "macro_precision": round(sum(ps) / len(ps), 3) if ps else None,
        "macro_recall": round(sum(rs) / len(rs), 3) if rs else None,
        "micro_f1": round(2 * mp * mr / (mp + mr), 3) if (mp + mr) else 0.0,
        "tp": tp, "fp": fp, "fn": fn,
    }


def _hdr_row(ws, r, names):
    for c, name in enumerate(names, 1):
        cell = ws.cell(r, c, name)
        cell.fill = HDR_FILL; cell.font = HDR_FONT
        cell.alignment = Alignment(horizontal="center", wrap_text=True); cell.border = BORDER


def build_oral_cancer_sheet(wb: Workbook) -> None:
    """3-model (claude/gpt/gemini) oral-cancer comparison, read from existing reports."""
    data = {}
    for m in ORAL_MODELS:
        p = _EVAL / "outputs" / "oral_cancer" / m / "all_forms_metrics.xlsx"
        data[m] = _read_all_forms_metrics(p) if p.exists() else {}
    forms = list(ORAL_FORM_LABEL)

    ws = wb.create_sheet("Oral Cancer (3 models)")
    r = 1
    ws.cell(r, 1, "Oral Cancer — Claude vs GPT vs Gemini").font = TITLE_FONT
    r += 2
    ws.cell(r, 1, "SCORECARD — macro F1 / Precision / Recall per model").font = SECTION_FONT
    r += 1
    cols = ["Form", "Fields",
            "F1 (claude)", "F1 (gpt)", "F1 (gemini)",
            "Prec (claude)", "Prec (gpt)", "Prec (gemini)",
            "Recall (claude)", "Recall (gpt)", "Recall (gemini)"]
    hdr_row = r
    _hdr_row(ws, r, cols); r += 1

    pooled = {m: [0, 0, 0] for m in ORAL_MODELS}  # tp,fp,fn
    macro = {m: [] for m in ORAL_MODELS}
    for form in forms:
        aggs = {m: _agg_form(data[m].get(form, [])) for m in ORAL_MODELS}
        for m in ORAL_MODELS:
            if aggs[m]["macro_f1"] is not None:
                macro[m].append(aggs[m]["macro_f1"])
            pooled[m][0] += aggs[m]["tp"]; pooled[m][1] += aggs[m]["fp"]; pooled[m][2] += aggs[m]["fn"]
        vals = [ORAL_FORM_LABEL[form], aggs["claude"]["n_fields"]] + \
               [aggs[m]["macro_f1"] for m in ORAL_MODELS] + \
               [aggs[m]["macro_precision"] for m in ORAL_MODELS] + \
               [aggs[m]["macro_recall"] for m in ORAL_MODELS]
        for c, v in enumerate(vals, 1):
            cell = ws.cell(r, c, v); cell.border = BORDER
            cell.alignment = Alignment(horizontal="center")
        for i, m in enumerate(ORAL_MODELS):           # color F1 cells (cols 3,4,5)
            ws.cell(r, 3 + i).fill = _f1_fill(aggs[m]["macro_f1"]) or PatternFill()
        r += 1
    # OVERALL
    ovr = ["OVERALL", ""] + [round(sum(macro[m]) / len(macro[m]), 3) if macro[m] else None for m in ORAL_MODELS] + [""] * 6
    for c, v in enumerate(ovr, 1):
        cell = ws.cell(r, c, v); cell.border = BORDER; cell.font = Font(bold=True)
        cell.fill = SUB_FILL if c == 1 else PatternFill(); cell.alignment = Alignment(horizontal="center")
    ws.cell(r, 1).font = Font(bold=True, color="FFFFFF")
    for i, m in enumerate(ORAL_MODELS):
        ws.cell(r, 3 + i).fill = _f1_fill(ovr[2 + i]) or PatternFill()
    r += 3

    ws.cell(r, 1, "FIELD DETAIL — F1 per model").font = SECTION_FONT
    r += 1
    fcols = ["Form", "Field", "Strategy", "F1 (claude)", "F1 (gpt)", "F1 (gemini)"]
    _hdr_row(ws, r, fcols); r += 1
    for form in forms:
        # union of fields across models, preserving claude's order first
        per_model = {m: {row["Field"]: row for row in data[m].get(form, [])} for m in ORAL_MODELS}
        order = list(per_model["claude"].keys())
        for m in ("gpt", "gemini"):
            order += [f for f in per_model[m] if f not in order]
        for fld in order:
            strat = next((per_model[m][fld].get("Strategy") for m in ORAL_MODELS if fld in per_model[m]), "")
            f1s = [per_model[m].get(fld, {}).get("F1") for m in ORAL_MODELS]
            vals = [ORAL_FORM_LABEL[form], fld, strat] + f1s
            for c, v in enumerate(vals, 1):
                cell = ws.cell(r, c, v); cell.border = BORDER
                if c >= 4:
                    cell.alignment = Alignment(horizontal="center")
            for i in range(3):
                ws.cell(r, 4 + i).fill = _f1_fill(f1s[i]) or PatternFill()
            r += 1
    for c, w in enumerate([22, 30, 22] + [13] * 8, 1):
        ws.column_dimensions[get_column_letter(c)].width = w
    ws.freeze_panes = ws.cell(hdr_row + 1, 1)


def build_antibiotic_sheet(wb: Workbook) -> None:
    """Antibiotic prophylaxis (claude), read from existing report."""
    p = _EVAL / "outputs" / "antibiotic" / "claude" / "all_forms_metrics.xlsx"
    data = _read_all_forms_metrics(p) if p.exists() else {}
    forms = list(ABX_FORM_LABEL)

    ws = wb.create_sheet("Antibiotic")
    r = 1
    ws.cell(r, 1, "Antibiotic Prophylaxis — system (claude)").font = TITLE_FONT
    r += 2
    ws.cell(r, 1, "SCORECARD — per form").font = SECTION_FONT
    r += 1
    cols = ["Form", "Fields", "macro F1", "micro F1", "Precision", "Recall", "TP", "FP", "FN"]
    hdr_row = r
    _hdr_row(ws, r, cols); r += 1
    macro = []
    pooled = [0, 0, 0]
    for form in forms:
        a = _agg_form(data.get(form, []))
        if a["macro_f1"] is not None:
            macro.append(a["macro_f1"])
        pooled[0] += a["tp"]; pooled[1] += a["fp"]; pooled[2] += a["fn"]
        vals = [ABX_FORM_LABEL[form], a["n_fields"], a["macro_f1"], a["micro_f1"],
                a["macro_precision"], a["macro_recall"], a["tp"], a["fp"], a["fn"]]
        for c, v in enumerate(vals, 1):
            cell = ws.cell(r, c, v); cell.border = BORDER
            cell.alignment = Alignment(horizontal="center")
        ws.cell(r, 3).fill = _f1_fill(a["macro_f1"]) or PatternFill()
        r += 1
    mp = pooled[0] / (pooled[0] + pooled[1]) if (pooled[0] + pooled[1]) else 0
    mr = pooled[0] / (pooled[0] + pooled[2]) if (pooled[0] + pooled[2]) else 0
    micro = round(2 * mp * mr / (mp + mr), 3) if (mp + mr) else 0.0
    ovr = ["OVERALL", "", round(sum(macro) / len(macro), 3) if macro else None, micro,
           "", "", pooled[0], pooled[1], pooled[2]]
    for c, v in enumerate(ovr, 1):
        cell = ws.cell(r, c, v); cell.border = BORDER; cell.font = Font(bold=True)
        cell.fill = SUB_FILL if c == 1 else PatternFill(); cell.alignment = Alignment(horizontal="center")
    ws.cell(r, 1).font = Font(bold=True, color="FFFFFF")
    ws.cell(r, 3).fill = _f1_fill(ovr[2]) or PatternFill()
    r += 3

    ws.cell(r, 1, "FIELD DETAIL").font = SECTION_FONT
    r += 1
    fcols = ["Form", "Field", "Strategy", "Compared", "% Agreement", "TP", "FP", "FN",
             "Precision", "Recall", "F1", "AI missed (NR)"]
    _hdr_row(ws, r, fcols); r += 1
    for form in forms:
        for row in data.get(form, []):
            vals = [ABX_FORM_LABEL[form], row.get("Field"), row.get("Strategy"),
                    row.get("Compared"), row.get("% Agreement"), row.get("TP"), row.get("FP"),
                    row.get("FN"), row.get("Precision"), row.get("Recall"), row.get("F1"),
                    row.get("AI missed (NR)")]
            for c, v in enumerate(vals, 1):
                cell = ws.cell(r, c, v); cell.border = BORDER
                if c >= 4:
                    cell.alignment = Alignment(horizontal="center")
            ws.cell(r, 11).fill = _f1_fill(row.get("F1")) or PatternFill()
            r += 1
    for c, w in enumerate([22, 30, 18, 10, 12, 6, 6, 6, 10, 10, 8, 14], 1):
        ws.column_dimensions[get_column_letter(c)].width = w
    ws.freeze_panes = ws.cell(hdr_row + 1, 1)


def build_ablation_sheet(wb: Workbook, sheet_title: str, dataset_title: str,
                         form_label_map: dict, full_path: Path, desc_path: Path) -> None:
    """Full system (claude) vs desc_only ablation, read from pre-scored all_forms_metrics.xlsx.
    Paired F1/P/R columns + ΔF1 (full − desc_only), one row per form + OVERALL, then field detail."""
    full = _read_all_forms_metrics(full_path) if full_path.exists() else {}
    desc = _read_all_forms_metrics(desc_path) if desc_path.exists() else {}
    forms = list(form_label_map)

    ws = wb.create_sheet(sheet_title)
    r = 1
    ws.cell(r, 1, dataset_title).font = TITLE_FONT
    r += 2
    ws.cell(r, 1, "SCORECARD — macro F1 / Precision / Recall per form (full vs desc_only)").font = SECTION_FONT
    r += 1
    cols = ["Form", "Fields",
            "F1 (full)", "F1 (desc_only)", "ΔF1",
            "microF1 (full)", "microF1 (desc_only)",
            "Prec (full)", "Prec (desc_only)", "Recall (full)", "Recall (desc_only)",
            "TP/FP/FN (full)", "TP/FP/FN (desc_only)"]
    hdr_row = r
    _hdr_row(ws, r, cols); r += 1

    pooled = {"full": [0, 0, 0], "desc": [0, 0, 0]}  # tp,fp,fn
    macro = {"full": [], "desc": []}
    for form in forms:
        fa, da = _agg_form(full.get(form, [])), _agg_form(desc.get(form, []))
        if fa["macro_f1"] is not None: macro["full"].append(fa["macro_f1"])
        if da["macro_f1"] is not None: macro["desc"].append(da["macro_f1"])
        for src, a in (("full", fa), ("desc", da)):
            pooled[src][0] += a["tp"]; pooled[src][1] += a["fp"]; pooled[src][2] += a["fn"]
        ff1, df1 = fa["macro_f1"], da["macro_f1"]
        vals = [form_label_map[form], fa["n_fields"],
                ff1, df1, _delta(ff1, df1),
                fa["micro_f1"], da["micro_f1"],
                fa["macro_precision"], da["macro_precision"],
                fa["macro_recall"], da["macro_recall"],
                f"{fa['tp']}/{fa['fp']}/{fa['fn']}", f"{da['tp']}/{da['fp']}/{da['fn']}"]
        for c, v in enumerate(vals, 1):
            cell = ws.cell(r, c, v); cell.border = BORDER
            cell.alignment = Alignment(horizontal="center")
        ws.cell(r, 3).fill = _f1_fill(ff1) or PatternFill()
        ws.cell(r, 4).fill = _f1_fill(df1) or PatternFill()
        d = _delta(ff1, df1)
        if d is not None:
            ws.cell(r, 5).fill = POS if d >= 0 else NEG
        r += 1

    def _micro(tpfpfn):
        tp, fp, fn = tpfpfn
        p = tp / (tp + fp) if (tp + fp) else 0
        rc = tp / (tp + fn) if (tp + fn) else 0
        return round(2 * p * rc / (p + rc), 3) if (p + rc) else 0.0
    f_macro = round(sum(macro["full"]) / len(macro["full"]), 3) if macro["full"] else None
    d_macro = round(sum(macro["desc"]) / len(macro["desc"]), 3) if macro["desc"] else None
    ovr = ["OVERALL", "", f_macro, d_macro, _delta(f_macro, d_macro),
           _micro(pooled["full"]), _micro(pooled["desc"]), "", "", "", "",
           "/".join(map(str, pooled["full"])), "/".join(map(str, pooled["desc"]))]
    for c, v in enumerate(ovr, 1):
        cell = ws.cell(r, c, v); cell.border = BORDER; cell.font = Font(bold=True)
        cell.fill = SUB_FILL if c == 1 else PatternFill(); cell.alignment = Alignment(horizontal="center")
    ws.cell(r, 1).font = Font(bold=True, color="FFFFFF")
    ws.cell(r, 3).fill = _f1_fill(f_macro) or PatternFill()
    ws.cell(r, 4).fill = _f1_fill(d_macro) or PatternFill()
    od = _delta(f_macro, d_macro)
    if od is not None:
        ws.cell(r, 5).fill = POS if od >= 0 else NEG
    r += 3

    ws.cell(r, 1, "FIELD DETAIL — F1 per field (full vs desc_only)").font = SECTION_FONT
    r += 1
    fcols = ["Form", "Field", "Strategy", "F1 (full)", "F1 (desc_only)", "ΔF1"]
    _hdr_row(ws, r, fcols); r += 1
    for form in forms:
        fmap = {row["Field"]: row for row in full.get(form, [])}
        dmap = {row["Field"]: row for row in desc.get(form, [])}
        order = list(fmap.keys()) + [f for f in dmap if f not in fmap]
        for fld in order:
            strat = (fmap.get(fld) or dmap.get(fld) or {}).get("Strategy", "")
            ff1 = fmap.get(fld, {}).get("F1")
            df1 = dmap.get(fld, {}).get("F1")
            vals = [form_label_map[form], fld, strat, ff1, df1, _delta(_num(ff1), _num(df1))]
            for c, v in enumerate(vals, 1):
                cell = ws.cell(r, c, v); cell.border = BORDER
                if c >= 4:
                    cell.alignment = Alignment(horizontal="center")
            ws.cell(r, 4).fill = _f1_fill(ff1) or PatternFill()
            ws.cell(r, 5).fill = _f1_fill(df1) or PatternFill()
            d = _delta(_num(ff1), _num(df1))
            if d is not None:
                ws.cell(r, 6).fill = POS if d >= 0 else NEG
            r += 1
    for c, w in enumerate([22, 30, 22, 14, 16, 10], 1):
        ws.column_dimensions[get_column_letter(c)].width = w
    ws.freeze_panes = ws.cell(hdr_row + 1, 1)


def main() -> int:
    _load_env()
    if not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit("ERROR: ANTHROPIC_API_KEY not set (eval/.env) — needed for the LLM judge.")

    print("### Scoring AGENT baseline ###")
    agent = score_source("agent", AGENT_FILES)
    print("\n### Scoring SYSTEM ###")
    system = score_source("system", SYSTEM_FILES)

    build_xlsx(agent, system)
    print(f"\n✓ wrote {SUMMARY_XLSX}")

    # console recap
    print(f"\n{'Form':22} {'F1 agent':>9} {'F1 system':>10} {'Δ':>7}")
    for form in FORM_ORDER:
        a = (agent.get(form) or {}).get("summary", {}) if agent.get(form) else {}
        s = (system.get(form) or {}).get("summary", {}) if system.get(form) else {}
        af1, sf1 = a.get("macro_f1"), s.get("macro_f1")
        d = _delta(_num(af1), _num(sf1))
        print(f"{FORM_LABEL[form]:22} {str(af1):>9} {str(sf1):>10} {str(d):>7}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
