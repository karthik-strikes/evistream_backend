"""
Field comparison strategies. Each function returns a ComparisonResult.
"""

from dataclasses import dataclass, field
from typing import Optional, Any
import re
import sys, os
import pandas as pd
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.cleaner import (
    normalize_string, strip_year_suffix, to_float, to_int,
    parse_date_range, extract_terms, extract_counts, extract_percent,
    jaccard, unpack_list_field, parse_duration_to_days,
)
from config.nr_synonyms import is_nr
from config.field_config import (
    STRATEGY_EXACT_NORMALIZE, STRATEGY_NUMERIC_EXACT, STRATEGY_NUMERIC_TOLERANCE,
    STRATEGY_DURATION, STRATEGY_DATE_RANGE, STRATEGY_SET_TERMS, STRATEGY_PARSE_COUNTS,
    STRATEGY_PARSE_PERCENT, STRATEGY_LLM_JUDGE, STRATEGY_SKIP, STRATEGY_EXCLUDE,
)


_UNCLEAR_PREFIX_RE = re.compile(r"^\s*unclear\b[^:]*:\s*", re.IGNORECASE)


def _strip_unclear_prefix(value):
    """Drop a leading 'Unclear:' / 'Unclear (no biopsy named):' tag the AI emits
    when it's hedging; the content after the colon is what should be scored."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return value
    return _UNCLEAR_PREFIX_RE.sub("", str(value))

MATCH_YES   = "YES"
MATCH_NO    = "NO"
MATCH_NA    = "NA"     # both NR
MATCH_SKIP  = "SKIP"


@dataclass
class ComparisonResult:
    ai_raw:       Any
    gt_raw:       Any
    ai_clean:     str
    gt_clean:     str
    match:        str          # YES / NO / NA / SKIP
    score:        Optional[float] = None   # Jaccard for sets; absolute diff for numeric
    note:         str          = ""
    llm_reasoning: str         = ""
    gt_nr_is_fp:  bool         = True       # GT=NR+AI=value counts as FP (over-extraction).
                                            # Set False for free-text fields where a GT blank
                                            # is an annotation gap, not a hallucination.


def _make_na(ai_raw, gt_raw) -> ComparisonResult:
    return ComparisonResult(ai_raw=ai_raw, gt_raw=gt_raw,
                            ai_clean="NR", gt_clean="NR",
                            match=MATCH_NA, note="both NR — excluded from kappa/F1")


def _make_skip(ai_raw, gt_raw) -> ComparisonResult:
    return ComparisonResult(ai_raw=ai_raw, gt_raw=gt_raw,
                            ai_clean="", gt_clean="", match=MATCH_SKIP)


# ── Strategy implementations ───────────────────────────────────────────────────

def compare_exact_normalize(ai_val, gt_val) -> ComparisonResult:
    if is_nr(ai_val) and is_nr(gt_val):
        return _make_na(ai_val, gt_val)
    ai_c = normalize_string(ai_val)
    gt_c = normalize_string(gt_val)
    note = ""
    if ";" in str(gt_val):
        note = "GT has combined value"
    match = MATCH_YES if ai_c == gt_c else MATCH_NO
    return ComparisonResult(ai_raw=ai_val, gt_raw=gt_val,
                            ai_clean=ai_c, gt_clean=gt_c, match=match, note=note)


def compare_numeric_exact(ai_val, gt_val) -> ComparisonResult:
    # Strip year suffix before converting (for year_of_study field)
    ai_str = strip_year_suffix(str(ai_val)) if not is_nr(ai_val) else ai_val
    gt_str = strip_year_suffix(str(gt_val)) if not is_nr(gt_val) else gt_val

    ai_n = to_int(ai_str)
    gt_n = to_int(gt_str)

    if ai_n is None and gt_n is None:
        return _make_na(ai_val, gt_val)

    ai_c = str(ai_n) if ai_n is not None else "NR"
    gt_c = str(gt_n) if gt_n is not None else "NR"

    if ai_n is None or gt_n is None:
        return ComparisonResult(ai_raw=ai_val, gt_raw=gt_val,
                                ai_clean=ai_c, gt_clean=gt_c, match=MATCH_NO,
                                note="one side NR")
    match = MATCH_YES if ai_n == gt_n else MATCH_NO
    diff  = abs(ai_n - gt_n)
    return ComparisonResult(ai_raw=ai_val, gt_raw=gt_val,
                            ai_clean=ai_c, gt_clean=gt_c,
                            match=match, score=float(diff))


def compare_numeric_tolerance(ai_val, gt_val, tolerance: float = 1.0) -> ComparisonResult:
    ai_f = to_float(ai_val)
    gt_f = to_float(gt_val)

    if ai_f is None and gt_f is None:
        return _make_na(ai_val, gt_val)
    if ai_f is None or gt_f is None:
        ai_c = str(ai_f) if ai_f is not None else "NR"
        gt_c = str(gt_f) if gt_f is not None else "NR"
        return ComparisonResult(ai_raw=ai_val, gt_raw=gt_val,
                                ai_clean=ai_c, gt_clean=gt_c, match=MATCH_NO,
                                note="one side NR")

    diff  = abs(ai_f - gt_f)
    match = MATCH_YES if diff <= tolerance else MATCH_NO
    return ComparisonResult(ai_raw=ai_val, gt_raw=gt_val,
                            ai_clean=str(ai_f), gt_clean=str(gt_f),
                            match=match, score=diff)


def compare_duration(ai_val, gt_val, tolerance_days: float = 15.0) -> ComparisonResult:
    """Unit-aware duration compare. '3 months' == '3 mths', '16 weeks' ≈ '4 months'.
    Falls back to normalized string equality when a side isn't a parseable duration."""
    if is_nr(ai_val) and is_nr(gt_val):
        return _make_na(ai_val, gt_val)
    ai_d = parse_duration_to_days(ai_val)
    gt_d = parse_duration_to_days(gt_val)
    if ai_d is None or gt_d is None:
        # One side isn't a recognizable duration → fall back to string compare
        ai_c = normalize_string(ai_val)
        gt_c = normalize_string(gt_val)
        match = MATCH_YES if ai_c == gt_c else MATCH_NO
        return ComparisonResult(ai_raw=ai_val, gt_raw=gt_val, ai_clean=ai_c, gt_clean=gt_c,
                                match=match, note="unparseable duration — string compare")
    diff  = abs(ai_d - gt_d)
    match = MATCH_YES if diff <= tolerance_days else MATCH_NO
    return ComparisonResult(ai_raw=ai_val, gt_raw=gt_val,
                            ai_clean=f"{ai_d:g}d", gt_clean=f"{gt_d:g}d",
                            match=match, score=diff)


