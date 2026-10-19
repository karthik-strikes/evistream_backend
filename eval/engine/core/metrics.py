"""
Compute agreement metrics from a list of ComparisonResults.

NR policy:
  - Both NR (MATCH_NA)         → excluded from kappa/F1/precision/recall, counted as agreement in % agreement
  - GT=NR, AI=value (MATCH_NA) → counted as FP (GT verified-absent; AI extracted something that isn't there)
  - AI=NR, GT=value (MATCH_NO) → FN only (AI missed the GT entry)
  - SKIP                       → excluded from all metrics

TP/FP/FN definition (information-retrieval style, no TN):
  - TP : AI value matches GT value
  - FP : AI extracted a value that does not match GT — two sub-types:
           fp_over_extraction : GT=NR but AI extracted a value (hallucination / over-extraction)
           fp_wrong_value     : both sides have a real value but they disagree (wrong extraction)
  - FN : GT has a value that AI did not extract correctly
         — covers AI=NR (missed entirely) and AI=wrong_value (extracted incorrectly)
  Note: a mismatch where both sides have real values counts as 1 FP (wrong_value) + 1 FN.
        a miss where AI=NR counts as 1 FN only.
        GT=NR + AI=value counts as 1 FP (over_extraction) only.
"""

import numpy as np
from sklearn.metrics import cohen_kappa_score
from core.comparator import ComparisonResult, MATCH_YES, MATCH_NO, MATCH_NA, MATCH_SKIP
from config.nr_synonyms import is_nr as _is_nr


def _fn_subtype(ai_raw) -> str:
    """Classify why AI missed a GT value.
    'false_nr'  — AI returned an explicit NR token (prompt-induced over-NR).
    'empty'     — AI returned None/blank (extraction failed or field absent).
    """
    import pandas as pd
    if ai_raw is None or (isinstance(ai_raw, float) and pd.isna(ai_raw)):
        return "empty"
    s = str(ai_raw).strip()
    if s == "" or s.lower() in ("nan", "-", "\u2013", "\u2014", "~"):
        return "empty"
    if s.lower() in ("nr", "n/a", "na", "not reported", "not applicable",
                     "not available", "unclear", "unknown"):
        return "false_nr"
    return "empty"


def _bootstrap_ci(results: list, n_iter: int = 1000):
    """95% bootstrap CI on macro precision, recall, F1.
    Returns (prec_lo, prec_hi, rec_lo, rec_hi, f1_lo, f1_hi) or (None,)*6 if N<3.
    """
    n = len(results)
    if n < 3:
        return (None,) * 6
    rng = np.random.default_rng(42)
    f1s, precs, recs = [], [], []
    for _ in range(n_iter):
        idxs = rng.integers(0, n, size=n)
        tp = fp = fn = 0
        for i in idxs:
            r = results[i]
            if r.match == MATCH_SKIP:
                continue
            if r.match == MATCH_NA:
                if r.ai_clean != "NR" and getattr(r, "gt_nr_is_fp", True):
                    fp += 1
                continue
            if r.match == MATCH_YES:
                tp += 1
            elif r.match == MATCH_NO:
                if _is_nr(r.ai_raw):
                    fn += 1
                else:
                    fp += 1
                    fn += 1
        p  = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        r_ = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        f_ = 2 * p * r_ / (p + r_) if (p + r_) > 0 else 0.0
        f1s.append(f_); precs.append(p); recs.append(r_)
    return (
        round(float(np.percentile(precs, 2.5)),  3), round(float(np.percentile(precs, 97.5)), 3),
        round(float(np.percentile(recs,  2.5)),  3), round(float(np.percentile(recs,  97.5)), 3),
        round(float(np.percentile(f1s,   2.5)),  3), round(float(np.percentile(f1s,   97.5)), 3),
    )


