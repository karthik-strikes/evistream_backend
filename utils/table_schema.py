"""Accessors for a table field's composite key and its extraction pipeline.

Why this module exists
----------------------
A table field's identifying columns are a **composite key**: the set of columns
whose values uniquely identify one row. The stored JSON key for them has
historically been `anchor_columns` — a metaphor for something with a precise
name. We are moving to `key_columns`.

Renaming a stored key that 61 call sites read is how you get a silent outage, so
the rename runs through this module and nowhere else:

* `field_key_columns()` reads the new key and falls back to the old one, so a
  form migrated or not migrated behaves identically.
* `set_field_key_columns()` **dual-writes** both keys. Any read site that was
  missed keeps working. The old key is dropped only once logs prove nothing
  reads it.
* `resolve_strategy()` maps old and new `extraction_strategy` spellings onto one
  canonical value. This one is load-bearing: `build_schema_classes` selects the
  pipeline by exact string match and **falls through to single-pass silently** on
  an unrecognised value — no exception, no log line — so a half-finished rename
  would quietly drop 45 live forms out of the verified pipeline. Every
  comparison must go through here rather than testing a literal.

Terminology used in the new names, from the two vocabularies that already have
words for this:

    composite key / non-key attribute      (relational model)
    record discovery / slot filling        (information extraction)
    set-at-a-time vs row-at-a-time         (query processing)
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

# ── Composite key ──────────────────────────────────────────────────────────
# New name first: whichever is present wins in this order.
KEY_COLUMN_FIELDS = ("key_columns", "anchor_columns")

# ── Extraction pipeline ────────────────────────────────────────────────────
# Canonical names, and every spelling that must keep resolving to them. Old
# values stay valid forever: 48 live table fields carry them, and an
# unrecognised value degrades silently rather than loudly.
SINGLE_PASS = "single_pass"           # one prompt for the whole table
DISCOVER_THEN_FILL = "discover_then_fill"   # record discovery, then slot filling
AGENTIC = "agentic"                   # agent loop (name was already accurate)

STRATEGY_ALIASES: Dict[str, str] = {
    SINGLE_PASS: SINGLE_PASS,
    "single_call": SINGLE_PASS,
    DISCOVER_THEN_FILL: DISCOVER_THEN_FILL,
    "row_then_columns": DISCOVER_THEN_FILL,
    AGENTIC: AGENTIC,
}

CANONICAL_STRATEGIES = frozenset({SINGLE_PASS, DISCOVER_THEN_FILL, AGENTIC})


def field_key_columns(field_def: Optional[Dict[str, Any]]) -> List[str]:
    """The composite key of a table field: the columns that identify one row.

    Reads `key_columns`, falls back to `anchor_columns`. Blank entries are
    dropped — a key column with an empty name cannot identify anything, and
    letting one through produced row keys with an empty component.
    """
    if not isinstance(field_def, dict):
        return []
    for name in KEY_COLUMN_FIELDS:
        raw = field_def.get(name)
        if isinstance(raw, list):
            cols = [str(c).strip() for c in raw if c is not None and str(c).strip()]
            if cols:
                return cols
    return []


def set_field_key_columns(field_def: Dict[str, Any], columns: List[str]) -> None:
    """Write the composite key to BOTH keys, new and old.

    Dual-write is deliberate. It makes a read site we failed to migrate harmless
    instead of silent: it sees the old key and behaves exactly as before.
    """
    cols = [str(c).strip() for c in (columns or []) if c is not None and str(c).strip()]
    field_def["key_columns"] = list(cols)
    field_def["anchor_columns"] = list(cols)


def is_key_column(field_def: Optional[Dict[str, Any]], column_name: str) -> bool:
    """Is this column part of the field's composite key?"""
    return column_name in set(field_key_columns(field_def))


def attribute_columns(field_def: Optional[Dict[str, Any]]) -> List[str]:
    """The non-key attributes: every column the composite key determines.

    These are the values a slot-filling call populates for an already-identified
    record.
    """
    if not isinstance(field_def, dict):
        return []
    key = set(field_key_columns(field_def))
    out: List[str] = []
    for col in (field_def.get("subform_fields") or []):
        name = (col or {}).get("field_name") if isinstance(col, dict) else None
        if name and name not in key:
            out.append(name)
    return out