def compare_date_range(ai_val, gt_val) -> ComparisonResult:
    ai_start, ai_end = parse_date_range(ai_val)
    gt_start, gt_end = parse_date_range(gt_val)

    if ai_start is None and gt_start is None:
        return _make_na(ai_val, gt_val)
    if ai_start is None or gt_start is None:
        return ComparisonResult(ai_raw=ai_val, gt_raw=gt_val,
                                ai_clean=str((ai_start, ai_end)),
                                gt_clean=str((gt_start, gt_end)),
                                match=MATCH_NO, note="one side NR")

    ai_c = f"{ai_start}–{ai_end}"
    gt_c = f"{gt_start}–{gt_end}"
    match = MATCH_YES if (ai_start == gt_start and ai_end == gt_end) else MATCH_NO
    note  = ""
    if match == MATCH_NO:
        if ai_start == gt_start or ai_end == gt_end:
            note = "partial year overlap"
    return ComparisonResult(ai_raw=ai_val, gt_raw=gt_val,
                            ai_clean=ai_c, gt_clean=gt_c, match=match, note=note)


def compare_set_terms(ai_val, gt_val, vocab: dict) -> ComparisonResult:
    import re as _re
    if is_nr(ai_val) and is_nr(gt_val):
        return _make_na(ai_val, gt_val)
    # AI returned a packed list placeholder — source items unavailable; skip rather than score wrong
    if _re.match(r"^\[\d+ items?\]$", str(ai_val).strip(), _re.IGNORECASE):
        return ComparisonResult(ai_raw=ai_val, gt_raw=gt_val,
                                ai_clean=str(ai_val), gt_clean=str(gt_val),
                                match=MATCH_SKIP, note="AI value is packed [N items] — original list unavailable")

    ai_terms = extract_terms(ai_val, vocab)
    gt_terms = extract_terms(gt_val, vocab)

    if not ai_terms and not gt_terms:
        return _make_na(ai_val, gt_val)

    j = jaccard(ai_terms, gt_terms)
    match = MATCH_YES if j >= 0.5 else MATCH_NO
    missing = gt_terms - ai_terms
    extra   = ai_terms - gt_terms
    notes = []
    if missing:
        notes.append(f"AI missed: {missing}")
    if extra:
        notes.append(f"AI added: {extra}")
    return ComparisonResult(ai_raw=ai_val, gt_raw=gt_val,
                            ai_clean=str(sorted(ai_terms)),
                            gt_clean=str(sorted(gt_terms)),
                            match=match, score=round(j, 3),
                            note="; ".join(notes))


