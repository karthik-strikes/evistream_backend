"""
Base form runner. All 5 form modules call run_form() with their config.
"""

import os
import pandas as pd
from typing import Optional

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))   # engine/ → config, core, forms, reports

from config.field_config import FieldSpec, STRATEGY_LLM_JUDGE, STRATEGY_SKIP, STRATEGY_EXCLUDE, PROMPT_DEV_PAPERS
from config.nr_synonyms import is_nr as _is_nr
from config.nr_synonyms import is_nr
from config import key_term_vocab
from core.loader   import load_ai, load_gt_deduped, load_gt_all_rows, load_gt_aligned, _pick_col
from core.matcher  import match_studies, match_subrecords
from core.comparator import compare_field, ComparisonResult, MATCH_SKIP
from core.metrics  import compute_field_metrics, compute_form_summary
from reports.metrics_writer import append_form_metrics, append_form_summary


def _get_vocab(vocab_name: Optional[str]) -> dict:
    if not vocab_name:
        return {}
    return getattr(key_term_vocab, vocab_name, {})


def _get_llm_judge(use_llm: bool):
    if not use_llm:
        return None
    from core.llm_judge import judge_batch
    return judge_batch


def _get_gt_val(gt_row: pd.Series, gt_df: pd.DataFrame, f) -> any:
    """GT value comes from the aligned sheet, keyed by ai_col."""
    return gt_row.get(f.ai_col)