# ── Hand-typed row tuples in authored prose ────────────────────────────────
# Before the composite key existed, the only way to say "what makes a row" was
# to type it into the field description: "One row per (a x b x c)". Authors
# still do, and the two drift — CD015432's prose said
# comparison x outcome x timepoint while the key also held arm1_label and
# arm2_label, and Corticosteroids' prose named three concepts against a
# fifteen-column key.
#
# The drift is not cosmetic. On several live forms the stale tuple sits under
# **Rules**, which the field editor presents as "hard constraints the AI must
# follow", so the model reads two absolute and contradicting instructions.
#
# The key is the one that runs, and `_compose_field_desc` already renders it as
# the authoritative block. So the typed tuple is redundant at best and wrong at
# worst: strip it at compose time. Deliberately at COMPOSE time, not by editing
# stored rows — the author's words stay exactly as typed in the editor, every
# existing form is fixed with no migration and no regeneration, and there is one
# source of truth by construction rather than by convention.
#
# `x` and `*` are matched alongside `×` because authors type all three.
_ROW_TUPLE_RE = re.compile(
    r"[^.!?\n]*\bone row per\b[^.!?\n]*",
    re.IGNORECASE,
)
# The tuple BODY — everything after the lead-in, stopping at a closing paren so
# "One row per (a x b x c). Then..." yields "a x b x c" and not the sentence.
_TUPLE_BODY_RE = re.compile(
    r"\bone row per\b\s*\(?\s*(?:each\s+|every\s+|unique\s+)?([^.!?\n)]+)",
    re.IGNORECASE,
)
_TUPLE_SPLIT_RE = re.compile(r"\s*(?:×|\bx\b|\*)\s*", re.IGNORECASE)
# Trailing nouns authors append to the last element ("... x timepoint combination").
_TUPLE_TAIL_RE = re.compile(
    r"\s+(?:combination|combinations|pair|pairs|reported.*|in the paper.*)$",
    re.IGNORECASE,
)


def _clean_tuple_part(part: str) -> str:
    return _TUPLE_TAIL_RE.sub("", part).strip(" ()").strip()


def find_row_tuple(text: Optional[str]) -> Optional[List[str]]:
    """The multi-part row tuple a sentence declares, else None.

    Returns None for a single-concept phrase such as "create one row per
    timepoint" — that is a clarifying nuance, not a competing definition of row
    identity, and stripping it would lose author intent.
    """
    if not isinstance(text, str) or not text.strip():
        return None
    for sentence in _ROW_TUPLE_RE.findall(text):
        m = _TUPLE_BODY_RE.search(sentence)
        if not m:
            continue
        parts = [
            cleaned
            for p in _TUPLE_SPLIT_RE.split(m.group(1))
            if (cleaned := _clean_tuple_part(p))
        ]
        if len(parts) >= 2:
            return parts
    return None


def strip_row_tuple(text: Optional[str]) -> str:
    """Remove only the sentence(s) declaring a multi-part row tuple.

    Everything else in the prose is preserved, including single-concept
    clarifications like "For an outcome reported at multiple timepoints, create
    one row per timepoint."
    """
    if not isinstance(text, str) or not text.strip():
        return text or ""

    out = text
    for sentence in _ROW_TUPLE_RE.findall(text):
        if not find_row_tuple(sentence):
            continue
        # Take the trailing punctuation with the sentence so we don't leave
        # a stray ". " behind.
        idx = out.find(sentence)
        if idx == -1:
            continue
        end = idx + len(sentence)
        while end < len(out) and out[end] in ".!?":
            end += 1
        out = out[:idx] + out[end:]

    # Collapse the whitespace the removal left behind.
    out = re.sub(r"[ \t]{2,}", " ", out)
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out.strip()


def resolve_strategy(value: Any) -> Optional[str]:
    """Canonical pipeline name for any accepted spelling, else None.

    None means "not a pipeline we know about" — callers must treat that as an
    error worth logging, never as "use the default". Defaulting on an
    unrecognised value is precisely the silent downgrade this module exists to
    prevent.
    """
    if not isinstance(value, str):
        return None
    return STRATEGY_ALIASES.get(value.strip())


def field_strategy(field_def: Optional[Dict[str, Any]]) -> Optional[str]:
    """Canonical pipeline for a table field, or None if unset/unrecognised."""
    if not isinstance(field_def, dict):
        return None
    return resolve_strategy(field_def.get("extraction_strategy"))


__all__ = [
    "KEY_COLUMN_FIELDS",
    "SINGLE_PASS",
    "DISCOVER_THEN_FILL",
    "AGENTIC",
    "STRATEGY_ALIASES",
    "CANONICAL_STRATEGIES",
    "field_key_columns",
    "set_field_key_columns",
    "is_key_column",
    "attribute_columns",
    "resolve_strategy",
    "field_strategy",
]
