"""
Level 1: study-level fuzzy matching (AI paper → GT study).
Level 2: sub-record matching within a study (index test arms only).

Match tables are written to outputs/manual_mappings/ so the user can
review and fix low-confidence matches. If the file already exists,
the user's edits are respected on rerun.
"""

import re
import os
import tempfile
import unicodedata
import pandas as pd
from rapidfuzz import process, fuzz
from typing import Optional

# The match table is an internal cache (cross-form canonical inheritance +
# any manual overrides), not a user-facing report. Default it to a per-process
# temp dir so no `manual_mappings/` folder is written into the output folders.
# Within one scoring run every form shares this dir, so the canonical
# study_characteristics → downstream-form inheritance still works. Set
# $EVAL_MAPPINGS_DIR to persist match tables to a known location instead.
_PROC_MATCH_DIR = None


def _mappings_dir() -> str:
    global _PROC_MATCH_DIR
    env = os.environ.get("EVAL_MAPPINGS_DIR")
    if env:
        return env
    if _PROC_MATCH_DIR is None:
        _PROC_MATCH_DIR = tempfile.mkdtemp(prefix="evistream_match_")
    return _PROC_MATCH_DIR
FUZZY_THRESHOLD      = 75   # Level 1 full-name pass
FALLBACK_THRESHOLD   = 90   # Level 1 last-name-only pass (strict to avoid false positives)
SUBRECORD_THRESHOLD  = 75   # Level 2 test-type pass
CANONICAL_FORM       = "study_characteristics"   # source-of-truth match table


def _load_canonical_lookup(canonical_form: str = CANONICAL_FORM) -> dict:
    """
    Build {_norm(ai_study): (gt_study, score, status)} from the canonical
    match CSV (default study_characteristics). Returns {} if absent.
    """
    canonical_path = os.path.join(_mappings_dir(), f"{canonical_form}_matches.csv")
    if not os.path.exists(canonical_path):
        return {}
    df = pd.read_csv(canonical_path)
    lookup = {}
    for _, row in df.iterrows():
        key = _norm(str(row["ai_study"]))
        lookup[key] = (
            str(row["gt_study"]),
            row["match_score"],
            str(row.get("status", "matched")),
        )
    return lookup


# ── Normalization helpers ──────────────────────────────────────────────────────

def _norm(name: str) -> str:
    s = str(name).strip()
    # Insert space before uppercase letters (camelCase → spaced)
    s = re.sub(r"([a-z])([A-Z])", r"\1 \2", s)
    # Insert space before digit runs (Sharma2021 → Sharma 2021)
    s = re.sub(r"([a-zA-Z])(\d)", r"\1 \2", s)
    # Insert space after digit runs before letters (2021b → 2021 b)
    s = re.sub(r"(\d)([a-zA-Z])", r"\1 \2", s)
    # Replace underscores, hyphens with spaces
    s = re.sub(r"[_\-]", " ", s)
    # Strip common noise suffixes
    s = re.sub(r"\bREF\b\s*\d+", "", s, flags=re.IGNORECASE)
    s = re.sub(r"\bpaper\s+with\s+appendix\b", "", s, flags=re.IGNORECASE)
    s = re.sub(r"\bDTA.*$", "", s, flags=re.IGNORECASE)
    s = s.lower()
    # Normalize unicode accents (Martín → Martin, Tamí → Tami)
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    # Truncate at 4 tokens max (strips appended paper titles like "Jabbar 2020 The diagnostic...")
    tokens = s.split()
    return " ".join(tokens[:4])


def _strip_year(name: str) -> str:
    return re.sub(r"\b\d{4}[ab]?\b", "", name).strip()


# ── Level 1: study matching ────────────────────────────────────────────────────

def _ai_study_id(ai_row: pd.Series) -> str:
    """Return the best available study identifier from an AI row."""
    for col in ("authors_last_name", "Paper", "paper"):
        if col in ai_row.index and pd.notna(ai_row[col]):
            return str(ai_row[col]).strip()
    return str(ai_row.iloc[0]).strip()


