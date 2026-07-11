"""
Load AI CSV/JSON files and the GT multi-header Excel into clean DataFrames.
"""

import json
import pandas as pd
from pathlib import Path
from typing import Optional


GT_EXCEL_PATH = "/home/ubuntu/evistream/eval/sheets/gt sheets/combined_oral_cancer_data.xlsx"


# ── AI loaders ─────────────────────────────────────────────────────────────────

def load_ai_csv(path: str) -> pd.DataFrame:
    df = pd.read_csv(path)
    df.columns = df.columns.str.strip()
    return df


def load_ai_json(path: str) -> pd.DataFrame:
    with open(path) as f:
        data = json.load(f)
    return pd.DataFrame(data)


def load_ai(path: str, fmt: str) -> pd.DataFrame:
    if fmt == "csv":
        return load_ai_csv(path)
    elif fmt == "json":
        return load_ai_json(path)
    raise ValueError(f"Unknown format: {fmt}")


# ── GT loader ──────────────────────────────────────────────────────────────────

def _flatten_multiheader(df_raw: pd.DataFrame) -> pd.DataFrame:
    """
    Flatten the 3-level header: use level-1 (field description) as column name.
    Append level-0 (section) prefix only when needed to avoid collisions.
    """
    new_cols = []
    for level0, level1 in df_raw.columns:
        name = str(level1).strip()
        new_cols.append(name)
    df_raw.columns = new_cols
    return df_raw


def _pick_col(df: pd.DataFrame, keyword: str) -> Optional[str]:
    """Return first column whose name contains keyword (case-insensitive)."""
    # Exact match first — prevents e.g. 'Reference standard' from matching
    # 'Site of biopsy (reference standard)' before the intended column
    if keyword in df.columns:
        return keyword
    kw = keyword.lower()
    for c in df.columns:
        if kw in str(c).lower():
            return c
    return None


def load_gt_section(
    section_keyword: str,
    excel_path: str = GT_EXCEL_PATH,
    extra_col_keywords: list[str] = None,
) -> pd.DataFrame:
    """
    Load the GT Excel and return the columns belonging to the given section.
    extra_col_keywords: additional column name substrings to include regardless of section.
    """
    df_raw = pd.read_excel(excel_path, header=[0, 1])

    # Extract author column (used for dedup key)
    author_col_tuple = [c for c in df_raw.columns if "Authors" in str(c[1])][0]
    refid_col_tuple  = [c for c in df_raw.columns if "Refid"   in str(c[1])][0]

    if section_keyword == "ALL":
        section_cols = list(df_raw.columns)
    else:
        section_cols = [
            c for c in df_raw.columns
            if section_keyword.lower() in str(c[0]).lower()
        ]
        # Always include author + refid
        for must_have in (author_col_tuple, refid_col_tuple):
            if must_have not in section_cols:
                section_cols.insert(0, must_have)

        # Extra columns from other sections (e.g. index_test_sub_form_k lives in Patient Pop)
        if extra_col_keywords:
            for kw in extra_col_keywords:
                for c in df_raw.columns:
                    if kw.lower() in str(c[1]).lower() and c not in section_cols:
                        section_cols.append(c)

    df_section = df_raw[section_cols].copy()
    df_section = _flatten_multiheader(df_section)

    # Rename author column to a stable name
    author_col = _pick_col(df_section, "Authors")
    if author_col:
        df_section = df_section.rename(columns={author_col: "_gt_author"})

    # Drop rows where author is missing
    df_section = df_section[df_section["_gt_author"].notna()].copy()
    df_section["_gt_author"] = df_section["_gt_author"].astype(str).str.strip()

    return df_section


def _apply_exclude(df: pd.DataFrame, exclude_papers: list[str] = None) -> pd.DataFrame:
    """Drop rows whose _gt_author matches any entry in exclude_papers."""
    if not exclude_papers:
        return df
    excl = {str(p).strip().lower() for p in exclude_papers}
    mask = df["_gt_author"].str.strip().str.lower().isin(excl)
    n_dropped = mask.sum()
    if n_dropped:
        print(f"  [loader] Excluded {n_dropped} papers flagged as PROMPT_DEV_PAPERS: "
              f"{df.loc[mask, '_gt_author'].tolist()}")
    return df[~mask].reset_index(drop=True)


def load_gt_deduped(section_keyword: str, excel_path: str = GT_EXCEL_PATH,
                    extra_col_keywords: list[str] = None,
                    exclude_papers: list[str] = None) -> pd.DataFrame:
    """One row per unique study (drops duplicate rows for multi-test studies)."""
    df = load_gt_section(section_keyword, excel_path, extra_col_keywords)
    df = df.drop_duplicates(subset=["_gt_author"]).reset_index(drop=True)
    return _apply_exclude(df, exclude_papers)


def load_gt_all_rows(section_keyword: str, excel_path: str = GT_EXCEL_PATH,
                     extra_col_keywords: list[str] = None,
                     exclude_papers: list[str] = None) -> pd.DataFrame:
    """All rows (for Index Test — multiple rows per study)."""
    df = load_gt_section(section_keyword, excel_path, extra_col_keywords)
    return _apply_exclude(df, exclude_papers)


def load_gt_aligned(aligned_path: str, sheet_name: str,
                    exclude_papers: list[str] = None,
                    key_col: str = "Paper",
                    skiprows=None) -> pd.DataFrame:
    """
    Load a pre-built aligned GT sheet (ai_col names as headers).

    key_col: name of the study-id column to rename to "_gt_author"
             (oral cancer: "Paper"; antibiotic: "study_id").
    skiprows: rows to drop after the header — e.g. [1] skips the antibiotic
             GT directive row (EXTRACT/NEGLECT/OPTIONAL/META).
    """
    df = pd.read_excel(aligned_path, sheet_name=sheet_name, skiprows=skiprows)
    df.columns = df.columns.str.strip()
    df = df.rename(columns={key_col: "_gt_author"})
    df["_gt_author"] = df["_gt_author"].astype(str).str.strip()
    return _apply_exclude(df, exclude_papers)
