"""Tokens that normalize to "NR" (not reported / missing)."""

NR_TOKENS = {
    "nr", "n/a", "na", "not reported", "not applicable",
    "not available", "unclear", "unknown", "none", "",
    "nan", "~", "-", "–", "—",
    # Ibuprofen GT's funding_source column uses this compound token as its
    # documented "not reported" marker (see gt sheets/ibuprofen.xlsx:definitions).
    "none/nr",
}


def is_nr(value) -> bool:
    import pandas as pd
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return True
    s = str(value).strip()
    if s.lower() in NR_TOKENS:
        return True
    # Excel multi-line cells: annotator wrote "NR" then Alt+Enter'd a note.
    # Newline-split case AND single-line "NR. <prose>" case (Tatapudi 2025
    # blinding fields) — first sentence/line is the actual field value.
    import re as _re
    first_chunk = _re.split(r'[\n.]', s, maxsplit=1)[0].strip().rstrip('.')
    return first_chunk.lower() in NR_TOKENS


def collapse_nr(value) -> str:
    return "NR" if is_nr(value) else str(value).strip()
