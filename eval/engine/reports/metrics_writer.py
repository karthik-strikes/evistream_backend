"""
Consolidated metrics writer.

One workbook per output folder, ``all_forms_metrics.xlsx``, with one sheet per
form. Each sheet is the form's per-field "Agreement Stats" (the ``stats_df``
returned by ``run_form``), folded into a single workbook at the folder ROOT.

This replaces the old per-form ``*_evaluation.xlsx`` files (reports/excel_writer)
and the notebook ``save_metrics`` cell. The folder root is taken from
``$EVAL_METRICS_DIR`` (default ``../outputs``).

The curated column names (Field, % Agreement, TP/FP/FN, Precision, Recall, F1,
Strategy, …) are kept verbatim so the downstream readers
(``scripts/build_model_comparison.py``, ``agent_baseline/build_summary.py``)
keep working; the kappa / bootstrap-CI / error-breakdown / MAE columns are
appended after them.
"""

import os
import openpyxl
import pandas as pd
from openpyxl.styles import PatternFill, Font, Alignment, Border, Side
from openpyxl.utils import get_column_letter

_DEFAULT_METRICS_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "outputs")


def _metrics_dir(metrics_dir: str = None) -> str:
    return metrics_dir or os.environ.get("EVAL_METRICS_DIR", _DEFAULT_METRICS_DIR)


# (stats_df source column, workbook display name) — also defines column order.
_COLUMNS = [
    ("field",                   "Field"),
    ("n_compared",              "Compared"),
    ("n_both_nr",               "Both NR"),
    ("n_gt_nr_ai_value",        "GT=NR, AI extracted"),
    ("pct_agreement",           "% Agreement"),
    ("kappa",                   "kappa"),
    ("tp",                      "TP"),
    ("fp",                      "FP"),
    ("fn",                      "FN"),
    ("macro_precision",         "Precision"),
    ("macro_recall",            "Recall"),
    ("macro_f1",                "F1"),
    ("macro_f1_ci_low",         "F1_CI_low"),
    ("macro_f1_ci_high",        "F1_CI_high"),
    ("macro_precision_ci_low",  "Precision_CI_low"),
    ("macro_precision_ci_high", "Precision_CI_high"),
    ("macro_recall_ci_low",     "Recall_CI_low"),
    ("macro_recall_ci_high",    "Recall_CI_high"),
    ("n_under_extraction",      "n_under_extraction"),
    ("fn_false_nr",             "AI missed (NR)"),
    ("fn_empty",                "fn_empty"),
    ("fp_over_extraction",      "fp_over_extraction"),
    ("fp_wrong_value",          "fp_wrong_value"),
    ("mae",                     "MAE"),
    ("strategy",                "Strategy"),
]

# Canonical sheet order (oral cancer, then antibiotic, then periodontitis).
_SHEET_ORDER = [
    "study_characteristics", "patient_population", "reference_standard",
    "index_test", "outcomes",
    "abx_study_char", "abx_patient_pop", "abx_interventions", "abx_outcomes",
    "perio_study_char", "perio_patient_pop", "perio_interventions",
    "perio_outcomes", "perio_risk_of_bias",
]

_HDR    = PatternFill("solid", fgColor="2C3E50")
_GREEN  = PatternFill("solid", fgColor="D4EDDA")
_AMBER  = PatternFill("solid", fgColor="FFF3CD")
_RED    = PatternFill("solid", fgColor="F8D7DA")
_HDR_FT = Font(bold=True, color="FFFFFF")
_BORDER = Border(*[Side(border_style="thin", color="BDBDBD")] * 4)
_RND3 = {"Precision", "Recall", "F1", "kappa", "MAE",
         "F1_CI_low", "F1_CI_high", "Precision_CI_low", "Precision_CI_high",
         "Recall_CI_low", "Recall_CI_high"}


def _fill_pct(v):
    try: v = float(v)
    except (TypeError, ValueError): return None
    return _GREEN if v >= 90 else _AMBER if v >= 70 else _RED


def _fill_rate(v):
    try: v = float(v)
    except (TypeError, ValueError): return None
    return _GREEN if v >= 0.85 else _AMBER if v >= 0.65 else _RED