def compare_parse_counts(ai_val, gt_val) -> ComparisonResult:
    if is_nr(ai_val) and is_nr(gt_val):
        return _make_na(ai_val, gt_val)

    ai_counts = extract_counts(ai_val)
    gt_counts = extract_counts(gt_val)

    if not ai_counts and not gt_counts:
        return _make_na(ai_val, gt_val)

    ai_keys = set(ai_counts.keys())
    gt_keys = set(gt_counts.keys())
    j = jaccard(ai_keys, gt_keys)

    count_match = ai_counts == gt_counts
    match = MATCH_YES if count_match else MATCH_NO
    return ComparisonResult(ai_raw=ai_val, gt_raw=gt_val,
                            ai_clean=str(ai_counts), gt_clean=str(gt_counts),
                            match=match, score=round(j, 3))


def compare_parse_percent(ai_val, gt_val, tolerance: float = 1.0) -> ComparisonResult:
    ai_p = extract_percent(ai_val)
    gt_p = extract_percent(gt_val)

    if ai_p is None and gt_p is None:
        return _make_na(ai_val, gt_val)
    if ai_p is None or gt_p is None:
        return ComparisonResult(ai_raw=ai_val, gt_raw=gt_val,
                                ai_clean=str(ai_p), gt_clean=str(gt_p),
                                match=MATCH_NO, note="one side NR or no % found")

    diff  = abs(ai_p - gt_p)
    match = MATCH_YES if diff <= tolerance else MATCH_NO
    return ComparisonResult(ai_raw=ai_val, gt_raw=gt_val,
                            ai_clean=f"{ai_p}%", gt_clean=f"{gt_p}%",
                            match=match, score=diff)


def compare_skip(ai_val, gt_val) -> ComparisonResult:
    return _make_skip(ai_val, gt_val)


# ── Dispatch ───────────────────────────────────────────────────────────────────

def compare_field(
    ai_val,
    gt_val,
    strategy: str,
    tolerance: float = 0.0,
    vocab: dict = None,
    llm_judge_fn=None,
    field_name: str = "",
) -> ComparisonResult:
    """Dispatch to the right strategy."""
    if strategy in (STRATEGY_SKIP, STRATEGY_EXCLUDE):
        return compare_skip(ai_val, gt_val)
    # Strip AI-side "Unclear:" hedging prefix before any strategy runs, so
    # llm_judge and exact_normalize and set_compare_terms all see the content
    # the model meant to extract.
    ai_val = _strip_unclear_prefix(ai_val)
    # GT=NR but AI has a value → not scored as a match. For free-text (llm_judge)
    # fields a GT blank is usually an annotation gap, so don't penalize it as an
    # over-extraction FP; for structured fields it stays a real over-extraction.
    if is_nr(gt_val) and not is_nr(ai_val):
        return ComparisonResult(ai_raw=ai_val, gt_raw=gt_val,
                                ai_clean=str(ai_val), gt_clean="NR",
                                match=MATCH_NA, note="GT is NR — excluded",
                                gt_nr_is_fp=(strategy != STRATEGY_LLM_JUDGE))
    if strategy == STRATEGY_EXACT_NORMALIZE:
        return compare_exact_normalize(ai_val, gt_val)
    if strategy == STRATEGY_NUMERIC_EXACT:
        return compare_numeric_exact(ai_val, gt_val)
    if strategy == STRATEGY_NUMERIC_TOLERANCE:
        return compare_numeric_tolerance(ai_val, gt_val, tolerance=tolerance or 1.0)
    if strategy == STRATEGY_DURATION:
        return compare_duration(ai_val, gt_val, tolerance_days=tolerance or 15.0)
    if strategy == STRATEGY_DATE_RANGE:
        return compare_date_range(ai_val, gt_val)
    if strategy == STRATEGY_SET_TERMS:
        vocab = vocab or {}
        return compare_set_terms(ai_val, gt_val, vocab)
    if strategy == STRATEGY_PARSE_COUNTS:
        return compare_parse_counts(ai_val, gt_val)
    if strategy == STRATEGY_PARSE_PERCENT:
        return compare_parse_percent(ai_val, gt_val, tolerance=tolerance or 1.0)
    if strategy == STRATEGY_LLM_JUDGE:
        if llm_judge_fn is not None:
            return llm_judge_fn(ai_val, gt_val, field_name)
        # Fallback if no LLM available: treat as skip with a note
        return ComparisonResult(ai_raw=ai_val, gt_raw=gt_val,
                                ai_clean=str(ai_val), gt_clean=str(gt_val),
                                match=MATCH_SKIP, note="LLM judge not configured")
    raise ValueError(f"Unknown strategy: {strategy}")