def compute_field_metrics(results: list[ComparisonResult], field_name: str = "") -> dict:
    n_total        = len(results)
    n_skip         = sum(1 for r in results if r.match == MATCH_SKIP)
    n_both_nr      = sum(1 for r in results if r.match == MATCH_NA and r.ai_clean == "NR")
    n_gt_nr_ai_val = sum(1 for r in results if r.match == MATCH_NA and r.ai_clean != "NR")

    # % agreement: both-NR = agreement; GT=NR+AI=value = disagreement (FP); SKIP excluded
    agree_pool   = [r for r in results if r.match != MATCH_SKIP]
    n_agree_pool = len(agree_pool)
    n_agreed     = sum(1 for r in agree_pool
                       if r.match == MATCH_YES
                       or (r.match == MATCH_NA and r.ai_clean == "NR"))
    pct_agreement = round(n_agreed / n_agree_pool * 100, 1) if n_agree_pool else None

    # kappa pool: only real YES/NO comparisons (no SKIP, no NA)
    kappa_pool = [r for r in results if r.match not in (MATCH_SKIP, MATCH_NA)]
    n_compared = len(kappa_pool)

    kappa = None
    confusion_data = None

    # ── Cohen's kappa (needs >= 2 compared + >= 2 distinct labels) ─────────────
    if n_compared >= 2:
        y_true = [r.gt_clean.lower().strip() for r in kappa_pool]
        y_pred = [r.ai_clean.lower().strip() for r in kappa_pool]
        labels = sorted(set(y_true) | set(y_pred))

        if len(labels) >= 2:
            try:
                kappa = round(cohen_kappa_score(y_true, y_pred, labels=labels), 3)
            except Exception:
                kappa = None

        if len(labels) <= 20:
            confusion_data = {"labels": labels, "y_true": y_true, "y_pred": y_pred}

    # ── IR-style TP / FP / FN (no TN) ─────────────────────────────────────────
    # FP is split into two sub-types for diagnostics:
    #   fp_over_extraction : GT=NR but AI extracted a value
    #   fp_wrong_value     : both had real values but they disagreed
    tp = fp = fn = 0
    fp_over_extraction = 0   # AI hallucinated / extracted something GT says isn't there
    fp_wrong_value     = 0   # AI extracted a real value but it was the wrong one

    for r in results:
        if r.match == MATCH_SKIP:
            continue
        if r.match == MATCH_NA:
            if r.ai_clean == "NR":
                continue                  # both NR — excluded entirely
            elif getattr(r, "gt_nr_is_fp", True):
                fp += 1
                fp_over_extraction += 1   # GT=NR but AI extracted a value
            # else: free-text field, GT blank is an annotation gap → excluded
            continue
        if r.match == MATCH_YES:
            tp += 1
        elif r.match == MATCH_NO:
            if _is_nr(r.ai_raw):
                fn += 1                   # AI extracted nothing — missed GT entry
            else:
                fp += 1
                fp_wrong_value += 1       # AI extracted wrong value
                fn += 1                   # GT entry not correctly matched

    precision = round(tp / (tp + fp), 3) if (tp + fp) > 0 else None
    recall    = round(tp / (tp + fn), 3) if (tp + fn) > 0 else None
    if precision is not None and recall is not None and (precision + recall) > 0:
        f1        = round(2 * precision * recall / (precision + recall), 3)
    elif tp == 0 and fp == 0 and fn == 0:
        f1 = precision = recall = None
    else:
        f1        = 0.0
        precision = precision or 0.0
        recall    = recall    or 0.0

    # Under-extraction: AI=NR but GT has a value — split into two sub-types
    fn_false_nr = 0   # AI returned an explicit NR token → prompt-induced
    fn_empty    = 0   # AI returned blank/null → extraction failure
    for r in results:
        if r.match == MATCH_NO and _is_nr(r.ai_raw) and not _is_nr(r.gt_raw):
            if _fn_subtype(r.ai_raw) == "false_nr":
                fn_false_nr += 1
            else:
                fn_empty += 1
    n_under = fn_false_nr + fn_empty

    # MAE for numeric fields
    mae = round(float(np.mean([r.score for r in kappa_pool if r.score is not None])), 3) \
          if any(r.score is not None for r in kappa_pool) else None

    # Bootstrap 95% CI on precision / recall / F1
    prec_lo, prec_hi, rec_lo, rec_hi, f1_lo, f1_hi = _bootstrap_ci(results)

    return dict(
        field=field_name,
        n_total=n_total,
        n_skip=n_skip,
        n_both_nr=n_both_nr,
        n_gt_nr_ai_value=n_gt_nr_ai_val,
        n_compared=n_compared,
        n_agreed=n_agreed,
        pct_agreement=pct_agreement,
        kappa=kappa,
        macro_f1=f1,
        macro_f1_ci_low=f1_lo,
        macro_f1_ci_high=f1_hi,
        macro_precision=precision,
        macro_precision_ci_low=prec_lo,
        macro_precision_ci_high=prec_hi,
        macro_recall=recall,
        macro_recall_ci_low=rec_lo,
        macro_recall_ci_high=rec_hi,
        n_under_extraction=n_under,
        fn_false_nr=fn_false_nr,
        fn_empty=fn_empty,
        mae=mae,
        tp=tp,
        fp=fp,
        fn=fn,
        fp_over_extraction=fp_over_extraction,
        fp_wrong_value=fp_wrong_value,
        confusion_data=confusion_data,
    )