def run_form(
    form_name:    str,
    fields:       list[FieldSpec],
    ai_path:      str,
    ai_format:    str,
    gt_section:   str,
    level2:       bool = False,
    level2_cfg:   dict = None,
    use_llm:      bool = True,
    force_rematch: bool = False,
    extra_col_keywords: list = None,
    gt_aligned_sheet: str = None,
    aligned_gt_path: str = None,
    gt_key_col: str = "Paper",
    gt_skiprows=None,
    canonical_form: str = "study_characteristics",
    score_missing_papers: bool = False,
) -> dict:
    """
    Run full evaluation for one form. Returns summary dict.

    score_missing_papers: only set True when the AI was RUN on the full GT set. By
    default the AI's output defines the evaluated scope — GT papers with no AI
    extraction are excluded (not penalized as FN); their count is still reported as
    n_gt_studies_missed. Misses WITHIN a matched paper always count as FN.
    """
    print(f"\n{'='*60}")
    print(f"  Running form: {form_name}")
    print(f"{'='*60}")

    # Load data
    ai_df = load_ai(ai_path, ai_format)
    excl = PROMPT_DEV_PAPERS or None
    if gt_aligned_sheet:
        if not aligned_gt_path:
            raise ValueError("aligned_gt_path must be provided when gt_aligned_sheet is set")
        gt_df = load_gt_aligned(aligned_gt_path, gt_aligned_sheet, exclude_papers=excl,
                                key_col=gt_key_col, skiprows=gt_skiprows)
    else:
        # For index test (level2), also pull index_test_sub_form_k which lives in Patient Pop section
        extra_cols = ["index_test_sub_form_k"] if level2 else []
        if extra_col_keywords:
            extra_cols = extra_cols + extra_col_keywords
        gt_loader = load_gt_all_rows if level2 else load_gt_deduped
        gt_df = gt_loader(gt_section, extra_col_keywords=extra_cols or None, exclude_papers=excl)

    llm_judge_fn = _get_llm_judge(use_llm)

    # ── Level 1: study matching ────────────────────────────────────────────────
    match_table = match_studies(ai_df, gt_df, form_name, force_recompute=force_rematch,
                                canonical_form=canonical_form)
    matched = match_table[match_table["status"].isin(["matched", "low_confidence", "manual_override"])]
    n_matched = len(matched)
    pct_matched = round(n_matched / len(match_table) * 100, 1)

    # ── Field comparison ───────────────────────────────────────────────────────
    # Only scored (non-skip, non-exclude) fields
    scored_fields = [f for f in fields if f.strategy not in (STRATEGY_SKIP, STRATEGY_EXCLUDE)]
    all_results: dict[str, list[ComparisonResult]] = {f.ai_col: [] for f in scored_fields}

    comparison_rows = []
    discrepancy_rows = []
    n_unannotated = 0      # studies where GT has zero filled scored fields
    unannotated_studies = []
    n_over_extraction = 0  # level2: AI arms with no GT match — out-of-scope OR hallucinated
                           #         (kept aside, NOT counted as FP until paper grounding)
    n_missed_gt_arms  = 0  # level2: GT arms the AI never produced — counted as false negatives
    n_matched_arms    = 0  # level2: AI↔GT arm pairs that aligned and were scored
    n_missed_papers   = 0  # GT papers the AI never extracted at all — folded into FN

    # For level2 forms the AI sheet is one-row-per-arm, so `matched` has duplicate
    # rows per study. Each study's arms are compared once inside _run_level2, so
    # iterate one row per (ai_study, gt_study) pair — otherwise a study with k arms
    # would be re-compared k times, inflating counts to k² (e.g. 63 instead of 22).
    iter_table = (matched.drop_duplicates(subset=["ai_study", "gt_study"])
                  if level2 else matched)

    for _, m in iter_table.iterrows():
        ai_rows = ai_df[ai_df.apply(
            lambda r: _get_ai_study_id(r) == m["ai_study"], axis=1
        )]
        gt_rows = gt_df[gt_df["_gt_author"] == m["gt_study"]]

        if ai_rows.empty or gt_rows.empty:
            continue

        # ── Fully-unannotated check ──────────────────────────────────────────
        # If GT has zero non-null values across ALL scored fields for this
        # study, the annotator never touched it → exclude entire study.
        # Partial blanks (some fields filled, some blank) still get scored;
        # blank individual fields are treated as NR → AI over-extraction.
        if not level2:
            gt_row = gt_rows.iloc[0]
            n_gt_filled = sum(
                1 for f in scored_fields
                if not is_nr(_get_gt_val(gt_row, gt_df, f))
            )
            if n_gt_filled == 0:
                n_unannotated += 1
                unannotated_studies.append(m["gt_study"])
                continue

        if level2 and level2_cfg:
            n_oe, n_missed, n_marm = _run_level2(
                ai_rows=ai_rows, gt_rows=gt_rows,
                fields=scored_fields, level2_cfg=level2_cfg,
                all_results=all_results, comparison_rows=comparison_rows,
                discrepancy_rows=discrepancy_rows,
                llm_judge_fn=llm_judge_fn,
                ai_study=m["ai_study"], gt_study=m["gt_study"],
            )
            n_over_extraction += n_oe
            n_missed_gt_arms  += n_missed
            n_matched_arms    += n_marm
        else:
            ai_row = ai_rows.iloc[0]
            gt_row = gt_rows.iloc[0]
            row_data = {"AI_study": m["ai_study"], "GT_study": m["gt_study"],
                        "match_score": m["match_score"]}
            _compare_row(
                ai_row=ai_row, gt_row=gt_row, fields=scored_fields,
                gt_df=gt_df, all_results=all_results,
                row_data=row_data, comparison_rows=comparison_rows,
                discrepancy_rows=discrepancy_rows, llm_judge_fn=llm_judge_fn,
            )

    # ── GT papers with no AI extraction ──────────────────────────────────────────
    # Only fold these into recall (as FN) when the AI was actually RUN on the full GT
    # set. By default the AI's output defines the evaluated scope — papers it was never
    # given are excluded, not penalized (their count is still reported as
    # n_gt_studies_missed). Misses WITHIN a matched paper always count.
    if score_missing_papers and "_gt_author" in gt_df.columns:
        matched_gt = set(matched["gt_study"]) if not matched.empty else set()
        for gt_study in gt_df["_gt_author"].dropna().unique():
            if gt_study in matched_gt:
                continue
            gt_rows = gt_df[gt_df["_gt_author"] == gt_study]
            if gt_rows.empty:
                continue
            has_data = any(not is_nr(_get_gt_val(r, gt_df, f))
                           for _, r in gt_rows.iterrows() for f in scored_fields)
            if not has_data:
                continue
            label_key = level2_cfg["level2_label_key"] if (level2 and level2_cfg) else None
            for _, gt_row in gt_rows.iterrows():
                _record_missed_gt_row(
                    gt_row, scored_fields, gt_df, all_results,
                    comparison_rows, discrepancy_rows,
                    ai_study="(missing — AI never extracted this paper)",
                    gt_study=gt_study, label_key=label_key, kind="paper")
            if level2:
                n_missed_gt_arms += len(gt_rows)   # these rows are misses too
            n_missed_papers += 1
    if n_missed_papers:
        print(f"  ⚠ {n_missed_papers} GT paper(s) the AI never extracted → their answers counted as misses (FN)")

    # ── Metrics ────────────────────────────────────────────────────────────────
    field_stats = []
    confusion_data_by_field: dict = {}
    for f in scored_fields:
        results = all_results.get(f.ai_col, [])
        if not results:
            continue
        stats = compute_field_metrics(results, field_name=f.ai_col)
        stats["strategy"] = f.strategy
        # Pull out confusion_data — dicts can't live in a DataFrame cleanly
        cd = stats.pop("confusion_data", None)
        if cd:
            confusion_data_by_field[f.ai_col] = cd
        field_stats.append(stats)

    if n_unannotated:
        print(f"\n  ⚠ {n_unannotated} studies excluded (GT fully unannotated): {unannotated_studies}")

    form_summary = compute_form_summary(field_stats)
    form_summary["form"] = form_name
    form_summary["n_ai_studies"] = len(match_table)
    form_summary["n_matched"] = n_matched
    form_summary["n_unannotated_excluded"] = n_unannotated
    form_summary["n_scored_studies"] = n_matched - n_unannotated
    form_summary["pct_studies_matched"] = pct_matched
    # Level-2 row accounting (0 for level-1 forms):
    #   n_over_extraction — AI arms with no GT match; each is out-of-scope OR
    #     hallucinated. Kept ASIDE, not counted as FP, so precision here is an honest
    #     UPPER BOUND. A future paper-grounding step splits these (hallucinated → FP).
    #   n_missed_gt_arms  — GT arms the AI never produced; already folded into recall
    #     as false negatives.
    form_summary["n_over_extraction"] = n_over_extraction if level2 else None
    form_summary["n_missed_gt_arms"]  = n_missed_gt_arms if level2 else None

    # ── Full row/study accounting so the workbook shows the complete picture ──────
    # Study level (every form). match_table has one row per AI study.
    n_gt_studies       = int(gt_df["_gt_author"].nunique()) if "_gt_author" in gt_df.columns else None
    gt_studies_matched = int(matched["gt_study"].nunique()) if not matched.empty else 0
    form_summary["n_gt_studies"]        = n_gt_studies
    form_summary["n_ai_studies_extra"]  = len(match_table) - n_matched               # AI studies with no GT match
    form_summary["n_gt_studies_missed"] = (n_gt_studies - gt_studies_matched) if n_gt_studies is not None else None
    # Arm level (level-2 forms only; AI/GT rows are per-arm / per-outcome). None for level-1.
    form_summary["n_arms_matched"] = n_matched_arms                    if level2 else None
    form_summary["n_gt_arms"]      = n_matched_arms + n_missed_gt_arms if level2 else None
    form_summary["n_ai_arms"]      = n_matched_arms + n_over_extraction if level2 else None

    # ── Write reports ──────────────────────────────────────────────────────────
    comparison_df   = pd.DataFrame(comparison_rows) if comparison_rows else pd.DataFrame()
    discrepancy_df  = pd.DataFrame(discrepancy_rows) if discrepancy_rows else pd.DataFrame()
    stats_df        = pd.DataFrame(field_stats)

    if not comparison_df.empty:
        append_form_metrics(form_name, stats_df)
        append_form_summary(form_name, form_summary)

    print(f"\n  Summary: {form_summary}")
    return {
        "summary":        form_summary,
        "field_stats":    field_stats,
        "stats_df":       stats_df,
        "comparison_df":  comparison_df,
        "discrepancy_df": discrepancy_df,
        "match_table":    match_table,
    }


