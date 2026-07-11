"""
Stateless normalization functions.
All functions are pure — they take a value and return a cleaned value.
"""

import re
import pandas as pd
from typing import Optional

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from config.nr_synonyms import collapse_nr, is_nr
from config.gt_typo_fixes import apply_gt_fixes


# ── String normalization ───────────────────────────────────────────────────────

_UNCLEAR_PREFIX_RE = re.compile(r"^\s*unclear\b[^:]*:\s*", re.IGNORECASE)


def normalize_string(value) -> str:
    """Lowercase, strip whitespace, collapse NR synonyms, apply GT fixes."""
    collapsed = collapse_nr(value)
    if collapsed == "NR":
        return "NR"
    # Drop AI's "Unclear:" / "Unclear (no biopsy named):" hedging prefix —
    # the content that follows is what should be compared.
    stripped = _UNCLEAR_PREFIX_RE.sub("", collapsed)
    fixed = apply_gt_fixes(stripped)
    # Age-range syntax normalisation: "<20->80" → "20-80", "21->70" → "21-70",
    # "< 20-> 70" → "20-70". Only triggers when the string looks like a range,
    # so we don't mangle ">50 years" prose elsewhere.
    if re.search(r"<\s*\d", fixed) or re.search(r"\d\s*->\s*\d", fixed):
        fixed = re.sub(r"[<>]", "", fixed)
    return re.sub(r"\s+", " ", fixed).strip()


def strip_year_suffix(value: str) -> str:
    """Remove trailing a/b from year strings: '2006a' → '2006'."""
    return re.sub(r"^(\d{4})[ab]$", r"\1", value.strip(), flags=re.IGNORECASE)


# ── Numeric conversion ─────────────────────────────────────────────────────────

_LEADING_NUM_RE = re.compile(r"^\s*([+-]?\d+(?:\.\d+)?)\b")


def to_float(value) -> Optional[float]:
    """
    Convert to float; return None if NR or unparseable.
    Handles GT/AI cells like "190 with suspected OPMD", "32 (38 assessed; 6 excluded)",
    "54 (analyzed)", or multi-line cells where the first line is the number and
    subsequent lines are annotator commentary. Extracts the leading numeric token.
    """
    if is_nr(value):
        return None
    s = str(value).replace(",", "").replace("%", "").strip()
    # Direct parse first
    try:
        return float(s)
    except (ValueError, TypeError):
        pass
    # Multi-line: try first line only
    if "\n" in s:
        s = s.split("\n", 1)[0].strip()
        try:
            return float(s)
        except (ValueError, TypeError):
            pass
    # Leading-number extraction (handles "(parenthetical)" and "with prose" suffixes)
    m = _LEADING_NUM_RE.match(s)
    if m:
        try:
            return float(m.group(1))
        except (ValueError, TypeError):
            return None
    return None


def to_int(value) -> Optional[int]:
    """Convert to int; return None if NR or unparseable."""
    f = to_float(value)
    return int(round(f)) if f is not None else None


# ── Duration normalization ──────────────────────────────────────────────────────
# Canonicalize free-text durations to a day count so unit/abbreviation variants
# compare equal: "3 months" == "3 mths", and "16 weeks" ≈ "4 months".
_DURATION_UNIT_DAYS = {
    "day": 1, "days": 1, "d": 1,
    "week": 7, "weeks": 7, "wk": 7, "wks": 7, "w": 7,
    "month": 30, "months": 30, "mth": 30, "mths": 30, "mo": 30, "mos": 30, "mon": 30,
    "year": 365, "years": 365, "yr": 365, "yrs": 365, "y": 365,
}
_DURATION_RE = re.compile(r"(\d+(?:\.\d+)?)\s*([a-zA-Z]+)")


def parse_duration_to_days(value) -> Optional[float]:
    """
    Parse a free-text follow-up duration to an approximate day count
    (month ≈ 30d, year = 365d). Returns None if NR or no recognizable
    quantity+unit is present. Uses the first quantity+unit it finds.
    """
    if is_nr(value):
        return None
    s = str(value).strip().lower()
    for m in _DURATION_RE.finditer(s):
        unit = m.group(2).rstrip(".")
        days = _DURATION_UNIT_DAYS.get(unit)
        if days is not None:
            return float(m.group(1)) * days
    return None


# ── Date range parsing ─────────────────────────────────────────────────────────

_YEAR_RE = re.compile(r"\b((?:19|20)\d{2})\b")

def parse_date_range(value) -> tuple[Optional[int], Optional[int]]:
    """
    Extract (start_year, end_year) from a free-text date range.
    Returns (None, None) if NR or unparseable.
    Examples:
      "January 2014 to December 2019" → (2014, 2019)
      "2016 to 2018"                  → (2016, 2018)
      "August 2017 to October 2019"   → (2017, 2019)
    """
    if is_nr(value):
        return (None, None)
    years = [int(y) for y in _YEAR_RE.findall(str(value))]
    if not years:
        return (None, None)
    return (min(years), max(years))


# ── List / set operations ──────────────────────────────────────────────────────

def unpack_list_field(value) -> list[str]:
    """
    Handle patient_population_categories field.
    Values may be a plain string, a comma-separated list, or "[N items]".
    Returns list of stripped lowercase strings.
    """
    if is_nr(value):
        return []
    s = str(value).strip()
    # If it looks like "[N items]" we can't unpack without the source data
    if re.match(r"^\[\d+ items?\]$", s, re.IGNORECASE):
        return [f"__packed_{s}__"]   # sentinel — will be flagged in comparator
    return [item.strip().lower() for item in re.split(r"[;,\n]", s) if item.strip()]


def extract_terms(text, vocab: dict[str, set[str]]) -> set[str]:
    """
    Scan text for known vocabulary aliases.
    Returns set of canonical terms found.
    """
    if is_nr(text):
        return set()
    lower = str(text).lower()
    found = set()
    for canonical, aliases in vocab.items():
        for alias in aliases:
            if alias in lower:
                found.add(canonical)
                break
    return found


def jaccard(set_a: set, set_b: set) -> float:
    """Jaccard similarity between two sets."""
    if not set_a and not set_b:
        return 1.0
    union = set_a | set_b
    return len(set_a & set_b) / len(union) if union else 0.0


# ── Count / percent extraction ─────────────────────────────────────────────────

def extract_counts(text) -> dict[str, int]:
    """
    Extract grade/condition → count mapping from free text.
    E.g. "15 mild dysplasia, 25 moderate dysplasia, 7 severe dysplasia"
    → {"mild dysplasia": 15, "moderate dysplasia": 25, "severe dysplasia": 7}
    """
    if is_nr(text):
        return {}
    pattern = re.compile(r"(\d+)\s+([a-z ]+?)(?=[,;\n]|$)", re.IGNORECASE)
    result = {}
    for match in pattern.finditer(str(text)):
        count = int(match.group(1))
        label = match.group(2).strip().lower()
        result[label] = result.get(label, 0) + count
    return result


def extract_percent(text) -> Optional[float]:
    """
    Extract a single percentage from free text.
    E.g. "Any dysplasia: 67.5% (27/40)" → 67.5
    """
    if is_nr(text):
        return None
    pattern = re.compile(r"([\d.]+)\s*%")
    match = pattern.search(str(text))
    return float(match.group(1)) if match else None
