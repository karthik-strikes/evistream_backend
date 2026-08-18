"""Canonical absence semantics for extraction cells.

Evidence synthesis distinguishes four claims that this codebase historically
collapsed into the single literal string "NR":

    reported        the value — including "None" / 0, which are findings
    not_reported    the paper is silent (a finding *about* the paper)
    not_applicable  the field cannot apply to this study/arm
    missing | error the pipeline faulted; no claim about the paper at all

Two invariants make the distinction survive:

1. A cell's ``value`` is never overwritten. Display and export derive their
   label from ``status`` via `display_label`.
2. **Declared options win.** A value matching one of the field's declared
   options is `REPORTED`, checked *before* any token matching — the form
   author's vocabulary outranks the system sentinel. This is what stops a
   RoB 2 "Not applicable" answer or a ``funding: "None"`` finding from being
   recorded as a reporting gap.

No token is ever removed from the recognizer: `ABSENCE_TOKENS` is a superset
of every token set this module replaces, so a bare absence token on a field
that declares no options keeps its old meaning. The narrowing comes only from
invariant 2.

Imports nothing from the repo, so it is safe to import from both ``app.*`` and
``dspy_components.*``.
"""

from typing import Any, Dict, List, Optional

# ── Status vocabulary ───────────────────────────────────────────────────────
REPORTED = "reported"
NOT_REPORTED = "not_reported"
NOT_APPLICABLE = "not_applicable"
MISSING = "missing"
ERROR = "error"
# Agentic table sessions resolve some cells and give up on others.
PARTIAL = "partial"

STATUSES = frozenset(
    {REPORTED, NOT_REPORTED, NOT_APPLICABLE, MISSING, ERROR, PARTIAL}
)

#: Statuses meaning "the pipeline failed here" — never an answer about the paper.
FAILURE_STATUSES = frozenset({MISSING, ERROR})
#: Statuses that *are* an answer, asserting the value is absent.
ABSENCE_STATUSES = frozenset({NOT_REPORTED, NOT_APPLICABLE})

#: The agentic path emitted "extracted" where the DSPy path emits "reported".
#: Kept as an alias so stored rows and in-flight agentic output both normalize.
LEGACY_STATUS_ALIASES = {"extracted": REPORTED}

# ── Fixed canonical labels ─────────────────────────────────────────────────
# The form spec controls the *meaning* (via declared options), not the glyph.
NR_LABEL = "NR"
NA_LABEL = "NA"
FAILED_LABEL = "⚠"  # ⚠ — a failure must never render blank or as NR

# ── Token sets ─────────────────────────────────────────────────────────────
# "N/A" stays on the not-reported side: it is genuinely ambiguous between
# "not applicable" and "not available", and moving it would shift counts for
# no methodological gain.
_NA_TOKENS = frozenset(
    {"NA", "N.A.", "NOT APPLICABLE", "NOT_APPLICABLE", "NOT-APPLICABLE"}
)
_NR_TOKENS = frozenset(
    {"", "NR", "N/R", "N/A", "NONE", "NOT REPORTED", "NOT_REPORTED"}
)

#: Union of both — a superset of the token lists this module replaces.
ABSENCE_TOKENS = _NA_TOKENS | _NR_TOKENS

#: Additionally the dash glyphs that only ever appear as display placeholders.
#: Used by the "is this cell empty" callers, which historically included them.
EMPTY_DISPLAY_TOKENS = ABSENCE_TOKENS | {"—", "–", "-"}


def normalize_status(status: Any) -> Optional[str]:
    """Map a stored status onto the canonical vocabulary.

    Returns None when the status is absent or unrecognized, so callers can
    fall back to inferring from the value (legacy rows carry no status).
    """
    if not isinstance(status, str):
        return None
    s = status.strip().lower()
    s = LEGACY_STATUS_ALIASES.get(s, s)
    return s if s in STATUSES else None


def matches_option(value: Any, options: Any) -> bool:
    """True when `value` is one of the declared options (case-insensitively).

    A multi-select list matches when *every* entry is a declared option.
    """
    if not options:
        return False
    canon = {str(o).strip().casefold() for o in options}
    vals = list(value) if isinstance(value, (list, tuple)) else [value]
    if not vals:
        return False
    return all(
        isinstance(v, str) and v.strip().casefold() in canon for v in vals
    )


def classify(value: Any, options: Any = None) -> str:
    """Classify a cell's value into REPORTED / NOT_REPORTED / NOT_APPLICABLE.

    Declared options are consulted first (invariant 2). Numbers and booleans
    are always findings — 0 and False mean something.
    """
    if matches_option(value, options):
        # The author's vocabulary is honoured — but a declared inapplicability
        # option still carries the NA *meaning*, so coverage and adjudication
        # can act on it. A declared "None" stays REPORTED: it is a finding.
        if isinstance(value, str) and value.strip().upper() in _NA_TOKENS:
            return NOT_APPLICABLE
        return REPORTED
    if value is None:
        return NOT_REPORTED
    if isinstance(value, str):
        token = value.strip().upper()
        if token in _NA_TOKENS:
            return NOT_APPLICABLE
        if token in _NR_TOKENS:
            return NOT_REPORTED
        return REPORTED
    if isinstance(value, (list, tuple, dict)):
        # An empty table/multi-select is the paper reporting no such data —
        # the same claim the two-stage path already makes explicitly when
        # Stage 1 finds no rows.
        return REPORTED if len(value) else NOT_REPORTED
    return REPORTED