def compute_form_summary(field_metrics: list[dict]) -> dict:
    """Macro and micro aggregation across all scored fields in a form."""
    kappas = [m["kappa"]           for m in field_metrics if m["kappa"]           is not None]
    f1s    = [m["macro_f1"]        for m in field_metrics if m["macro_f1"]        is not None]
    precs  = [m["macro_precision"] for m in field_metrics if m["macro_precision"] is not None]
    recs   = [m["macro_recall"]    for m in field_metrics if m["macro_recall"]    is not None]

    # Micro pooling: sum raw TP/FP/FN across all fields, then compute
    total_tp = sum(m.get("tp", 0) or 0 for m in field_metrics)
    total_fp = sum(m.get("fp", 0) or 0 for m in field_metrics)
    total_fn = sum(m.get("fn", 0) or 0 for m in field_metrics)
    total_fp_over = sum(m.get("fp_over_extraction", 0) or 0 for m in field_metrics)
    total_fp_wrong = sum(m.get("fp_wrong_value",    0) or 0 for m in field_metrics)

    micro_prec = round(total_tp / (total_tp + total_fp), 3) if (total_tp + total_fp) > 0 else None
    micro_rec  = round(total_tp / (total_tp + total_fn), 3) if (total_tp + total_fn) > 0 else None
    if micro_prec and micro_rec and (micro_prec + micro_rec) > 0:
        micro_f1 = round(2 * micro_prec * micro_rec / (micro_prec + micro_rec), 3)
    else:
        micro_f1 = None

    return dict(
        n_scored_fields    = len(f1s),
        # macro (per-field F1 averaged — treats all fields equally)
        macro_kappa        = round(float(np.mean(kappas)), 3) if kappas else None,
        macro_precision    = round(float(np.mean(precs)),  3) if precs  else None,
        macro_recall       = round(float(np.mean(recs)),   3) if recs   else None,
        macro_f1           = round(float(np.mean(f1s)),    3) if f1s    else None,
        # micro (pool all TP/FP/FN first — fields weighted by volume)
        micro_precision    = micro_prec,
        micro_recall       = micro_rec,
        micro_f1           = micro_f1,
        # raw counts
        total_tp           = total_tp,
        total_fp           = total_fp,
        total_fn           = total_fn,
        total_fp_over_extraction = total_fp_over,
        total_fp_wrong_value     = total_fp_wrong,
        total_fn_false_nr  = sum(m.get("fn_false_nr", 0) or 0 for m in field_metrics),
        total_fn_empty     = sum(m.get("fn_empty",    0) or 0 for m in field_metrics),
        total_both_nr      = sum(m.get("n_both_nr",       0) for m in field_metrics),
        total_gt_nr_ai_val = sum(m.get("n_gt_nr_ai_value",0) for m in field_metrics),
    )
