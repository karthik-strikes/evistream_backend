"""Rebuild eval/outputs/metrics_summary.xlsx as ONE clean, consistent scorecard.

Single pure read+aggregate pass over the already-scored per-field metric workbooks
(`run_form` output) — no re-scoring, no LLM. Every number comes from the same current
scoring, so the workbook is internally consistent. Regenerates the whole file from a clean
slate (backing up the old one first), producing 7 sheets:

  1. Overview                  — provenance, coverage matrix, color key
  2. Production by Model        — system scorecard, long format (one row per form × model)
  3. Full vs Prompt (Table)     — production vs single-call prompt arm, table forms      [story #1]
  4. Full vs Prompt (Scalar)    — production vs single-call prompt arm, scalar forms     [story #1]
  5. Ablation                   — production vs desc_only (rules/hints-stripped)         [story #2]
  6. Summary                    — periodontitis agent baseline vs production system (F1)
  7. Recall                     — same, recall-focused

Soft / minimalist palette. Raw F1 numbers are left uncolored; only the Δ columns are tinted
(directional: rose = first column higher, sage = second column higher, white = within ±0.02).

    python -m eval.engine.reports.build_metrics_summary
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

EVAL_ROOT = Path("/home/ubuntu/evistream/eval")
sys.path.insert(0, str(EVAL_ROOT))

import pandas as pd
import openpyxl
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from eval.engine.reports.build_full_vs_prompt import (
    _collect, LABELS, DS_LABEL, DATASETS as DS_LIST, MODELS, NON_FORM_SHEETS,
)

OUT = EVAL_ROOT / "outputs"
SUMMARY = OUT / "metrics_summary.xlsx"
AGENT_SCORING = EVAL_ROOT / "studies" / "agent_baseline" / "outputs" / "scoring"

MODEL_LABEL = {"claude": "Claude", "gpt": "GPT", "gemini": "Gemini"}
# Production models available per dataset (no gpt/gemini production run for ibuprofen).
PROD_MODELS = {"oral_cancer": ["claude", "gpt", "gemini"],
               "antibiotic": ["claude", "gemini", "gpt"],
               "periodontitis": ["claude", "gemini", "gpt"],
               "ibuprofen": ["claude", "gemini", "gpt"]}
PERIO_FORMS = ["perio_study_char", "perio_patient_pop", "perio_interventions", "perio_outcomes"]

# ── soft / minimalist palette ────────────────────────────────────────────────
TITLE_FT = Font(bold=True, size=13, color="2F3A45")
HDR_FT = Font(bold=True, color="FFFFFF")
HDR_FILL = PatternFill("solid", fgColor="5E6B7A")          # muted slate
SECTION_FT = Font(bold=True, size=11, color="2F3A45")
SECTION_FILL = PatternFill("solid", fgColor="DCE3EB")      # light slate band
OVR_FT = Font(bold=True, color="2F3A45")
OVR_FILL = PatternFill("solid", fgColor="EEF2F6")
ZEBRA_FILL = PatternFill("solid", fgColor="FAFBFC")        # barely-there row guide
NOTE_FT = Font(italic=True, size=9, color="6B7280")
CTR = Alignment(horizontal="center")
_TOP = Border(top=Side(style="thin", color="9AA7B4"))

# directional Δ tints (diverging, soft)
TIE = PatternFill("solid", fgColor="FFFFFF")
ROSE1 = PatternFill("solid", fgColor="F7DAD5")             # first col mildly ahead
ROSE2 = PatternFill("solid", fgColor="EFB9B0")             # first col clearly ahead
SAGE1 = PatternFill("solid", fgColor="E4EFDC")             # second col mildly ahead
SAGE2 = PatternFill("solid", fgColor="CBE0BC")             # second col clearly ahead


def _delta_fill_soft(d: float) -> PatternFill:
    """Diverging, sign-aware: rose = first column higher, sage = second higher, white = match."""
    if abs(d) <= 0.02:
        return TIE
    if d > 0:
        return ROSE2 if d > 0.05 else ROSE1
    return SAGE2 if d < -0.05 else SAGE1


# soft sequential heatmap for raw scores (0–1): sage = high → amber → rose = low
AMBER = PatternFill("solid", fgColor="FBEFD4")


def _score_fill(v: float) -> PatternFill:
    if v >= 0.90:
        return SAGE2
    if v >= 0.80:
        return SAGE1
    if v >= 0.70:
        return AMBER
    if v >= 0.60:
        return ROSE1
    return ROSE2


def _metrics_path(variant: str, ds: str, model: str) -> Path:
    return OUT / variant / ds / model / "all_forms_metrics.xlsx"


def _sheet_names(path: Path) -> list[str]:
    names = pd.ExcelFile(path).sheet_names if path.exists() else []
    return [s for s in names if s not in NON_FORM_SHEETS]


def _agg(path: Path, sheet: str) -> dict:
    """Form-level aggregate from a per-field metrics sheet (macro = mean; micro = pooled TP/FP/FN)."""
    df = pd.read_excel(path, sheet_name=sheet)
    f1 = pd.to_numeric(df["F1"], errors="coerce")
    pr = pd.to_numeric(df["Precision"], errors="coerce")
    rc = pd.to_numeric(df["Recall"], errors="coerce")
    kp = pd.to_numeric(df["kappa"], errors="coerce") if "kappa" in df.columns else pd.Series(dtype=float)
    tp = int(pd.to_numeric(df["TP"], errors="coerce").fillna(0).sum())
    fp = int(pd.to_numeric(df["FP"], errors="coerce").fillna(0).sum())
    fn = int(pd.to_numeric(df["FN"], errors="coerce").fillna(0).sum())
    miss = (int(pd.to_numeric(df["AI missed (NR)"], errors="coerce").fillna(0).sum())
            if "AI missed (NR)" in df.columns else 0)
    mp = tp / (tp + fp) if (tp + fp) else 0.0
    mr = tp / (tp + fn) if (tp + fn) else 0.0
    micro = 2 * mp * mr / (mp + mr) if (mp + mr) else 0.0
    return {"fields": int(len(df)), "f1": float(f1.mean()), "prec": float(pr.mean()),
            "recall": float(rc.mean()), "kappa": float(kp.mean()) if len(kp.dropna()) else 0.0,
            "micro_f1": float(micro), "micro_prec": float(mp), "micro_recall": float(mr),
            "tp": tp, "fp": fp, "fn": fn, "ai_missed_nr": miss}


# ── shared styling helpers ───────────────────────────────────────────────────
def _hdr(ws, row, headers, start=1):
    for c, h in enumerate(headers, start):
        cell = ws.cell(row, c, h); cell.font = HDR_FT; cell.fill = HDR_FILL; cell.alignment = CTR


def _section_band(ws, row, text, ncols):
    for c in range(1, ncols + 1):
        ws.cell(row, c).fill = SECTION_FILL
    ws.cell(row, 1, text).font = SECTION_FT


def _zebra(ws, row, ncols, skip=()):
    for c in range(1, ncols + 1):
        if c not in skip:
            ws.cell(row, c).fill = ZEBRA_FILL


def _overall_band(ws, row, ncols, delta_col=None, d=None):
    for c in range(1, ncols + 1):
        ws.cell(row, c).fill = OVR_FILL; ws.cell(row, c).font = OVR_FT; ws.cell(row, c).border = _TOP
    if delta_col is not None and d is not None:
        ws.cell(row, delta_col).fill = _delta_fill_soft(d); ws.cell(row, delta_col).alignment = CTR


def _legend(ws, row, a_lbl, b_lbl):
    ws.cell(row, 1, f"Δ color —  rose: {a_lbl} higher   ·   sage: {b_lbl} higher   ·   "
                    f"white: within ±0.02 (match).    F1 values are uncolored.").font = NOTE_FT


def _widths(ws, first_w, rest_w, ncols, first_n=2):
    for c in range(1, ncols + 1):
        ws.column_dimensions[get_column_letter(c)].width = first_w if c <= first_n else rest_w


# ─────────────────────────────────────────────────────────────────────────────
# 1. Overview
# ─────────────────────────────────────────────────────────────────────────────
def _write_overview(wb):
    ws = wb.create_sheet("Overview")
    ws.cell(1, 1, "evistream — extraction metrics scorecard").font = Font(bold=True, size=14, color="2F3A45")
    ws.cell(2, 1, "All numbers from one consistent scoring pass (forms.base_form.run_form); "
                  "macro F1 = mean of per-field F1, micro F1 = pooled TP/FP/FN.").font = NOTE_FT

    ws.cell(4, 1, "Coverage — which variants are scored per dataset × model").font = SECTION_FT
    hdr = ["Dataset", "Model", "Production (full)", "Prompt arm", "desc_only ablation", "Agent baseline"]
    _hdr(ws, 5, hdr)
    r = 6
    for ds in DS_LIST:
        for m in MODELS:
            full = _metrics_path("full_studies", ds, m).exists()
            prompt = (_metrics_path("staged", ds, m).exists() or _metrics_path("table", ds, m).exists())
            desc = _metrics_path("desc_only", ds, m).exists()
            agent = (ds == "periodontitis" and m == "claude"
                     and (AGENT_SCORING / "agent" / "all_forms_metrics.xlsx").exists())
            ws.cell(r, 1, DS_LABEL.get(ds, ds)); ws.cell(r, 2, MODEL_LABEL.get(m, m))
            for c, ok in zip((3, 4, 5, 6), (full, prompt, desc, agent)):
                cell = ws.cell(r, c, "✓" if ok else "—"); cell.alignment = CTR
            if r % 2 == 0:
                _zebra(ws, r, len(hdr))
            r += 1
    r += 2
    # color key swatches
    ws.cell(r, 1, "Color key (comparison sheets)").font = SECTION_FT; r += 1
    for fill, txt in ((ROSE2, "first column clearly higher (>0.05)"), (ROSE1, "first column higher (0.02–0.05)"),
                      (TIE, "match — within ±0.02"),
                      (SAGE1, "second column higher (0.02–0.05)"), (SAGE2, "second column clearly higher (>0.05)")):
        ws.cell(r, 2).fill = fill; ws.cell(r, 2).border = _TOP
        ws.cell(r, 3, txt).font = NOTE_FT; r += 1
    r += 1
    notes = [
        "Sheets:",
        "  • Production by Model — the system scorecard (production multi-call DSPy pipeline), one row per form × model.",
        "  • Full vs Prompt — production vs a single-call hand-tuned prompt arm (table + scalar forms). [comparison #1]",
        "  • Ablation — production vs desc_only (field descriptions only; rules/hints/examples stripped). [comparison #2]",
        "  • Summary / Recall — periodontitis Claude-Code agent baseline vs the production system.",
        "",
        "Coverage gaps (shown so nothing reads as complete when it isn't):",
        "  • No GPT production run for antibiotic / periodontitis (Claude + Gemini only).",
        "  • desc_only ablation and the agent baseline exist for Claude only (agent = periodontitis only).",
    ]
    for i, t in enumerate(notes):
        ws.cell(r + i, 1, t).font = NOTE_FT if (t.startswith(" ") or not t) else Font(bold=True, color="2F3A45")
    _widths(ws, 16, 18, len(hdr), first_n=2)


# ─────────────────────────────────────────────────────────────────────────────
# 2. Production by Model  (long format: one row per form × model)
# ─────────────────────────────────────────────────────────────────────────────
def _write_production(wb):
    ws = wb.create_sheet("Production by Model")
    ws.cell(1, 1, "Production system (multi-call DSPy pipeline) — F1 · microF1 · Precision · Recall, "
                  "per form × model").font = TITLE_FT
    ws.cell(2, 1, "One row per (form, model); a form's models sit together. Score cells heatmapped — "
                  "sage ≥0.80 · amber 0.70–0.80 · rose <0.70.").font = NOTE_FT
    headers = ["Form", "Model", "Fields", "F1", "microF1", "Prec", "Recall"]
    nc = len(headers)
    r = 4
    for ds in DS_LIST:
        models = PROD_MODELS[ds]
        forms = _sheet_names(_metrics_path("full_studies", ds, "claude"))
        if not forms:
            continue
        _section_band(ws, r, DS_LABEL.get(ds, ds), nc); r += 1
        _hdr(ws, r, headers); r += 1
        agg_by_model = {m: [] for m in models}
        for gi, sheet in enumerate(forms):
            for mi, m in enumerate(models):
                p = _metrics_path("full_studies", ds, m)
                ws.cell(r, 1, LABELS.get(sheet, sheet) if mi == 0 else "")
                ws.cell(r, 2, MODEL_LABEL.get(m, m))
                if gi % 2 == 1:                       # shade alternate form groups (id cols)
                    _zebra(ws, r, nc)
                if sheet in _sheet_names(p):
                    a = _agg(p, sheet); agg_by_model[m].append(a)
                    ws.cell(r, 3, a["fields"])
                    for ci, v in zip((4, 5, 6, 7), (a["f1"], a["micro_f1"], a["prec"], a["recall"])):
                        cell = ws.cell(r, ci, round(v, 3)); cell.fill = _score_fill(v); cell.alignment = CTR
                r += 1
        for mi, m in enumerate(models):               # OVERALL (mean) per model
            rows = agg_by_model[m]
            ws.cell(r, 1, "OVERALL (mean)" if mi == 0 else ""); ws.cell(r, 2, MODEL_LABEL.get(m, m))
            _overall_band(ws, r, nc)
            if rows:
                for ci, key in zip((4, 5, 6, 7), ("f1", "micro_f1", "prec", "recall")):
                    v = round(sum(x[key] for x in rows) / len(rows), 3)
                    cell = ws.cell(r, ci, v); cell.fill = _score_fill(v); cell.alignment = CTR
            r += 1
        r += 1
    ws.column_dimensions["A"].width = 24; ws.column_dimensions["B"].width = 10
    for c in range(3, nc + 1):
        ws.column_dimensions[get_column_letter(c)].width = 11
    ws.freeze_panes = "A4"


# ─────────────────────────────────────────────────────────────────────────────
# comparison helpers
# ─────────────────────────────────────────────────────────────────────────────
def _compare_row(extra, a, b):
    d = round(a["f1"] - b["f1"], 3)
    return ([*extra, a["fields"], b["fields"], round(a["f1"], 3), round(b["f1"], 3), d,
             round(a["micro_f1"], 3), round(b["micro_f1"], 3), round(a["prec"], 3), round(b["prec"], 3),
             round(a["recall"], 3), round(b["recall"], 3),
             f"{a['tp']}/{a['fp']}/{a['fn']}", f"{b['tp']}/{b['fp']}/{b['fn']}"], d)


def _finish_compare(ws, ncols, id_widths):
    for i, w in enumerate(id_widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = w
    for c in range(len(id_widths) + 1, ncols + 1):
        ws.column_dimensions[get_column_letter(c)].width = 12
    ws.freeze_panes = "C5"


# ─────────────────────────────────────────────────────────────────────────────
# 3a / 3b. Full vs Prompt — one sheet per arm (Table forms, Scalar forms)
# ─────────────────────────────────────────────────────────────────────────────
def _write_full_vs_prompt(wb, variant: str, sheet_name: str, arm_label: str):
    ws = wb.create_sheet(sheet_name)
    ws.cell(1, 1, f"Production full pipeline vs single-call prompt arm — {arm_label} forms, "
                  f"per (dataset, form, model). ΔF1 = full − prompt").font = TITLE_FT
    _legend(ws, 2, "production (full)", "prompt arm")
    headers = (["Dataset", "Form", "Model", "Fields (full)", "Fields (prompt)",
                "F1 (full)", "F1 (prompt)", "ΔF1 (full−prompt)", "microF1 (full)", "microF1 (prompt)",
                "Prec (full)", "Prec (prompt)", "Recall (full)", "Recall (prompt)",
                "TP/FP/FN (full)", "TP/FP/FN (prompt)"])
    nc = len(headers)
    delta_col = headers.index("ΔF1 (full−prompt)") + 1
    _hdr(ws, 4, headers)
    by_ds = {}
    for row in _collect(variant)[0]:
        by_ds.setdefault(row["ds"], []).append(row)
    r = 5
    for ds in DS_LIST:
        ds_rows = by_ds.get(ds, [])
        if not ds_rows:
            continue
        ds_rows.sort(key=lambda row: (LABELS.get(row["sheet"], row["sheet"]), row["model"]))
        _section_band(ws, r, DS_LABEL.get(ds, ds), nc); r += 1
        for i, row in enumerate(ds_rows):
            extra = [DS_LABEL.get(ds, ds), LABELS.get(row["sheet"], row["sheet"]), row["model"]]
            vals, d = _compare_row(extra, row["full"], row["prompt"])
            for c, v in enumerate(vals, 1):
                ws.cell(r, c, v)
            if i % 2 == 1:
                _zebra(ws, r, nc, skip=(delta_col,))
            ws.cell(r, delta_col).fill = _delta_fill_soft(d); ws.cell(r, delta_col).alignment = CTR
            r += 1
        mf = round(sum(row["full"]["f1"] for row in ds_rows) / len(ds_rows), 3)
        mp = round(sum(row["prompt"]["f1"] for row in ds_rows) / len(ds_rows), 3)
        ws.cell(r, 1, f"{DS_LABEL.get(ds, ds)} — OVERALL (mean)")
        ws.cell(r, headers.index("F1 (full)") + 1, mf)
        ws.cell(r, headers.index("F1 (prompt)") + 1, mp)
        ws.cell(r, delta_col, round(mf - mp, 3))
        _overall_band(ws, r, nc, delta_col, round(mf - mp, 3))
        r += 1
    _finish_compare(ws, nc, id_widths=[14, 24, 9])


# ─────────────────────────────────────────────────────────────────────────────
# 4. Ablation — Full vs desc_only
# ─────────────────────────────────────────────────────────────────────────────
def _write_ablation(wb):
    ws = wb.create_sheet("Ablation (Full vs desc_only)")
    ws.cell(1, 1, "Production full pipeline vs desc_only (field descriptions only; rules/hints/examples "
                  "stripped) — Claude. ΔF1 = full − desc_only").font = TITLE_FT
    _legend(ws, 2, "production (full)", "desc_only")
    headers = (["Dataset", "Form", "Fields (full)", "Fields (desc)", "F1 (full)", "F1 (desc)",
                "ΔF1 (full−desc)", "microF1 (full)", "microF1 (desc)", "Prec (full)", "Prec (desc)",
                "Recall (full)", "Recall (desc)", "TP/FP/FN (full)", "TP/FP/FN (desc)"])
    nc = len(headers)
    delta_col = headers.index("ΔF1 (full−desc)") + 1
    _hdr(ws, 4, headers)
    r = 5
    for ds in ("antibiotic", "periodontitis", "ibuprofen", "oral_cancer"):
        full_p = _metrics_path("full_studies", ds, "claude")
        desc_p = _metrics_path("desc_only", ds, "claude")
        forms = [s for s in _sheet_names(desc_p) if s in _sheet_names(full_p)]
        if not forms:
            continue
        _section_band(ws, r, DS_LABEL.get(ds, ds), nc); r += 1
        ds_full, ds_desc = [], []
        for i, sheet in enumerate(forms):
            a, b = _agg(full_p, sheet), _agg(desc_p, sheet)
            vals, d = _compare_row([DS_LABEL.get(ds, ds), LABELS.get(sheet, sheet)], a, b)
            for c, v in enumerate(vals, 1):
                ws.cell(r, c, v)
            if i % 2 == 1:
                _zebra(ws, r, nc, skip=(delta_col,))
            ws.cell(r, delta_col).fill = _delta_fill_soft(d); ws.cell(r, delta_col).alignment = CTR
            ds_full.append(a["f1"]); ds_desc.append(b["f1"]); r += 1
        mf, md = round(sum(ds_full) / len(ds_full), 3), round(sum(ds_desc) / len(ds_desc), 3)
        ws.cell(r, 1, f"{DS_LABEL.get(ds, ds)} — OVERALL (mean)")
        ws.cell(r, headers.index("F1 (full)") + 1, mf)
        ws.cell(r, headers.index("F1 (desc)") + 1, md)
        ws.cell(r, delta_col, round(mf - md, 3))
        _overall_band(ws, r, nc, delta_col, round(mf - md, 3))
        r += 1
    ws.cell(r + 1, 1, "Note: desc_only was run for Claude on antibiotic, periodontitis, ibuprofen & oral_cancer only.").font = NOTE_FT
    _finish_compare(ws, nc, id_widths=[14, 24])


# ─────────────────────────────────────────────────────────────────────────────
# 5 & 6. Agent baseline vs System (periodontitis)
# ─────────────────────────────────────────────────────────────────────────────
def _write_agent(wb, recall: bool):
    name = "Recall" if recall else "Summary"
    agent_p = AGENT_SCORING / "agent" / "all_forms_metrics.xlsx"
    system_p = AGENT_SCORING / "system" / "all_forms_metrics.xlsx"
    ws = wb.create_sheet(name)
    focus = "Recall" if recall else "F1"
    ws.cell(1, 1, f"Periodontitis — Claude-Code agent baseline vs production system ({focus}-focused). "
                  f"Δ = agent − system").font = TITLE_FT
    if not (agent_p.exists() and system_p.exists()):
        ws.cell(3, 1, "(agent_baseline scoring not found)").font = NOTE_FT
        return
    _legend(ws, 2, "agent baseline", "production system")
    if recall:
        headers = ["Form", "Fields", "Recall (agent)", "Recall (system)", "ΔRecall",
                   "microRecall (agent)", "microRecall (system)", "F1 (agent)", "F1 (system)",
                   "FN (agent)", "FN (system)", "AI-missed-NR (agent)", "AI-missed-NR (system)"]
        prim = "recall"
    else:
        headers = ["Form", "Fields", "F1 (agent)", "F1 (system)", "ΔF1",
                   "microF1 (agent)", "microF1 (system)", "Prec (agent)", "Prec (system)",
                   "Recall (agent)", "Recall (system)", "kappa (agent)", "kappa (system)",
                   "TP/FP/FN (agent)", "TP/FP/FN (system)"]
        prim = "f1"
    nc = len(headers)
    delta_col = 5
    _hdr(ws, 4, headers)
    r = 5
    forms = [s for s in PERIO_FORMS if s in _sheet_names(agent_p) and s in _sheet_names(system_p)]
    ag, sy = [], []
    for i, sheet in enumerate(forms):
        a, s = _agg(agent_p, sheet), _agg(system_p, sheet)
        ag.append(a); sy.append(s)
        d = round(a[prim] - s[prim], 3)
        if recall:
            vals = [LABELS.get(sheet, sheet), a["fields"], round(a["recall"], 3), round(s["recall"], 3), d,
                    round(a["micro_recall"], 3), round(s["micro_recall"], 3), round(a["f1"], 3), round(s["f1"], 3),
                    a["fn"], s["fn"], a["ai_missed_nr"], s["ai_missed_nr"]]
        else:
            vals = [LABELS.get(sheet, sheet), a["fields"], round(a["f1"], 3), round(s["f1"], 3), d,
                    round(a["micro_f1"], 3), round(s["micro_f1"], 3), round(a["prec"], 3), round(s["prec"], 3),
                    round(a["recall"], 3), round(s["recall"], 3), round(a["kappa"], 3), round(s["kappa"], 3),
                    f"{a['tp']}/{a['fp']}/{a['fn']}", f"{s['tp']}/{s['fp']}/{s['fn']}"]
        for c, v in enumerate(vals, 1):
            ws.cell(r, c, v)
        if i % 2 == 1:
            _zebra(ws, r, nc, skip=(delta_col,))
        ws.cell(r, delta_col).fill = _delta_fill_soft(d); ws.cell(r, delta_col).alignment = CTR
        r += 1
    if ag:
        ma = round(sum(x[prim] for x in ag) / len(ag), 3)
        ms = round(sum(x[prim] for x in sy) / len(sy), 3)
        ws.cell(r, 1, "OVERALL (mean)"); ws.cell(r, 3, ma); ws.cell(r, 4, ms)
        ws.cell(r, delta_col, round(ma - ms, 3))
        _overall_band(ws, r, nc, delta_col, round(ma - ms, 3))
        r += 1
    ws.cell(r + 1, 1, "Note: agent vs system are scored on the agent baseline's paper subset "
                      "(apples-to-apples within this comparison); numbers differ slightly from the "
                      "full-corpus 'Production by Model' sheet.").font = NOTE_FT
    _widths(ws, 24, 13, nc, first_n=1)
    ws.column_dimensions["B"].width = 8
    ws.freeze_panes = "C5"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--summary", default=str(SUMMARY))
    args = ap.parse_args()
    summary = Path(args.summary)

    if summary.exists():
        bak = summary.with_name("metrics_summary.bak.xlsx")
        shutil.copy2(summary, bak)
        print(f"  backed up → {bak}")

    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    _write_overview(wb)
    _write_production(wb)
    _write_full_vs_prompt(wb, "table", "Full vs Prompt (Table)", "table")
    _write_full_vs_prompt(wb, "staged", "Full vs Prompt (Scalar)", "scalar")
    _write_ablation(wb)
    _write_agent(wb, recall=False)
    _write_agent(wb, recall=True)
    wb.save(summary)
    print(f"  wrote {len(wb.sheetnames)} sheets: {wb.sheetnames}")
    print(f"  saved → {summary}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