def match_studies(
    ai_df: pd.DataFrame,
    gt_df: pd.DataFrame,
    form_name: str,
    force_recompute: bool = False,
    canonical_form: str = CANONICAL_FORM,
) -> pd.DataFrame:
    """
    Match each AI study to a GT study.

    Returns a DataFrame with columns:
      ai_study, gt_study, match_score, match_pass, status
        status: matched / low_confidence / unmatched / manual_override

    Writes to outputs/manual_mappings/<form_name>_matches.csv.
    If that file already exists (and force_recompute=False), reads user edits.
    """
    mappings_dir = _mappings_dir()
    os.makedirs(mappings_dir, exist_ok=True)
    out_path = os.path.join(mappings_dir, f"{form_name}_matches.csv")

    if os.path.exists(out_path) and not force_recompute:
        existing = pd.read_csv(out_path)
        print(f"  [matcher] Loaded existing match table from {out_path}")
        return existing

    # For non-canonical forms, inherit gt_study assignments from study_characteristics
    # so all forms stay consistent. Falls back to fuzzy matching for any study not found.
    canonical = {} if form_name == canonical_form else _load_canonical_lookup(canonical_form)
    if canonical:
        print(f"  [matcher] Using canonical match table from '{canonical_form}' ({len(canonical)} entries)")

    gt_names  = gt_df["_gt_author"].tolist()
    gt_norm   = [_norm(n) for n in gt_names]
    gt_noyear = [_strip_year(n) for n in gt_norm]

    rows = []
    for _, ai_row in ai_df.iterrows():
        ai_name   = _ai_study_id(ai_row)
        ai_norm   = _norm(ai_name)
        ai_noyear = _strip_year(ai_norm)

        # Pass 0: look up in canonical study_characteristics table
        if canonical and ai_norm in canonical:
            gt_study, score, status = canonical[ai_norm]
            rows.append(dict(ai_study=ai_name, gt_study=gt_study,
                             match_score=score, match_pass="canonical", status=status))
            continue

        # Pass 1: full name fuzzy
        r1 = process.extractOne(
            ai_norm, gt_norm,
            scorer=fuzz.token_sort_ratio,
            score_cutoff=FUZZY_THRESHOLD,
        )
        if r1:
            _, score, idx = r1
            status = "low_confidence" if score < 85 else "matched"
            rows.append(dict(ai_study=ai_name, gt_study=gt_names[idx],
                             match_score=round(score, 1), match_pass=1, status=status))
            continue

        # Pass 2: year-stripped token_set_ratio (handles "Kokubun 2023 Evaluation" vs "Kokubun")
        r2 = process.extractOne(
            ai_noyear, gt_noyear,
            scorer=fuzz.token_set_ratio,
            score_cutoff=FALLBACK_THRESHOLD,
        )
        if r2:
            _, score2, idx2 = r2
            rows.append(dict(ai_study=ai_name, gt_study=gt_names[idx2],
                             match_score=round(score2, 1), match_pass=2, status="matched"))
        else:
            rows.append(dict(ai_study=ai_name, gt_study=None,
                             match_score=None, match_pass=None, status="unmatched"))

    match_df = pd.DataFrame(rows)

    low = match_df[match_df["status"] == "low_confidence"]
    unmatched = match_df[match_df["status"] == "unmatched"]
    if not low.empty:
        print(f"\n  ⚠ {len(low)} low-confidence matches (score < 85) — edit {out_path} to fix:")
        for _, r in low.iterrows():
            print(f"    [{r['match_score']:.0f}] '{r['ai_study']}' → '{r['gt_study']}'")
    if not unmatched.empty:
        print(f"\n  ⚠ {len(unmatched)} studies could NOT be matched:")
        for s in unmatched["ai_study"].tolist():
            print(f"    • {s}")

    print(f"\n  ✓ {(match_df['status'].isin(['matched','low_confidence'])).sum()} / {len(match_df)} matched")
    match_df.to_csv(out_path, index=False)
    print(f"  → Written to {out_path}. Edit gt_study column to fix bad matches, then rerun.")
    return match_df


# ── Level 2: sub-record matching (index test arms) ────────────────────────────

HUNGARIAN_THRESHOLD = 0.70   # pairs below this similarity are treated as unmatched

# Assignment weights (Fixes 1 & 2). The old assign_sim was a flat mean over
# type + label + extras, so outcome, comparison and timepoint each got an equal
# vote. When outcome and timepoint DISAGREED the mean cancelled and the pairing
# became a near-tie the optimizer resolved by coincidence — pairing e.g.
# "bleeding" (AI) with "rescue_medication" (GT) just because their timepoints
# matched (Mirashrafi 2021). We now weight the primary row-identity key (type,
# e.g. outcome / test-type) far above the label, and the extra keys (timepoint)
# lowest so they act only as a soft tie-breaker between otherwise-equal
# candidates (Santos 2020, where AI/GT bin timepoints differently). A key that
# is uniform across a study contributes a constant to every pair and so cannot
# skew the assignment, making the high type weight safe even when type doesn't
# discriminate.
ASSIGN_W_TYPE  = 3.0   # outcome / test-type — the primary row identity
ASSIGN_W_LABEL = 2.0   # comparison / arm label
ASSIGN_W_EXTRA = 1.0   # timepoint, reporter, … — soft tie-breaker only