def _get_ai_study_id(row: pd.Series) -> str:
    for col in ("authors_last_name", "Paper", "paper"):
        if col in row.index and pd.notna(row[col]):
            return str(row[col]).strip()
    return str(row.iloc[0]).strip()


def _compare_row(ai_row, gt_row, fields, gt_df, all_results,
                 row_data, comparison_rows, discrepancy_rows, llm_judge_fn):
    from core.comparator import ComparisonResult, MATCH_NA, MATCH_NO, MATCH_SKIP, MATCH_YES
    from core.cleaner import normalize_string

    llm_fields   = [f for f in fields if f.strategy == STRATEGY_LLM_JUDGE]
    other_fields = [f for f in fields if f.strategy != STRATEGY_LLM_JUDGE]

    # ── Non-LLM fields ────────────────────────────────────────────────────────
    for f in other_fields:
        ai_val = ai_row.get(f.ai_col, None) if f.ai_col in ai_row.index else None
        gt_val = _get_gt_val(gt_row, gt_df, f)
        result = compare_field(
            ai_val=ai_val, gt_val=gt_val,
            strategy=f.strategy,
            tolerance=f.tolerance,
            vocab=_get_vocab(f.vocab),
        )
        _record(f, result, ai_val, gt_val, all_results, row_data, discrepancy_rows)

    # ── LLM fields: handle NR locally, batch the rest ────────────────────────
    if llm_fields:
        llm_pairs   = []
        llm_nr      = {}
        llm_raw     = {}

        for f in llm_fields:
            ai_val = ai_row.get(f.ai_col, None) if f.ai_col in ai_row.index else None
            gt_val = _get_gt_val(gt_row, gt_df, f)
            llm_raw[f.ai_col] = (ai_val, gt_val)

            if _is_nr(ai_val) and _is_nr(gt_val):
                llm_nr[f.ai_col] = ComparisonResult(
                    ai_raw=ai_val, gt_raw=gt_val, ai_clean="NR", gt_clean="NR",
                    match=MATCH_NA, note="both NR — excluded from kappa/F1")
            elif _is_nr(ai_val):
                llm_nr[f.ai_col] = ComparisonResult(
                    ai_raw=ai_val, gt_raw=gt_val,
                    ai_clean="NR", gt_clean=str(gt_val),
                    match=MATCH_NO, note="AI is NR")
            elif _is_nr(gt_val):
                # Free-text field with a GT blank → annotation gap, not a
                # hallucination; don't count as an over-extraction FP.
                llm_nr[f.ai_col] = ComparisonResult(
                    ai_raw=ai_val, gt_raw=gt_val,
                    ai_clean=str(ai_val), gt_clean="NR",
                    match=MATCH_NA, note="GT is NR — excluded", gt_nr_is_fp=False)
            elif normalize_string(ai_val) == normalize_string(gt_val):
                # Identical after normalization → unambiguous match. Never send to
                # the judge: it wastes an API call and an omitted/garbled batch key
                # can flip a verbatim-equal pair (e.g. "2009" vs "2009") to NO.
                llm_nr[f.ai_col] = ComparisonResult(
                    ai_raw=ai_val, gt_raw=gt_val,
                    ai_clean=str(ai_val), gt_clean=str(gt_val),
                    match=MATCH_YES, note="exact match — judge skipped")
            else:
                llm_pairs.append({"field_name": f.ai_col,
                                  "ai_val": ai_val, "gt_val": gt_val})

        batch = llm_judge_fn(llm_pairs) if (llm_judge_fn and llm_pairs) else {}

        for f in llm_fields:
            ai_val, gt_val = llm_raw[f.ai_col]
            if f.ai_col in llm_nr:
                result = llm_nr[f.ai_col]
            elif f.ai_col in batch:
                result = batch[f.ai_col]
            else:
                result = ComparisonResult(
                    ai_raw=ai_val, gt_raw=gt_val,
                    ai_clean=str(ai_val) if ai_val is not None else "",
                    gt_clean=str(gt_val) if gt_val is not None else "",
                    match=MATCH_SKIP, note="LLM judge not configured")
            _record(f, result, ai_val, gt_val, all_results, row_data, discrepancy_rows)

    comparison_rows.append(row_data)