def is_absent(value: Any, options: Any = None) -> bool:
    """True when the value asserts absence (either flavour).

    Drop-in replacement for the old ``_is_not_reported``.
    """
    return classify(value, options) in ABSENCE_STATUSES


def has_grounding(cell: Any) -> bool:
    """True when the cell carries a real verbatim quote (not a placeholder)."""
    if not isinstance(cell, dict):
        return False
    st = cell.get("source_text")
    if not isinstance(st, str):
        return False
    text = st.strip()
    return bool(text) and text.upper() not in ABSENCE_TOKENS


def stamp(
    cell: Dict[str, Any],
    options: Any = None,
    *,
    status: Optional[str] = None,
) -> Dict[str, Any]:
    """Write the canonical `status` onto an envelope. The single writer of it.

    Pass `status` to record a verdict the pipeline already knows (missing /
    error / partial). Otherwise the status is derived from the value, with
    declared options taking precedence.

    Callers that canonicalize a select value against its options must do so
    *before* calling this, so the canonical form is what gets classified.
    """
    out = dict(cell)
    if status is not None:
        out["status"] = status
        return out

    existing = normalize_status(out.get("status"))
    if existing in FAILURE_STATUSES or existing == PARTIAL:
        # A pipeline verdict outranks value inference: a failed cell must not
        # be re-read as a genuine absence just because its value looks like one.
        out["status"] = existing
        return out

    value = out.get("value")
    derived = classify(value, options)
    if (
        derived == NOT_APPLICABLE
        and not matches_option(value, options)
        and not has_grounding(out)
    ):
        # Backstop against NA inflation. Claiming a field cannot apply is a claim
        # about the study design, so an NA the model invented has to quote that
        # design; without a quote it is downgraded to the always-safe claim.
        # An NA the author *declared* as an option is trusted as-is — it is part
        # of their schema (RoB 2 routes signalling questions to exactly this).
        derived = NOT_REPORTED
    out["status"] = derived
    return out


def failure_envelope(status: str, error: Optional[str] = None) -> Dict[str, Any]:
    """The canonical shape for a cell the pipeline could not extract.

    Deliberately carries an empty `value` rather than "NR": a downstream
    flatten or unwrap must not be able to resurrect a failure as an assertion
    that the paper is silent.
    """
    env: Dict[str, Any] = {"value": "", "source_text": "", "status": status}
    if error:
        env["error"] = error
    return env


def display_label(cell: Any) -> Optional[str]:
    """The label a results surface should show instead of the raw value.

    Returns None when the cell's own value should be shown.
    """
    if not isinstance(cell, dict):
        return None
    st = normalize_status(cell.get("status"))
    if st in FAILURE_STATUSES:
        return FAILED_LABEL
    if st == NOT_REPORTED:
        return NR_LABEL
    if st == NOT_APPLICABLE:
        return NA_LABEL
    return None


#: Unambiguous absence spellings, safe to collapse in a comparison. Excludes
#: "NONE" and "" on purpose: a bare "None" may be a substantive answer (a study
#: with no funding), so folding it into NR would erase a finding.
_CANON_NA = _NA_TOKENS
_CANON_NR = frozenset({"NR", "N/R", "N/A", "NOT REPORTED", "NOT_REPORTED"})


def canonical_absence_label(value: Any) -> Optional[str]:
    """NR / NA label for an unambiguous bare absence token, else None.

    Used where only a scalar survives (legacy status-less rows), so that "NR"
    and "not reported" agree while "None" keeps its own identity.
    """
    if not isinstance(value, str):
        return None
    token = value.strip().upper()
    if token in _CANON_NA:
        return NA_LABEL
    if token in _CANON_NR:
        return NR_LABEL
    return None


def export_cell(cell: Dict[str, Any]) -> Dict[str, Any]:
    """Render one value cell for export or download.

    The label comes from `status`, never from the raw text, and a failure is
    never blanked — an empty column reads as "the paper does not report this",
    which is a different claim from "we failed to read it".
    """
    label = display_label(cell)
    out = {k: v for k, v in cell.items()
           if k not in ("source_location", "status", "error")}
    if label is not None:
        out["value"] = label
    elif isinstance(out.get("value"), list):
        out["value"] = export_rows(out["value"])
    return out


def export_rows(rows: List[Any]) -> List[Any]:
    """Apply `export_cell` to every cell of every row of a table field."""
    rendered: List[Any] = []
    for row in rows:
        if isinstance(row, dict):
            rendered.append({
                k: export_cell(c) if isinstance(c, dict) and "value" in c else c
                for k, c in row.items()
            })
        else:
            rendered.append(row)
    return rendered