_COLOR = {"% Agreement": _fill_pct, "Precision": _fill_rate,
          "Recall": _fill_rate, "F1": _fill_rate}

# Summary-sheet coloring: RAG on the rate columns, tint on the scope columns so
# "how many did the AI miss / over-produce" reads at a glance.
_BLUE   = PatternFill("solid", fgColor="E7F1FA")
_AMBER2 = PatternFill("solid", fgColor="FCE8CE")
_SUM_RATE  = {"F1", "Precision", "Recall", "F1 (micro)", "Precision (micro)", "Recall (micro)"}
_SUM_PCT   = {"% papers matched"}
_SUM_MISS  = {"Papers w/o AI", "Rows missed (FN)"}
_SUM_EXTRA = {"Papers extra", "Rows extra (over-produced)"}


def _to_display_df(stats_df: pd.DataFrame) -> pd.DataFrame:
    """Select + rename stats_df columns into the merged display schema."""
    pairs = [(src, dst) for src, dst in _COLUMNS if src in stats_df.columns]
    return stats_df[[src for src, _ in pairs]].rename(columns=dict(pairs))


def _write_sheet(ws, df_out: pd.DataFrame) -> None:
    for j, col in enumerate(df_out.columns, 1):
        c = ws.cell(1, j, col)
        c.fill = _HDR; c.font = _HDR_FT
        c.alignment = Alignment(horizontal="center", wrap_text=True); c.border = _BORDER
    for i, (_, row) in enumerate(df_out.iterrows(), start=2):
        for j, col in enumerate(df_out.columns, 1):
            v = row[col]
            if col in _RND3 and isinstance(v, (int, float)):
                v = round(float(v), 3)
            elif col == "% Agreement" and isinstance(v, (int, float)):
                v = round(float(v), 1)
            cell = ws.cell(i, j, v); cell.border = _BORDER
            if col in _COLOR:
                fill = _COLOR[col](v)
                if fill:
                    cell.fill = fill
    widths = {"Field": 36, "Strategy": 18}
    for j, col in enumerate(df_out.columns, 1):
        ws.column_dimensions[get_column_letter(j)].width = widths.get(col, 13)
    ws.freeze_panes = "B2"


def _reorder(wb) -> None:
    present = [s for s in _SHEET_ORDER if s in wb.sheetnames] + \
              [s for s in wb.sheetnames if s not in _SHEET_ORDER]
    wb._sheets = [wb[s] for s in present]


# ── Summary sheet: one ROW per form, the complete count picture ──────────────
# (form_summary source key, workbook display name) — also defines column order.
_SUMMARY_COLUMNS = [
    ("form",                 "Form"),
    # ── 1. Scores (the headline) ──
    ("macro_f1",             "F1"),
    ("macro_precision",      "Precision"),
    ("macro_recall",         "Recall"),
    ("micro_f1",             "F1 (micro)"),
    ("micro_precision",      "Precision (micro)"),
    ("micro_recall",         "Recall (micro)"),
    # ── 2. Papers (the documents) ──
    ("n_gt_studies",         "GT papers"),
    ("n_ai_studies",         "AI papers"),
    ("n_matched",            "Papers matched"),
    ("n_gt_studies_missed",  "Papers w/o AI"),
    ("n_ai_studies_extra",   "Papers extra"),
    ("n_scored_studies",     "Scored papers"),
    ("pct_studies_matched",  "% papers matched"),
    ("n_unannotated_excluded", "Blank papers excluded"),
    # ── 3. Rows (extraction rows; = papers for one-row-per-paper forms, else "—") ──
    ("n_gt_arms",            "GT rows"),
    ("n_ai_arms",            "AI rows"),
    ("n_arms_matched",       "Rows matched"),
    ("n_missed_gt_arms",     "Rows missed (FN)"),
    ("n_over_extraction",    "Rows extra (over-produced)"),
    # ── 4. Details (error breakdown, summed across fields) ──
    ("n_scored_fields",      "Scored fields"),
    ("total_tp",             "TP"),
    ("total_fp",             "FP"),
    ("total_fn",             "FN"),
    ("total_fp_over_extraction", "FP over-extract"),
    ("total_fp_wrong_value", "FP wrong-value"),
    ("total_fn_false_nr",    "FN false-NR"),
    ("total_fn_empty",       "FN empty"),
    ("total_both_nr",        "Both NR"),
    ("total_gt_nr_ai_val",   "GT=NR, AI extracted"),
]