def _record(f, result, ai_val, gt_val, all_results, row_data, discrepancy_rows):
    from core.comparator import MATCH_NA, MATCH_NO
    from core.metrics import _fn_subtype
    all_results[f.ai_col].append(result)
    row_data[f"AI_{f.ai_col}"]    = str(ai_val) if ai_val is not None else ""
    row_data[f"GT_{f.ai_col}"]    = str(gt_val) if gt_val is not None else ""
    row_data[f"MATCH_{f.ai_col}"] = result.match
    if result.match == MATCH_NO:
        # Classify FN sub-type: explicit NR token vs blank/null
        fn_sub = _fn_subtype(ai_val) if _is_nr(ai_val) and not _is_nr(gt_val) else ""
        discrepancy_rows.append({
            "AI_study":   row_data.get("AI_study"),
            "GT_study":   row_data.get("GT_study"),
            "field":      f.ai_col,
            "strategy":   f.strategy,
            "AI_value":   str(ai_val),
            "GT_value":   str(gt_val),
            "type":       "mismatch",
            "fn_subtype": fn_sub,
            "note":       result.note,
        })
    elif result.match == MATCH_NA and result.ai_clean != "NR" and result.gt_clean == "NR":
        discrepancy_rows.append({
            "AI_study":   row_data.get("AI_study"),
            "GT_study":   row_data.get("GT_study"),
            "field":      f.ai_col,
            "strategy":   f.strategy,
            "AI_value":   str(ai_val),
            "GT_value":   str(gt_val),
            "type":       "potential_over_extraction",
            "fn_subtype": "",
            "note":       "GT=NR but AI extracted — check if this is in the paper or hallucinated",
        })