def match_subrecords(
    ai_arms: pd.DataFrame,
    gt_arms: pd.DataFrame,
    study_id: str,
    type_col: str,
    label_col: str,
    extra_key_cols: list = None,
) -> pd.DataFrame:
    """
    Within a matched study pair, match AI test arms to GT test arms using
    the Hungarian algorithm (scipy linear_sum_assignment) for globally optimal
    assignment.

    Assignment cost uses the MEAN of every key's token_sort_ratio (type_col,
    label_col, and any extra_key_cols) — NOT max(type, label). The old max()
    collapsed when the label key was uniform within a study (e.g. perio
    `subgroup`): every pair scored ~1.0, ties were broken arbitrarily, and rows
    stayed mis-aligned so each numeric field compared against the wrong GT row.
    Averaging lets a discriminative key (outcome_type, timepoint) break the tie.

    The keep/drop threshold takes max(similarity) over only the keys that are
    NOT uniform within this study (i.e. have >1 distinct GT value across the
    rows being matched) — a key that's constant for every GT row (e.g.
    comparison="placebo" on every outcome row) carries zero row-discriminating
    information and must never be allowed to single-handedly justify a keep
    decision, regardless of which form it's in. extra_key_cols join this pool
    (previously they were excluded from the keep decision entirely, even though
    they typically exist BECAUSE type+label don't disambiguate — see e.g.
    ibu_continuous_outcomes). If every key is uniform (the common n_ai=n_gt=1
    single-row case, where there's nothing to disambiguate anyway) this falls
    back to the historical max(type, label) so recall on forms whose label key
    IS globally discriminative (e.g. abx outcomes) is unchanged. Confirmed via
    matcher.py bugfix (Kokki 1994 in ibu_dichotomous_outcomes: comparison was
    uniform "placebo" across all 3 rows, masking a completely wrong
    outcome-type pairing). NOTE: this does not fix every mis-pairing — a key
    that's genuinely non-uniform but happens to coincidentally agree on one
    wrong pair (e.g. Santos 2020), or a wrong global Hungarian assignment
    driven by extra_key_cols agreement across a cyclic type-swap (e.g.
    Mirashrafi 2021), are separate, harder problems this does not address.

    extra_key_cols: optional additional columns to fold into the assignment
    similarity (e.g. ["timepoint"] for continuous-outcome rows).

    Returns DataFrame with columns:
      ai_idx, gt_idx, match_score, match_type, status
        match_type: type_match / label_match
        status: matched / unmatched
    """
    import numpy as np
    from scipy.optimize import linear_sum_assignment

    n_ai = len(ai_arms)
    n_gt = len(gt_arms)

    if n_ai == 0 or n_gt == 0:
        # No pairing possible: every AI arm is an (unclassified) over-extraction and
        # every GT arm is a miss the AI never produced. Emit BOTH so the scorer counts
        # the GT misses as false negatives instead of silently dropping them.
        rows = [dict(ai_idx=i, gt_idx=None, match_score=None,
                     match_type=None, status="unmatched") for i in range(n_ai)]
        rows += [dict(ai_idx=None, gt_idx=j, match_score=None,
                      match_type=None, status="unmatched_gt") for j in range(n_gt)]
        return pd.DataFrame(rows)

    def _col_vals(df, col, n):
        return [_norm(str(df[col].iloc[k])) if col in df.columns else "" for k in range(n)]

    ai_types  = _col_vals(ai_arms, type_col, n_ai)
    gt_types  = _col_vals(gt_arms, type_col, n_gt)
    ai_labels = _col_vals(ai_arms, label_col, n_ai)
    gt_labels = _col_vals(gt_arms, label_col, n_gt)

    extra_cols = [c for c in (extra_key_cols or []) if c not in (type_col, label_col)]
    ai_extra = {c: _col_vals(ai_arms, c, n_ai) for c in extra_cols}
    gt_extra = {c: _col_vals(gt_arms, c, n_gt) for c in extra_cols}

    # A key whose GT-side values are constant across this study's rows carries
    # zero row-discriminating information — every AI row scores ~equally
    # against it regardless of which GT row it's paired with. Exclude such
    # keys from the keep/drop candidate pool; keys that DO vary are untouched.
    def _is_uniform(vals):
        return len(set(vals)) <= 1

    eligible = [name for name, gt_vals in
                [("type", gt_types), ("label", gt_labels)]
                + [(c, gt_extra[c]) for c in extra_cols]
                if not _is_uniform(gt_vals)]
    if not eligible:
        # Every configured key is constant for this study — typically the
        # single-row case (n_ai=n_gt=1), where there's nothing to disambiguate
        # anyway. Fall back to the historical max(type, label).
        eligible = ["type", "label"]
        if n_gt > 1:
            print(f"    [match_subrecords] {study_id}: WARNING all match keys uniform "
                  f"across {n_gt} GT rows — keep/drop falls back to legacy max(type,label); "
                  f"these rows may be indistinguishable with the current keys")

    # assign_sim — mean over all keys, used for the optimal assignment (alignment)
    # keep_sim   — max over only the non-uniform (discriminative) keys, used for
    #              the keep/drop threshold + reporting
    assign_sim = np.zeros((n_ai, n_gt), dtype=float)
    keep_sim   = np.zeros((n_ai, n_gt), dtype=float)
    keep_via   = [["" for _ in range(n_gt)] for _ in range(n_ai)]

    for i in range(n_ai):
        for j in range(n_gt):
            ts = fuzz.token_sort_ratio(ai_types[i],  gt_types[j])  / 100.0
            ls = fuzz.token_sort_ratio(ai_labels[i], gt_labels[j]) / 100.0
            extra_s = {c: fuzz.token_sort_ratio(ai_extra[c][i], gt_extra[c][j]) / 100.0
                       for c in extra_cols}
            # Weighted mean: type dominates, label next, extras (timepoint) lowest.
            vals = [ts, ls] + list(extra_s.values())
            wts  = [ASSIGN_W_TYPE, ASSIGN_W_LABEL] + [ASSIGN_W_EXTRA] * len(extra_s)
            assign_sim[i, j] = float(np.average(vals, weights=wts))

            scores = {"type": ts, "label": ls, **extra_s}
            best_name = max(eligible, key=lambda k: scores[k])
            keep_sim[i, j] = scores[best_name]
            keep_via[i][j] = best_name

    # Optimal assignment driven by the combined (mean) similarity
    row_idx, col_idx = linear_sum_assignment(1.0 - assign_sim)

    assigned_ai = set()
    assigned_gt = set()
    rows = []
    for ai_i, gt_j in zip(row_idx.tolist(), col_idx.tolist()):
        score = keep_sim[ai_i, gt_j]
        assigned_ai.add(ai_i)
        if score >= HUNGARIAN_THRESHOLD:
            mtype = f"{keep_via[ai_i][gt_j]}_match"
            assigned_gt.add(gt_j)
            rows.append(dict(ai_idx=ai_i, gt_idx=gt_j,
                             match_score=round(score * 100, 1),
                             match_type=mtype, status="matched"))
        else:
            rows.append(dict(ai_idx=ai_i, gt_idx=None,
                             match_score=round(score * 100, 1),
                             match_type=None, status="unmatched"))

    # AI arms with no GT counterpart (n_ai > n_gt) → over-extraction (out-of-scope
    # or hallucinated; the scorer keeps these aside rather than counting them as FP).
    for ai_i in range(n_ai):
        if ai_i not in assigned_ai:
            rows.append(dict(ai_idx=ai_i, gt_idx=None,
                             match_score=None, match_type=None, status="unmatched"))

    # GT arms with no AI counterpart (n_gt > n_ai, or their best pair fell below the
    # keep threshold) → the AI missed these entirely. Emit them so the scorer counts
    # each as a false negative instead of silently dropping it (previously these
    # vanished, which inflated recall on the per-arm / per-outcome forms).
    for gt_j in range(n_gt):
        if gt_j not in assigned_gt:
            rows.append(dict(ai_idx=None, gt_idx=gt_j,
                             match_score=None, match_type=None, status="unmatched_gt"))

    rows.sort(key=lambda r: r["ai_idx"] if r["ai_idx"] is not None else 10**9)
    n_matched_arms = sum(1 for r in rows if r["status"] == "matched")
    n_missed_arms  = sum(1 for r in rows if r["status"] == "unmatched_gt")
    print(f"    [match_subrecords] {study_id}: {n_ai} AI × {n_gt} GT → "
          f"{n_matched_arms} matched, {n_missed_arms} GT missed")
    return pd.DataFrame(rows)