def field_is_empty(v: Any) -> bool:
    """Is this field a reporting gap, for coverage and flagging purposes?

    Mirrors `fieldIsEmpty` in frontend/lib/absence.ts — several screens compare
    their counts against backend-computed ones, so the two must agree.
    """
    if v is None:
        return True
    if isinstance(v, str):
        return v.strip() == "" or v.strip().upper() in EMPTY_DISPLAY_TOKENS
    if isinstance(v, bool) or isinstance(v, (int, float)):
        return False
    if isinstance(v, list):
        return len(v) == 0 or all(field_is_empty(item) for item in v)
    if isinstance(v, dict):
        st = normalize_status(v.get("status"))
        # Exhaustive on purpose: a status that falls through to the value below
        # gets classified by its raw text, which silently mislabels new states.
        if st is not None:
            if st in FAILURE_STATUSES or st == NOT_REPORTED:
                return True
            if st in (REPORTED, PARTIAL, NOT_APPLICABLE):
                return False
        if "value" in v:
            return field_is_empty(v.get("value"))
        return len(v) == 0 or all(field_is_empty(val) for val in v.values())
    return False


def field_is_not_applicable(v: Any) -> bool:
    """A field the study design excludes. Not a reporting gap, so it comes out
    of the coverage denominator rather than counting against the paper — a RoB 2
    form routes many of its signalling questions here by design.
    """
    return (
        isinstance(v, dict)
        and normalize_status(v.get("status")) == NOT_APPLICABLE
    )


def cell_status(cell: Any, options: Any = None) -> str:
    """Effective status of a cell, inferring one for legacy status-less rows."""
    if isinstance(cell, dict):
        st = normalize_status(cell.get("status"))
        if st is not None:
            return st
        if "value" in cell:
            return classify(cell.get("value"), options)
        return REPORTED
    return classify(cell, options)


def compare_key(cell: Any, options: Any = None) -> Optional[str]:
    """Canonical token for reviewer-agreement comparison.

    Returns None for a failed cell, which callers must treat as incomparable —
    an extraction failure is not evidence that two reviewers agree.
    NR and NA collapse to their labels, so `NR` vs `NA` compares unequal
    (they are different claims about the study) while `NR` vs `NR` agrees.
    """
    value = cell
    if isinstance(cell, dict) and "value" in cell:
        value = cell.get("value")
    st = cell_status(cell, options)
    if st in FAILURE_STATUSES:
        return None
    if st == NOT_REPORTED:
        return NR_LABEL
    if st == NOT_APPLICABLE:
        return NA_LABEL
    return _value_key(value)


def _value_key(value: Any) -> str:
    """Order- and format-insensitive key for a substantive value."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        # "3" == 3 == 3.0, matching adjudication's existing leniency.
        as_float = float(value)
        return str(int(as_float)) if as_float.is_integer() else str(as_float)
    if isinstance(value, (list, tuple)):
        return "|".join(sorted(_value_key(v) for v in value))
    if isinstance(value, dict):
        return "|".join(
            f"{k}={_value_key(v)}" for k, v in sorted(value.items())
            if k not in ("source_text", "source_location", "status", "error")
        )
    text = str(value).strip()
    try:
        return _value_key(float(text))
    except ValueError:
        pass
    lowered = text.casefold()
    if lowered in ("true", "yes", "y"):
        return "true"
    if lowered in ("false", "no", "n"):
        return "false"
    return lowered


#: Spellings that already give the model a way to say "the paper is silent".
#: Deliberately excludes "NA" and "NONE": a column offering only those has no
#: not-reported token, and the model would reach for the author's substantive
#: option (e.g. funding "None") to express silence — the exact confusion this
#: module exists to prevent.
_EXPLICIT_NR_TOKENS = frozenset(
    {"NR", "N/R", "N/A", "NOT REPORTED", "NOT_REPORTED"}
)


def schema_extras(options: Any) -> List[str]:
    """Absence tokens to append to a declared-option enum.

    Every enum needs some way to say "not reported", so `NR` is appended unless
    the author already declared an explicit NR spelling (which would duplicate
    the concept). A declared "Not applicable" does *not* suppress it: NA and NR
    are different claims and a routed field needs both.
    """
    declared = {str(o).strip().upper() for o in (options or [])}
    if declared & _EXPLICIT_NR_TOKENS:
        return []
    return [NR_LABEL]


__all__ = [
    "REPORTED", "NOT_REPORTED", "NOT_APPLICABLE", "MISSING", "ERROR", "PARTIAL",
    "STATUSES", "FAILURE_STATUSES", "ABSENCE_STATUSES", "LEGACY_STATUS_ALIASES",
    "NR_LABEL", "NA_LABEL", "FAILED_LABEL",
    "ABSENCE_TOKENS", "EMPTY_DISPLAY_TOKENS",
    "normalize_status", "matches_option", "classify", "is_absent",
    "has_grounding", "stamp", "failure_envelope", "display_label",
    "cell_status", "compare_key", "schema_extras",
]