def _run_level2(ai_rows, gt_rows, fields, level2_cfg, all_results,
                comparison_rows, discrepancy_rows, llm_judge_fn, ai_study, gt_study):
    """Match sub-records (arms / outcomes) within a study pair and score them.

    Returns (n_over_extraction, n_missed_gt_arms):
      - matched AI↔GT arm pairs  → scored field-by-field (TP/FP/FN as usual).
      - a GT arm with no AI partner  → the AI missed a whole in-scope row: each of
        its filled fields is recorded as a false negative. This is what stops recall
        from being inflated on per-arm / per-outcome forms (previously such rows were
        silently dropped).
      - an AI arm with no GT partner → an over-extraction. It is NOT scored as an FP
        here: it is either out-of-scope (legitimate) or hallucinated, and only
        grounding against the paper can tell those apart. We tally it aside so
        precision stays an honest UPPER BOUND until that grounding step runs.
    """
    sub_matches = match_subrecords(
        ai_arms=ai_rows,
        gt_arms=gt_rows,
        study_id=ai_study,
        type_col=level2_cfg["level2_type_key"],
        label_col=level2_cfg["level2_label_key"],
        extra_key_cols=level2_cfg.get("level2_extra_keys"),
    )

    def _missing(v):
        return v is None or (isinstance(v, float) and pd.isna(v))

    n_over_extraction = 0
    n_missed_gt_arms  = 0
    n_matched_arms    = 0
    for _, sm in sub_matches.iterrows():
        ai_missing = _missing(sm["ai_idx"])
        gt_missing = _missing(sm["gt_idx"])

        # AI arm with no GT partner → over-extraction (kept aside, not an FP yet)
        if not ai_missing and gt_missing:
            n_over_extraction += 1
            ai_row = ai_rows.iloc[int(sm["ai_idx"])]
            discrepancy_rows.append({
                "AI_study": ai_study, "GT_study": gt_study,
                "field": level2_cfg["level2_label_key"], "strategy": "row_match",
                "AI_value": str(ai_row.get(level2_cfg["level2_label_key"], "")),
                "GT_value": "", "type": "over_extraction_unclassified", "fn_subtype": "",
                "note": "AI arm has no GT match — out-of-scope or hallucinated "
                        "(needs paper grounding); excluded from precision for now",
            })
            continue

        # GT arm the AI never produced → real miss → false negative on filled fields
        if ai_missing and not gt_missing:
            gt_row = gt_rows.iloc[int(sm["gt_idx"])]
            _record_missed_gt_row(gt_row, fields, gt_rows, all_results,
                                  comparison_rows, discrepancy_rows,
                                  ai_study, gt_study,
                                  label_key=level2_cfg["level2_label_key"], kind="arm")
            n_missed_gt_arms += 1
            continue

        if ai_missing and gt_missing:
            continue

        # Both present → normal per-arm field comparison
        ai_row = ai_rows.iloc[int(sm["ai_idx"])]
        gt_row = gt_rows.iloc[int(sm["gt_idx"])]
        row_data = {
            "AI_study": ai_study, "GT_study": gt_study,
            "AI_test_arm": ai_row.get(level2_cfg["level2_label_key"], ""),
            "GT_test_arm": gt_row.get(level2_cfg["level2_label_key"], ""),
            "arm_match_score": sm["match_score"],
            "arm_match_type":  sm["match_type"],
        }
        _compare_row(
            ai_row=ai_row, gt_row=gt_row, fields=fields,
            gt_df=gt_rows, all_results=all_results,
            row_data=row_data, comparison_rows=comparison_rows,
            discrepancy_rows=discrepancy_rows, llm_judge_fn=llm_judge_fn,
        )
        n_matched_arms += 1

    return n_over_extraction, n_missed_gt_arms, n_matched_arms