def append_form_summary(form_name: str, form_summary: dict,
                        metrics_dir: str = None) -> str:
    """Add/replace this form's ROW in the "Summary" sheet of all_forms_metrics.xlsx.

    One row per form: GT/AI totals, matched, GT-missed, AI-extra (studies AND arms),
    plus the field-metric rollup. The Summary sheet is kept first in the workbook so
    it is the landing page. "—" is shown where a count does not apply (arm columns on
    level-1 forms).
    """
    out_dir = _metrics_dir(metrics_dir)
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, "all_forms_metrics.xlsx")

    if os.path.exists(out_path):
        wb = openpyxl.load_workbook(out_path)
    else:
        wb = openpyxl.Workbook()
        wb.remove(wb.active)

    display_cols = [dst for _, dst in _SUMMARY_COLUMNS]

    # Preserve rows already written for other forms.
    existing: dict = {}
    if "Summary" in wb.sheetnames:
        ws_old = wb["Summary"]
        headers = [c.value for c in ws_old[1]]
        for vals in ws_old.iter_rows(min_row=2, values_only=True):
            d = dict(zip(headers, vals))
            if d.get("Form"):
                existing[d["Form"]] = d
        del wb["Summary"]

    # This form's row.
    row = {dst: form_summary.get(src) for src, dst in _SUMMARY_COLUMNS}
    row["Form"] = form_name
    existing[form_name] = row

    ws = wb.create_sheet("Summary")
    for j, col in enumerate(display_cols, 1):
        c = ws.cell(1, j, col)
        c.fill = _HDR; c.font = _HDR_FT
        c.alignment = Alignment(horizontal="center", wrap_text=True); c.border = _BORDER
    order = [f for f in _SHEET_ORDER if f in existing] + \
            [f for f in existing if f not in _SHEET_ORDER]
    for i, fname in enumerate(order, start=2):
        d = existing[fname]
        for j, col in enumerate(display_cols, 1):
            raw = d.get(col)
            v = "—" if raw is None else (round(raw, 3) if isinstance(raw, float) else raw)
            cell = ws.cell(i, j, v)
            cell.border = _BORDER
            cell.alignment = Alignment(horizontal="center")
            num = raw if isinstance(raw, (int, float)) else None
            if num is not None:
                if col in _SUM_RATE:
                    if _fill_rate(num): cell.fill = _fill_rate(num)
                elif col in _SUM_PCT:
                    if _fill_pct(num): cell.fill = _fill_pct(num)
                elif col in _SUM_MISS and num > 0:
                    cell.fill = _AMBER2
                elif col in _SUM_EXTRA and num > 0:
                    cell.fill = _BLUE
    for j, col in enumerate(display_cols, 1):
        ws.column_dimensions[get_column_letter(j)].width = 24 if col == "Form" else 13
    ws.freeze_panes = "B2"

    # Keep Summary first.
    wb._sheets = [ws] + [s for s in wb._sheets if s.title != "Summary"]
    wb.save(out_path)
    print(f'  → Summary row for "{form_name}" written to {out_path}')
    return out_path


def append_form_metrics(form_name: str, stats_df: pd.DataFrame,
                        metrics_dir: str = None) -> str:
    """Add/replace this form's sheet in ``<metrics_dir>/all_forms_metrics.xlsx``.

    Re-running a form replaces only its own sheet. Returns the workbook path.
    """
    out_dir = _metrics_dir(metrics_dir)
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, "all_forms_metrics.xlsx")

    if os.path.exists(out_path):
        wb = openpyxl.load_workbook(out_path)
    else:
        wb = openpyxl.Workbook()
        wb.remove(wb.active)

    sheet_name = form_name[:31]
    if sheet_name in wb.sheetnames:
        del wb[sheet_name]
    ws = wb.create_sheet(sheet_name)
    _write_sheet(ws, _to_display_df(stats_df))
    _reorder(wb)
    wb.save(out_path)
    print(f'  → Metrics sheet "{sheet_name}" written to {out_path}')
    return out_path