def _record_missed_gt_row(gt_row, fields, gt_df, all_results, comparison_rows,
                          discrepancy_rows, ai_study, gt_study, label_key=None, kind="row"):
    """AI produced nothing matching this GT row → every filled GT field is a false
    negative (a miss). Two callers: a GT arm the AI didn't produce inside a matched
    paper (kind="arm"), and a whole GT paper the AI never extracted (kind="paper").
    Blank GT fields are recorded as NA (both-NR) so they stay excluded, exactly like a
    normal comparison. `label_key` (level-2 only) adds the arm-label columns."""
    from core.comparator import ComparisonResult, MATCH_NA, MATCH_NO
    row_data = {"AI_study": ai_study, "GT_study": gt_study}
    if label_key is not None:
        row_data["AI_test_arm"]    = f"(missing — AI produced no matching {kind})"
        row_data["GT_test_arm"]    = gt_row.get(label_key, "")
        row_data["arm_match_score"] = None
        row_data["arm_match_type"]  = None
    for f in fields:
        gt_val = _get_gt_val(gt_row, gt_df, f)
        if is_nr(gt_val):
            result = ComparisonResult(ai_raw=None, gt_raw=gt_val, ai_clean="NR",
                                      gt_clean="NR", match=MATCH_NA,
                                      note=f"both NR — AI {kind} missing, GT blank")
        else:
            result = ComparisonResult(ai_raw=None, gt_raw=gt_val, ai_clean="NR",
                                      gt_clean=str(gt_val), match=MATCH_NO,
                                      note=f"AI produced no matching {kind} — GT value missed (FN)")
            discrepancy_rows.append({
                "AI_study": ai_study, "GT_study": gt_study,
                "field": f.ai_col, "strategy": f.strategy,
                "AI_value": "", "GT_value": str(gt_val),
                "type": f"missed_gt_{kind}", "fn_subtype": "empty",
                "note": f"AI produced no matching {kind} for this GT row",
            })
        all_results[f.ai_col].append(result)
        row_data[f"AI_{f.ai_col}"] = ""
        row_data[f"GT_{f.ai_col}"] = str(gt_val) if gt_val is not None else ""
        row_data[f"MATCH_{f.ai_col}"] = result.match
    comparison_rows.append(row_data)
