"""
Runtime DSPy class construction from JSON schema definitions.

Replaces per-form signatures.py + modules.py disk files.
DSPy Signature and Module classes are built via type() from schema_def JSONB
stored in the forms/schemas tables, eliminating importlib round-trips and
sys.modules staleness across Celery workers.

The pattern is proven by pilot_feedback.py:90 which already uses type() to
subclass Signature classes at runtime.
"""

import asyncio
import copy
import hashlib
import json
import logging
import os
import re
from functools import lru_cache
from typing import Any, Dict, List, Optional, Tuple, Type

import dspy

from utils.table_schema import (
    AGENTIC, DISCOVER_THEN_FILL, field_key_columns, field_strategy,
    find_row_tuple, resolve_strategy, strip_row_tuple,
)
from utils import absence
from utils.dspy_async import async_dspy_forward, was_truncated

logger = logging.getLogger(__name__)

# ── Two-stage debug logging (opt-in via DEBUG_TWO_STAGE=1) ───────────────────
_DEBUG_KEYED = os.getenv("DEBUG_TWO_STAGE", "").lower() in ("1", "true", "yes")
_keyed_debug_logger = None

def _get_keyed_logger():
    global _keyed_debug_logger
    if _keyed_debug_logger is None:
        import os as _os
        _lg = logging.getLogger("keyed_debug")
        _lg.setLevel(logging.DEBUG)
        _lg.propagate = False
        _log_path = _os.path.join(
            _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))),
            "..", "logs", "keyed_debug.log",
        )
        _os.makedirs(_os.path.dirname(_log_path), exist_ok=True)
        _h = logging.FileHandler(_log_path, mode="a")
        _h.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
        _lg.addHandler(_h)
        _keyed_debug_logger = _lg
    return _keyed_debug_logger
# ─────────────────────────────────────────────────────────────────────────────

# Absence semantics (NR vs NA vs pipeline failure) live in one place so the
# extraction path, the API layer and the UI cannot drift apart.
# See utils/absence.py; these aliases keep the local call sites readable.
_NR_TOKENS = absence.ABSENCE_TOKENS

# Fed to a dependent signature when an upstream stage produced nothing. It must
# not read as "the paper does not report this" — that would present a pipeline
# failure to the next model as evidence.
_UPSTREAM_UNAVAILABLE = "<unavailable: upstream extraction did not produce this field>"


def _is_not_reported(v) -> bool:
    """True when a model-returned value asserts absence (genuine NR or NA)."""
    return absence.is_absent(v)


# Allowlist of type strings to avoid eval().
# Add entries here as new field types are introduced.
_TYPE_MAP: Dict[str, Any] = {
    "str": str,
    "int": int,
    "float": float,
    "bool": bool,
    "Any": Any,
    "Dict[str, Any]": Dict[str, Any],
    "List[Dict[str, Any]]": List[Dict[str, Any]],
    "List[str]": List[str],
    "List[int]": List[int],
    "Optional[str]": Optional[str],
    "Optional[Dict[str, Any]]": Optional[Dict[str, Any]],
}

_UNKNOWN_TYPES_WARNED: set = set()


def _resolve_type(type_str: str, default: Any) -> Any:
    """Map a schema_def type string to a real Python type. Logs once per
    unknown string so silent fallback doesn't mask schema-spec drift."""
    if type_str in _TYPE_MAP:
        return _TYPE_MAP[type_str]
    if type_str not in _UNKNOWN_TYPES_WARNED:
        _UNKNOWN_TYPES_WARNED.add(type_str)
        logger.warning(
            "Unknown type string %r in schema_def — falling back to %s. "
            "Add it to runtime_builders._TYPE_MAP if it should be recognised.",
            type_str, default,
        )
    return default


# Section headers used by _compose_field_desc when rendering structured
# fields. We strip these from the LLM-baked description before re-rendering
# to prevent double-baking when structured arrays are present.
_STRIPPABLE_SECTIONS: Dict[str, List[str]] = {
    "options": ["options"],
    "hints": ["extraction hints", "hints"],
    "rules": ["rules"],
    "examples": ["examples"],
    "source_grounding": ["source grounding", "source-grounding"],
    # Legacy forms had column structure baked into prose; strip it when
    # subform_fields is now present as structured data.
    "subform_fields": ["table columns", "subfields", "columns", "structure"],
}

_ALL_SECTION_HEADERS: set = {
    h for headers in _STRIPPABLE_SECTIONS.values() for h in headers
}


def _strip_embedded_sections(text: str, fields_present: Dict[str, bool]) -> str:
    """Strip section blocks from `text` whose corresponding structured field
    is present (truthy) in `fields_present`. Prevents duplicate sections in
    the final desc when the LLM already baked them into description.

    A section spans from a `Header:` line until the next known section
    header, a blank line, or end-of-text.
    """
    if not text:
        return text

    headers_to_strip = set()
    for key, present in fields_present.items():
        if present:
            headers_to_strip.update(_STRIPPABLE_SECTIONS.get(key, []))

    if not headers_to_strip:
        return text

    lines = text.splitlines()
    out_lines: List[str] = []
    in_skip = False

    for line in lines:
        stripped = line.strip().lower()
        # A "Header:" line (no other content) marks a section boundary.
        if stripped.endswith(":") and " " not in stripped[:-1].split(":")[0]:
            # Some headers are multi-word like "extraction hints:"
            pass
        header_match = None
        if stripped.endswith(":"):
            header_match = stripped[:-1].strip()

        if header_match in headers_to_strip:
            in_skip = True
            continue
        if header_match in _ALL_SECTION_HEADERS:
            # Different known section — stop skipping.
            in_skip = False
            out_lines.append(line)
            continue
        if not stripped and in_skip:
            # Blank line ends a stripped section.
            in_skip = False
            out_lines.append(line)
            continue
        if not in_skip:
            out_lines.append(line)

    return "\n".join(out_lines).rstrip()


# Map normalized header text → canonical bucket key used by _parse_embedded_sections.
_HEADER_TO_KEY: Dict[str, str] = {
    h: key
    for key, headers in _STRIPPABLE_SECTIONS.items()
    for h in headers
}


def _parse_embedded_sections(text: str) -> Dict[str, Any]:
    """Extract Hints/Rules/Examples/Options blocks baked into a description string.

    Used by GET /forms/{id}/field-prompts when the LLM emitted everything into
    `description` and left the structured arrays empty (Phase A behavior). Returns:
        {
          "description": <text with sections removed>,
          "hints": [str, ...],
          "rules": [str, ...],
          "examples": [{"value": str, "source_text": str}, ...],
          "options": [str, ...],
        }

    A section spans from a `Header:` line until the next known header, a blank
    line, or end-of-text. Item lines beginning with "- " or "* " have the bullet
    stripped. Example items that parse as JSON dicts are returned as-is.
    """
    result: Dict[str, Any] = {
        "description": "",
        "hints": [],
        "rules": [],
        "examples": [],
        "options": [],
    }
    if not text:
        return result

    desc_lines: List[str] = []
    current_key: Optional[str] = None
    buckets: Dict[str, List[str]] = {"hints": [], "rules": [], "examples": [], "options": []}

    # "Description:" gets recognised as a section header and its content is
    # merged back into the description bucket — the LLM sometimes wraps the
    # main desc inside a literal `Description:` label.
    _DESC_HEADERS = {"description", "field description", "definition"}

    # Matches a single-line "Label: content..." prefix where the label is a
    # short alphabetic run (avoids false matches on commas / e.g. / etc.).
    inline_label_re = re.compile(r"^([A-Za-z][A-Za-z ]{0,30}):\s+(.+)$")

    for line in text.splitlines():
        stripped = line.strip()
        lowered = stripped.lower()
        header_match: Optional[str] = None
        if lowered.endswith(":"):
            header_match = lowered[:-1].strip()

        if header_match is not None and header_match in _DESC_HEADERS:
            # Standalone "Description:" header — subsequent lines route to desc.
            current_key = "_desc"
            continue

        if header_match is not None and header_match in _HEADER_TO_KEY:
            key = _HEADER_TO_KEY[header_match]
            current_key = key if key in buckets else None
            continue

        if header_match is not None and header_match in _ALL_SECTION_HEADERS:
            # Known section we don't extract (e.g. source_grounding) — end any active bucket.
            current_key = None
            continue

        if not stripped:
            current_key = None
            if not desc_lines or desc_lines[-1] != "":
                desc_lines.append("")
            continue

        # Inline "Label: content" form (label and content on same line).
        if header_match is None:
            inline_m = inline_label_re.match(stripped)
            if inline_m:
                label = inline_m.group(1).lower().strip()
                rest = inline_m.group(2).strip()
                if label in _DESC_HEADERS:
                    desc_lines.append(rest)
                    current_key = "_desc"
                    continue
                if label in _HEADER_TO_KEY:
                    key = _HEADER_TO_KEY[label]
                    if key in buckets and rest:
                        buckets[key].append(rest)
                    current_key = key if key in buckets else None
                    continue

        if current_key is None or current_key == "_desc":
            desc_lines.append(line)
            continue

        item = stripped
        if item.startswith(("- ", "* ")):
            item = item[2:].strip()
        if not item:
            continue
        buckets[current_key].append(item)

    examples_out: List[Dict[str, str]] = []
    for raw in buckets["examples"]:
        parsed: Optional[Dict[str, Any]] = None
        if raw.startswith("{") and raw.endswith("}"):
            try:
                obj = json.loads(raw)
                if isinstance(obj, dict):
                    parsed = obj
            except (ValueError, TypeError):
                parsed = None
        if parsed is not None:
            examples_out.append({
                "value": str(parsed.get("value", "")),
                "source_text": str(parsed.get("source_text", "")),
            })
        else:
            examples_out.append({"value": raw, "source_text": ""})

    result["description"] = _dedupe_paragraphs("\n".join(desc_lines).strip())
    result["hints"] = buckets["hints"]
    result["rules"] = buckets["rules"]
    result["examples"] = examples_out
    result["options"] = buckets["options"]
    return result


def _dedupe_paragraphs(text: str) -> str:
    """Collapse near-duplicate paragraphs the LLM sometimes emits (e.g. a short
    summary line followed by a `Description:` block that restates the same
    content). Splits on blank lines, normalises whitespace + lowercase, and
    drops any paragraph whose word-set is mostly contained in another's."""
    if not text:
        return text
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    if len(paragraphs) < 2:
        return text

    def words(p: str) -> set:
        return set(re.findall(r"[a-z0-9]+", p.lower()))

    word_sets = [words(p) for p in paragraphs]
    keep: List[int] = []
    for i, ws_i in enumerate(word_sets):
        redundant = False
        for j in keep:
            ws_j = word_sets[j]
            if not ws_i or not ws_j:
                continue
            overlap = len(ws_i & ws_j)
            shorter = min(len(ws_i), len(ws_j))
            if shorter and overlap / shorter >= 0.7:
                # Near-duplicate: keep whichever paragraph is longer.
                if len(ws_i) > len(ws_j):
                    keep.remove(j)
                    break
                redundant = True
                break
        if not redundant:
            keep.append(i)
    return "\n\n".join(paragraphs[k] for k in sorted(keep))


# ---------------------------------------------------------------------------
# Desc composition
# ---------------------------------------------------------------------------

_GROUNDING_PLACEHOLDER = "<one verbatim sentence from the document containing this value>"

# Appended to BOTH grounding blocks (table and scalar).
#
# Fields legitimately ask for values the paper never prints — "derive SD from
# SE", "the allocated N" when only a total is given. The grounding rules above
# simultaneously demand that the value appear inside its own quote, which for a
# computed number is impossible. The model resolved that contradiction the only
# way it could: by attaching a quote that does not contain the value (observed
# live as n_arm1=20 grounded on "The remaining 120 patients were evenly
# distributed among the six groups"), which is indistinguishable from a
# fabrication.
#
# source_text cannot carry the arithmetic: source_linker.locate_source matches it
# against the paper's own text to resolve a page/bbox for the PDF highlight, so
# invented text there breaks the highlight. The quote therefore stays verbatim on
# the INPUTS and the arithmetic goes in a separate `derived` key.
_DERIVATION_CLAUSE = (
    '- DERIVED values: if a value is not printed in the document but follows from '
    'values that are (converting SE/CI/IQR to SD, computing a per-group N from a '
    'total, unit conversion), you may report it. When you do: set "source_text" to '
    'the VERBATIM span containing the INPUTS you used (not the computed number, '
    'which does not appear in the document), and add a third key "derived" stating '
    'the arithmetic — e.g. {"value": "1.21", "source_text": "SE 0.27 | n = 20", '
    '"derived": "SD = SE x sqrt(n) = 0.27 x sqrt(20)"}. Never present a derived '
    'value as though it were printed, and never invent inputs in order to derive '
    'something: if the inputs are not in the document, the value is "NR".'
)


def _example_for_prompt(ex: dict) -> dict:
    """Rendered examples must model the grounding contract. An example whose
    source_text is blank teaches the model to skip citations — show the
    placeholder contract instead. NR examples keep their literal "NR"."""
    st = ex.get("source_text", "")
    if isinstance(st, str) and not st.strip() and not _is_not_reported(ex.get("value")):
        return {**ex, "source_text": _GROUNDING_PLACEHOLDER}
    return ex


def _declared_na_option(options) -> Optional[str]:
    """The author's own spelling of "not applicable", when they declared one.

    NA is offered to the model only through this route, so a free-text or
    numeric field can never reach for it.
    """
    for opt in (options or []):
        if absence.classify(str(opt)) == absence.NOT_APPLICABLE:
            return str(opt)
    return None


def _na_clause(option: str) -> str:
    """Teach NR-vs-NA for a field that declares an NA option.

    Phrased to deflate over-application: NA is a claim about the study DESIGN
    and must be grounded in a quote, while silence is always NR.
    """
    return (
        "\nNot applicable vs not reported:\n"
        f'- "{option}" means this field CANNOT apply to this study — e.g. a '
        "crossover-only question in a parallel-group trial, or an arm the study "
        "does not have. It is a claim about the study DESIGN.\n"
        f'- Choose "{option}" ONLY when the document states the design fact that '
        'makes the field inapplicable, and quote that statement in "source_text".\n'
        "- If the field could apply but the document simply never states the "
        f'value, that is "NR" — never "{option}". Silence is always NR.'
    )


def _format_context_value(val: Any) -> str:
    """Serialize an upstream field for injection as a dependent signature's
    input. Envelopes are stripped to value + source_text — status/source_location
    metadata is pipeline bookkeeping, not extraction context."""
    if isinstance(val, dict):
        clean = {k: v for k, v in val.items() if k in ("value", "source_text")}
        return json.dumps(clean or val, ensure_ascii=False, default=str)
    if isinstance(val, (list, tuple)):
        return json.dumps(val, ensure_ascii=False, default=str)
    return str(val)


def _normalize_enum_cell(cell: dict, options: list, field_name: str = "") -> dict:
    """Case-insensitively canonicalize a select cell's value against its
    options; flag anything that matches no option as `off_options`. Never
    rejects — reviewers see the raw value plus the flag.

    Deliberately status-independent: callers run this BEFORE `absence.stamp`
    so that a value matching a declared option (an author's "NA" / "None") is
    canonicalized and then classified as reported, not written off as absent.
    """
    if not options or not isinstance(cell, dict):
        return cell
    canon = {str(o).strip().lower(): o for o in options}
    raw = cell.get("value")
    is_list = isinstance(raw, list)
    vals = raw if is_list else [raw]
    out_vals: list = []
    off: list = []
    for v in vals:
        if isinstance(v, str):
            key = v.strip().lower()
            if key in canon:
                out_vals.append(canon[key])
            elif v.strip() == "" or v.strip().upper() in _NR_TOKENS:
                out_vals.append(v)
            else:
                off.append(v)
                out_vals.append(v)
        else:
            out_vals.append(v)
    cell = dict(cell)
    cell["value"] = out_vals if is_list else out_vals[0]
    if off:
        cell["off_options"] = off
        logger.warning(
            "Field '%s': value(s) %s not in options %s — flagged off_options.",
            field_name, off, options,
        )
    return cell


def _compose_field_desc(field_def: dict) -> str:
    """Build the full DSPy OutputField desc string from a structured field definition.

    If no structured keys (options/hints/rules/examples) are present, the
    description is returned verbatim — this covers Phase A where the LLM
    already emits a fully composed desc string.
    """
    description = field_def.get("description", "")
    options = field_def.get("options") or []
    hints = field_def.get("hints") or []
    rules = field_def.get("rules") or []
    examples = field_def.get("examples") or []
    source_grounded = field_def.get("source_grounded", False)
    subform_fields_data = field_def.get("subform_fields") or []

    # If there are no structured sub-fields, the description IS the full desc.
    if not (options or hints or rules or examples or source_grounded or subform_fields_data):
        return description

    # Strip any matching section blocks already present in description so we
    # don't double-bake (the LLM may have baked Hints/Rules/Examples into
    # description at gen time; structured arrays are the source of truth).
    description = _strip_embedded_sections(
        description,
        {
            "options": bool(options),
            "hints": bool(hints),
            "rules": bool(rules),
            "examples": bool(examples),
            "source_grounding": bool(source_grounded),
            "subform_fields": bool(subform_fields_data),
        },
    )

    # ── One definition of row identity, not two ───────────────────────────
    # When a composite key exists, the "Row Identity (authoritative)" block
    # below states it exactly. Any tuple an author typed by hand is then a
    # second, competing definition — and on 9 of 53 live table fields the two
    # disagreed, several with the stale tuple sitting under Rules, which the
    # editor calls a hard constraint. Drop the typed tuple so the model reads
    # one answer instead of an answer and a correction.
    #
    # Only whole multi-part tuples go. A single-concept clarification ("create
    # one row per timepoint") is author nuance and survives — see
    # table_schema.find_row_tuple.
    if field_key_columns(field_def):
        _stripped = strip_row_tuple(description)
        if _stripped != description:
            logger.debug(
                "[row-identity] dropped a hand-typed row tuple from '%s' — the "
                "composite key is authoritative", field_def.get("name"),
            )
            description = _stripped
        rules = [r for r in rules if not find_row_tuple(str(r))]

    parts: List[str] = []
    if description:
        parts.append(description)

    if options:
        if field_def.get("multiple"):
            parts.append(
                '\nOptions (multi-select — the "value" key is a JSON array '
                'containing every option that applies):'
            )
        else:
            parts.append("\nOptions:")
        for opt in options:
            parts.append(f'- "{opt}"')

    if hints:
        parts.append("\nExtraction Hints:")
        for hint in hints:
            parts.append(f"- {hint}")

    if rules:
        parts.append("\nRules:")
        for rule in rules:
            parts.append(f"- {rule}")

    # ── Row identity, composed from anchor_columns ────────────────────────
    # anchor_columns IS the row key, and codegen already computes it for every
    # table field (signature_gen.py). Before this block the key was ALSO typed
    # by hand into the field description, and the two drifted: CD015432's prose
    # said "one row per (comparison × outcome_type × reporter × timepoint)"
    # while anchor_columns also contained `scale`. row_then_columns and agentic
    # both read anchor_columns (record discovery's signature keeps only key columns;
    # agentic injects ROW IDENTITY COLUMNS), so only single-call depended on the
    # prose — and it produced 12 rows one run and 32 the next on the same paper,
    # because nothing told it authoritatively whether `scale` was part of the
    # identity.
    #
    # Composing it here makes anchor_columns the single source of truth for all
    # three modes, applies to existing forms with no regeneration, and means no
    # form author ever types a row key again. The precedence line resolves the
    # ~26 forms that still carry a hand-typed tuple without machine-editing
    # their authored prose.
    _key_cols = field_key_columns(field_def)
    if subform_fields_data and _key_cols:
        _attr_cols = [
            c.get("field_name") for c in subform_fields_data
            if c.get("field_name") and c.get("field_name") not in set(_key_cols)
        ]
        parts.append(
            "\nRow Identity (authoritative):\n"
            f"- Create exactly ONE row per unique combination of: "
            f"{' × '.join(_key_cols)}.\n"
            "- Two rows must never share all of those values. If the document reports "
            "the same combination more than once, that is ONE row.\n"
            + (
                f"- Every other column ({', '.join(_attr_cols)}) is a measurement "
                "determined by that combination, never part of the row identity.\n"
                if _attr_cols else ""
            )
            + "- If any sentence elsewhere in this field lists a different combination, "
            "THIS list wins."
        )

    if subform_fields_data:
        parts.append(
            '\nTable Columns (each row must populate these keys; '
            'EACH CELL VALUE MUST itself be {"value": ..., "source_text": ...}):'
        )
        for col in subform_fields_data:
            cname = col.get("field_name", "")
            ctype = col.get("field_type", "string")
            cdesc = col.get("field_description") or ""
            parts.append(f"  - {cname} ({ctype}){': ' + cdesc if cdesc else ''}")
            for col_opt in (col.get("options") or []):
                parts.append(f'      • Option: "{col_opt}"')
            for col_hint in (col.get("hints") or []):
                parts.append(f"      \u2022 Hint: {col_hint}")
            for col_rule in (col.get("rules") or []):
                parts.append(f"      \u2022 Rule: {col_rule}")
            _col_na = _declared_na_option(col.get("options"))
            if _col_na:
                parts.append(
                    f'      \u2022 Use "{_col_na}" only when the study design makes '
                    f"this column inapplicable, and quote the design statement; if "
                    f'the paper is merely silent, use "NR".'
                )
            for col_ex in (col.get("examples") or []):
                # Show the full {value, source_text} envelope so the LLM emits
                # cells in that shape — do NOT strip source_text from examples.
                col_payload = col_ex if isinstance(col_ex, dict) else {"value": col_ex, "source_text": ""}
                parts.append(f"      \u2022 Example cell: {json.dumps(_example_for_prompt(col_payload))}")

    if source_grounded:
        if subform_fields_data:
            parts.append(
                '\nSource Grounding (table):\n'
                '- Return a dictionary with two keys: "value" and "source_text".\n'
                '- "value": a list of row dicts.\n'
                '- "source_text": ONE sentence (≤30 words) from the document that '
                'introduces the table or its caption — copied verbatim.\n'
                '- Each row dict maps column_name -> {"value": <cell_value>, "source_text": <cell_quote>}.\n'
                '- Each per-cell "source_text" MUST be text copied VERBATIM from the '
                'document (≤30 words): either the sentence that states the value, or — '
                'when the value is a table cell — that cell\'s row copied as it appears, '
                'including the row/column labels that identify it (e.g. '
                '"Group A | 51.8 | 5.85"). Do not reformat or re-space the row.\n'
                '- The cell value MUST appear as a substring of its source_text whenever '
                'the value appears literally in the document. If the value is a '
                'normalization (e.g., "RCT" from "randomized controlled trial"), '
                'source_text must contain the phrase the value was derived from.\n'
                '- Do NOT paraphrase, summarize, or stitch together text from multiple sentences.\n'
                '- If a single cell is not reported, use {"value": "NR", "source_text": "NR"} for that cell.\n'
                '- If the entire table is not reported, return {"value": "NR", "source_text": "NR"}.\n'
                '- Do NOT guess or infer cell values beyond the text: if the document '
                'genuinely does not state a cell — and it cannot be derived under the '
                'DERIVED rule below — use NR for that cell rather than a fabricated '
                'value.\n'
                + _DERIVATION_CLAUSE
            )
        else:
            parts.append(
                '\nSource Grounding:\n'
                '- Return a dictionary with two keys: "value" and "source_text".\n'
                '- "source_text" MUST be text copied VERBATIM from the document, '
                'normally ONE sentence (≤30 words).\n'
                '- If the value comes from a TABLE or FIGURE, copy the table row or '
                'cell verbatim instead — include the row/column labels and the number '
                'as they appear (e.g. "Age, mean (SD) | 51.8 (5.85)"); it need NOT be '
                'a sentence. Do not reformat or re-space the row.\n'
                '- The extracted value MUST appear as a substring of source_text whenever '
                'the value appears literally in the document. If the value is a '
                'normalization (e.g., "RCT" from "randomized controlled trial"), '
                'source_text must contain the phrase the value was derived from.\n'
                '- Do NOT paraphrase, summarize, or stitch together text from multiple sentences.\n'
                '- If value is "NR", set source_text to "NR".\n'
                '- Do NOT guess or infer beyond the text: if the document genuinely '
                'does not state the value — and it cannot be derived under the DERIVED '
                'rule below — return "NR" rather than a fabricated one.\n'
                + _DERIVATION_CLAUSE
            )

    if source_grounded:
        # Conditional on the field's own declared options, which are already
        # part of sig_def — so the signature-class content hash covers it and
        # existing forms pick this up without regeneration.
        _na_option = _declared_na_option(options)
        if _na_option:
            parts.append(_na_clause(_na_option))

    if examples:
        parts.append("\nExamples:")
        for ex in examples:
            if isinstance(ex, dict):
                parts.append(json.dumps(_example_for_prompt(ex), ensure_ascii=False))
            else:
                parts.append(str(ex))

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Review scope (project-level extraction context)
# ---------------------------------------------------------------------------

def _compose_scope_block(review_scope: str) -> str:
    """The prompt block stating the review's scope as context, not a filter.

    The "do not omit" clause is load-bearing: told only what a review is about,
    a model will start silently dropping rows it judges off-topic. Deciding
    inclusion is a separate feature, deliberately not shipped yet.
    """
    return (
        "REVIEW SCOPE — this document is being extracted for the following "
        f"systematic review:\n{review_scope}\n\n"
        "Use this ONLY to resolve ambiguity: which arm, population, timepoint or "
        "measure a field refers to when the paper reports several. Do NOT omit or "
        "drop anything because it looks out of scope — report everything each "
        "field asks for, including every row you find.\n\n"
    )


def apply_review_scope(schema_def: dict, review_scope: Optional[str]) -> dict:
    """Return a copy of schema_def with the review scope folded into every signature.

    The scope is written *into* each sig_def rather than carried alongside it, and
    that is load-bearing: `build_signature_class` is LRU-cached on the content hash
    of sig_def, so a scope living outside sig_def would let two projects with
    identical forms but different scopes collide on one cache entry and extract
    under each other's scope.

    The same property is why this needs no form regeneration — a changed hash
    simply builds a new signature class.

    With no scope set, schema_def is returned unchanged so prompts stay
    byte-identical to before.
    """
    scope = (review_scope or "").strip()
    if not scope:
        return schema_def

    block = _compose_scope_block(scope)
    scoped_sigs = []
    for sig_def in schema_def.get("signatures", []):
        new_sig = dict(sig_def)
        # Kept as a structured key so the keyed builders, which synthesize
        # their own docstrings and never inherit the parent's, can pick it up.
        new_sig["review_scope"] = scope
        new_sig["docstring"] = block + (sig_def.get("docstring") or "")
        scoped_sigs.append(new_sig)

    out = dict(schema_def)
    out["signatures"] = scoped_sigs
    return out


# ---------------------------------------------------------------------------
# Content hash for cache key
# ---------------------------------------------------------------------------

def _content_hash(sig_def: dict) -> str:
    """Stable 16-char hex hash of a signature definition for cache keying."""
    canonical = json.dumps(sig_def, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Signature class builder (cached)
# ---------------------------------------------------------------------------

@lru_cache(maxsize=512)
def _build_signature_class_cached(
    class_name: str,
    task_name: str,
    content_hash: str,  # included so cache invalidates on content change
    sig_def_json: str,
) -> Type[dspy.Signature]:
    sig_def = json.loads(sig_def_json)
    return _build_signature_class_impl(class_name, task_name, sig_def)


def _build_signature_class_impl(
    class_name: str,
    task_name: str,
    sig_def: dict,
) -> Type[dspy.Signature]:
    """Core type() construction logic."""
    attrs: Dict[str, Any] = {}
    annotations: Dict[str, Any] = {}

    # __doc__ MUST be set before type() — DSPy uses it as the system prompt.
    # Setting it after the call leaves __doc__ = None and silently degrades
    # prompt quality.
    attrs["__doc__"] = sig_def.get("docstring", f"{class_name} extraction signature.")
    attrs["__module__"] = f"dspy_components.runtime.{task_name}"

    for inp in sig_def.get("input_fields", []):
        name = inp["name"]
        py_type = _resolve_type(inp.get("type", "str"), str)
        annotations[name] = py_type
        attrs[name] = dspy.InputField(desc=inp.get("desc", f"Input: {name}"))

    for out in sig_def.get("output_fields", []):
        name = out["name"]
        py_type = _resolve_type(out.get("type", "Dict[str, Any]"), Dict[str, Any])
        annotations[name] = py_type
        attrs[name] = dspy.OutputField(desc=_compose_field_desc(out))

    attrs["__annotations__"] = annotations

    sig_class = type(class_name, (dspy.Signature,), attrs)

    # Verify output field ordering — dict order matters for DSPy structured output.
    actual_keys = list(sig_class.output_fields.keys())
    expected_keys = [f["name"] for f in sig_def.get("output_fields", [])]
    if actual_keys != expected_keys:
        logger.warning(
            "Output field order mismatch for %s: expected %s, got %s",
            class_name, expected_keys, actual_keys,
        )

    return sig_class


def build_signature_class(
    sig_def: dict,
    task_name: str = "runtime",
) -> Type[dspy.Signature]:
    """Build a dspy.Signature subclass from a signature definition dict.

    Results are LRU-cached by (class_name, task_name, content_hash) so
    DSPy's internal program caches stay stable across repeated calls.
    """
    class_name = sig_def["class_name"]
    h = _content_hash(sig_def)
    sig_def_json = json.dumps(sig_def, sort_keys=True, ensure_ascii=False)
    return _build_signature_class_cached(class_name, task_name, h, sig_def_json)


# ---------------------------------------------------------------------------
# Extractor (Module) class builder
# ---------------------------------------------------------------------------

class _RuntimeExtractorBase(dspy.Module):
    """Top-level parent for all runtime-built extractors.

    Class-attribute config (not closure captures) keeps __reduce__ clean for
    GEPA/MIPROv2 deepcopy/pickle operations.
    """

    _sig_class: Type[dspy.Signature] = None
    _fallback_structure: Dict[str, Any] = None
    _requires_fields: List[str] = None
    _field_options: Dict[str, list] = None  # field → allowed options (select fields)

    def __init__(self):
        super().__init__()
        self.extract = dspy.ChainOfThought(self.__class__._sig_class)

    async def __call__(self, markdown_content: str, **kwargs) -> Dict[str, Any]:
        fallback = self.__class__._fallback_structure or {}
        requires = self.__class__._requires_fields or []

        try:
            if requires:
                call_kwargs: Dict[str, Any] = {}
                for field in requires:
                    call_kwargs[field] = _format_context_value(
                        kwargs.get(field, _UPSTREAM_UNAVAILABLE)
                    )
                outputs = await async_dspy_forward(
                    self.extract,
                    markdown_content=markdown_content,
                    **call_kwargs,
                )
            else:
                outputs = await async_dspy_forward(
                    self.extract,
                    markdown_content=markdown_content,
                    **kwargs,
                )

            # A max_tokens cutoff yields salvageable-but-incomplete output: the
            # parser recovers whole rows and drops the rest, so a truncated
            # table is indistinguishable from a short one downstream unless we
            # mark it here. Flagged per field rather than raised, because the
            # rows we did get are real and worth keeping.
            _truncated = was_truncated(self.extract)

            result: Dict[str, Any] = {}
            for field_name, default in fallback.items():
                if default == []:
                    raw = outputs.get(field_name, [])
                    if isinstance(raw, str):
                        # Mirror the keyed path: DSPy sometimes hands the
                        # list back as a JSON string.
                        try:
                            parsed = json.loads(raw)
                            if isinstance(parsed, (list, dict)):
                                raw = parsed
                        except (json.JSONDecodeError, TypeError):
                            pass
                    result[field_name] = raw
                else:
                    cell = outputs.get(field_name)
                    if cell is None:
                        # Model omitted this field → failure, not a genuine NR.
                        result[field_name] = absence.failure_envelope(absence.MISSING)
                    else:
                        if (
                            isinstance(cell, dict)
                            and "value" not in cell
                            and cell
                            and all(isinstance(v, dict) for v in cell.values())
                        ):
                            # Some models (e.g. Qwen) return table fields keyed by row
                            # identity (arm/group name) instead of the expected
                            # {"value": [rows]} envelope. Row identity is already
                            # captured inside each row (e.g. arm_label), so this is a
                            # lossless reshape rather than a guess.
                            cell = {"value": list(cell.values()), "source_text": ""}
                        elif not isinstance(cell, dict):
                            cell = {"value": cell, "source_text": ""}
                        opts = (self.__class__._field_options or {}).get(field_name)
                        if opts:
                            cell = _normalize_enum_cell(cell, opts, field_name)
                        result[field_name] = absence.stamp(cell, opts)

            if _truncated:
                # Downgrade every field this call produced to `partial` and leave
                # a machine-readable marker. Reviewers must not see an under-filled
                # table presented as a complete "reported" answer.
                for field_name, env in result.items():
                    if isinstance(env, dict):
                        env["truncated"] = True
                        if env.get("status") == absence.REPORTED:
                            env["status"] = absence.PARTIAL
                logger.error(
                    "Extractor %s: output truncated at max_tokens — fields %s marked "
                    "partial (rows are missing).",
                    self.__class__.__name__, sorted(result.keys()),
                )
            return result

        except Exception as e:
            logger.error(
                "Extractor %s failed: %s", self.__class__.__name__, e, exc_info=True
            )
            raise


def build_extractor_class(
    sig_class: Type[dspy.Signature],
    fallback_structure: Dict[str, Any],
    requires_fields: Optional[List[str]] = None,
    field_options: Optional[Dict[str, list]] = None,
) -> Type[dspy.Module]:
    """Build an async dspy.Module extractor class wrapping a given signature."""
    class_name = f"Async{sig_class.__name__}Extractor"
    return type(
        class_name,
        (_RuntimeExtractorBase,),
        {
            "__module__": sig_class.__module__,
            "_sig_class": sig_class,
            "_fallback_structure": fallback_structure,
            "_requires_fields": list(requires_fields or []),
            "_field_options": dict(field_options or {}),
        },
    )


# ---------------------------------------------------------------------------
# Two-stage (row-first + per-row parallel) extractor helpers
# ---------------------------------------------------------------------------

def _build_record_discovery_sig_def(parent_sig_def: dict, output_field_def: dict) -> dict:
    """Signature def for record discovery: key columns only, no attributes.

    The parent field's prose is reshaped for record discovery:
    - `examples` are dropped — they are full rows including value columns,
      which contradicts "populate only the key columns";
    - rules mandating the whole-field {"value": "NR"} envelope are dropped —
      record discovery returns a bare array, so an empty [] is the not-reported answer;
    - the dict-envelope Source Grounding block is suppressed
      (source_grounded=False) and the array-shaped grounding contract is stated
      inline instead. Without this the prompt demands three different output
      shapes at once (bare array vs envelope dict vs NR object).
    """
    key_cols = field_key_columns(output_field_def)
    key_set = set(key_cols)
    key_subfields = [
        sf for sf in (output_field_def.get("subform_fields") or [])
        if sf.get("field_name") in key_set
    ]
    discovery_field = {
        k: v for k, v in output_field_def.items()
        if k not in ("extraction_strategy", "anchor_columns", "key_columns", "examples")
    }
    discovery_field["subform_fields"] = key_subfields
    discovery_field["source_grounded"] = False

    def _demands_nr_envelope(rule: str) -> bool:
        compact = str(rule).replace(" ", "").replace("'", '"').lower()
        # Both spellings: an author who wrote the NA convention would otherwise
        # keep a rule contradicting record discovery's bare-array contract.
        return '{"value":"nr"' in compact or '{"value":"na"' in compact

    discovery_field["rules"] = [
        r for r in (discovery_field.get("rules") or []) if not _demands_nr_envelope(r)
    ]
    if not discovery_field["rules"]:
        discovery_field.pop("rules")

    # record discovery always returns a list of row dicts — override whatever the schema_def
    # has (typically "Dict[str, Any]") so DSPy validates and coerces correctly.
    discovery_field["type"] = "List[Dict[str, Any]]"
    existing_desc = discovery_field.get("description", "")
    discovery_field["description"] = (
        existing_desc + (("\n\n") if existing_desc else "") +
        "STAGE 1 — ROW DISCOVERY ONLY: Identify every distinct row. "
        "Populate only the anchor columns listed below. "
        "Do NOT fill measurement/value columns — those are extracted separately.\n\n"
        # record discovery used to get NO statement of row identity at all: it is the one
        # call `_compose_field_desc`'s "Row Identity (authoritative)" block never
        # reaches, because this builder strips `anchor_columns` from the field
        # dict and that block is gated on it. The design assumed the structure
        # spoke for itself — the key columns ARE the only outputs — but which
        # columns identify a row does not convey that rows must be UNIQUE on
        # them, nor that only combinations the paper reports may be created.
        # Nothing downstream catches an invented combination either: the census
        # checker looks for omissions, not fabrications.
        + (
            f"ROW IDENTITY: a row is one unique combination of "
            f"{' × '.join(key_cols)}. Two rows must never share all of those "
            "values — if the document reports the same combination more than "
            "once, that is ONE row.\n\n"
            "Create a row ONLY for combinations the document actually reports. "
            "Do NOT generate combinations by pairing identity values that appear "
            "separately in different parts of the document — if the paper reports "
            "Arm A at 2 h and Arm B at 24 h, that is two rows, not four.\n\n"
            if key_cols else ""
        ) +
        "OUTPUT SHAPE: Return a JSON **array** of row objects: "
        "`[{...}, {...}, ...]`. "
        "Do NOT return an object keyed by row names "
        "(e.g. `{\"row_1\": {...}, \"row_2\": {...}}` is WRONG) and do NOT wrap "
        "the array in a `{\"value\": ...}` envelope — the bare array is the answer. "
        "Each array element is one row dict with the anchor-column keys.\n\n"
        "SOURCE GROUNDING: each anchor cell must be "
        "`{\"value\": <cell_value>, \"source_text\": <quote>}` where the quote is "
        "ONE sentence (≤30 words) copied VERBATIM from the document that grounds "
        "that cell — never paraphrased.\n\n"
        "NOT REPORTED: if the document contains no rows for this table, return an "
        "empty array `[]` — never an NR object in place of the array."
    )
    # record discovery synthesizes its own docstring and never inherits the parent's, so
    # the review scope must be re-applied here — otherwise tables, the place
    # scope matters most, would be the one path that silently misses it.
    _scope = (parent_sig_def.get("review_scope") or "").strip()
    return {
        "class_name": f"{parent_sig_def['class_name']}RecordDiscovery",
        "docstring": (
            (_compose_scope_block(_scope) if _scope else "")
            + f"Stage 1 row discovery for {output_field_def['name']}. Anchor columns only."
        ),
        "input_fields": parent_sig_def.get("input_fields", []),
        "output_fields": [discovery_field],
    }


def _key_column_legend(output_field_def: dict) -> List[str]:
    """What each key column MEANS — needed by every call that fills values.

    A value call receives the row identity as bare JSON (`{"timepoint":
    "2h_to_24h", ...}`). Without the column's own definition, that string is
    uninterpretable, and the model can reasonably read a range like `2h_to_24h`
    as "an aggregate across 2-24h" rather than "the bin a reported time maps
    into". Observed on Polat 2005b (Aug 11 2026): every mean and SD came back NR
    with the reasoning "the paper never reports a single aggregated
    pain_intensity mean/SD collapsed across the 2h-24h range" — while the form's
    own timepoint description says "mapped to the nearest bin ... report the
    EARLIEST measurement in that bin". Correct extraction, near-empty table,
    purely because the definition was dropped before the values were asked for.
    """
    key_set = set(field_key_columns(output_field_def))
    lines: List[str] = []
    for sf in (output_field_def.get("subform_fields") or []):
        if sf.get("field_name") not in key_set:
            continue
        line = f"- {sf.get('field_name')}"
        cdesc = (sf.get("field_description") or "").strip()
        if cdesc:
            line += f": {cdesc}"
        opts = sf.get("options") or []
        if opts:
            line += f" (one of: {', '.join(str(o) for o in opts)})"
        # Rules on an key column often carry the mapping convention itself
        # ("a measurement at exactly a bin's lower bound belongs to THAT bin"),
        # which is precisely what a value call needs and cannot infer.
        for rule in (sf.get("rules") or []):
            r = str(rule).strip()
            if r:
                line += f"\n    · {r}"
        lines.append(line)
    return lines


def _build_row_slot_fill_sig_def(
    parent_sig_def: dict, output_field_def: dict, attr_col_defs: list
) -> dict:
    """Signature def for one row-at-a-time slot-fill call.

    Inputs:  markdown_content (from parent) + row_anchor (single row JSON from record discovery).
    Outputs: every attribute column (non-key) for that one record.

    Each call is anchored to exactly one row — no positional alignment required.
    The row_anchor desc carries a legend of what each key column means —
    without it, slot filling would have to reverse-engineer normalized codes like
    '2h_to_24h' whose definitions live only in record discovery's prompt. Attribute
    columns are always source-grounded so the verbatim-quote contract applies to the
    cells that carry the actual measurements.
    """
    key_set = set(field_key_columns(output_field_def))
    key_col_defs = [
        sf for sf in (output_field_def.get("subform_fields") or [])
        if sf.get("field_name") in key_set
    ]

    legend_lines = _key_column_legend(output_field_def)

    key_example = (
        json.dumps({sf.get("field_name"): "..." for sf in key_col_defs}, ensure_ascii=False)
        if key_col_defs else '{"<anchor_column>": "..."}'
    )
    row_anchor_desc = (
        "JSON dict of anchor column values identifying this specific row, e.g. "
        f"{key_example}. Extract all output fields for THIS ROW ONLY."
    )
    if legend_lines:
        row_anchor_desc += (
            "\n\nAnchor column meanings (as defined during row discovery):\n"
            + "\n".join(legend_lines)
        )

    output_fields = []
    for col_def in attr_col_defs:
        col_name = col_def["field_name"]
        output_fields.append({
            "name": col_name,
            "type": "Dict[str, Any]",
            "description": (
                (col_def.get("field_description", "") + "\n\n")
                + "STAGE 2 — PER-ROW EXTRACTION: extract this column's value for the "
                "single row identified by `row_anchor`. "
                "Return a single {\"value\": ..., \"source_text\": ...} dict. "
                "Do NOT return a list — this call covers exactly one row."
            ),
            "options": col_def.get("options") or [],
            "hints": col_def.get("hints") or [],
            "rules": col_def.get("rules") or [],
            "examples": col_def.get("examples") or [],
            # Value cells carry the measurements — always source-grounded so
            # the verbatim/substring contract is part of the slot filling prompt.
            "source_grounded": True,
        })
    # Same reason as record discovery: this docstring is synthesized, not inherited.
    _scope = (parent_sig_def.get("review_scope") or "").strip()
    return {
        "class_name": f"{parent_sig_def['class_name']}RowSlotFill",
        "docstring": (
            (_compose_scope_block(_scope) if _scope else "")
            + "Stage 2 per-row extraction. "
            "row_anchor pins this call to a single row from Stage 1; "
            "extract all non-anchor columns for that row."
        ),
        "input_fields": [
            *parent_sig_def.get("input_fields", []),
            {
                "name": "row_anchor",
                "type": "str",
                "desc": row_anchor_desc,
            },
        ],
        "output_fields": output_fields,
    }


# ---------------------------------------------------------------------------
# Phase 2 — recall audit + record-set reconciliation
# ---------------------------------------------------------------------------
# Record discovery runs as a single call, and one call cannot prove it found
# every record: a table missing rows is still valid JSON, still correctly shaped, still
# non-empty, so nothing downstream fires. The audit is a SECOND pass that asks
# a DIFFERENT question — not "enumerate the rows again" (a re-run is anchored on
# the same reading and tends to agree with itself) but "here is a candidate plan,
# what did it miss?".
#
# Every proposed row must carry a verbatim quote, and the quote is checked in
# PYTHON against the paper via source_linker before the row is accepted. That is
# what stops the audit inventing plausible rows to look useful: it must point
# at text we can find. It does not prove the row is real, only that the evidence
# it cited exists.
#
# Fail-safe by construction: any error, unparseable output, or unverifiable
# addition leaves the candidate plan exactly as record discovery produced it.
# Runs on every keyed extraction — no flag. Cost is one extra call per table
# field per paper, on the same cached paper prefix record discovery and slot filling use.
_RECALL_AUDIT_QUOTE_THRESHOLD = 0.65   # matches source_linker/agentic grounding gate
_EVIDENCE_KEY = "evidence_quote"


def _build_recall_audit_sig_def(parent_sig_def: dict, output_field_def: dict) -> dict:
    """Signature def for the recall auditor: audit a candidate record set.

    Deliberately NOT a second row-discovery signature. It receives the candidate
    plan as an input and returns ONLY rows absent from it, each with the quote
    that evidences it. An empty array means "the plan looks complete", which is
    the common case and costs nothing downstream.
    """
    key_cols = field_key_columns(output_field_def)
    key_set = set(key_cols)
    key_subfields = [
        sf for sf in (output_field_def.get("subform_fields") or [])
        if sf.get("field_name") in key_set
    ]

    # Only the identity vocabulary is carried over: descriptions and options tell
    # the checker what a row IS. Examples/rules from the parent describe full
    # rows including measurements, which is the wrong task here.
    checker_field = {
        "name": "missing_rows",
        "type": "List[Dict[str, Any]]",
        "source_grounded": False,
        "subform_fields": key_subfields,
        "description": (
            "ROWS MISSING FROM THE CANDIDATE PLAN.\n\n"
            "You are auditing a record set someone else produced for this table. Your "
            "job is NOT to enumerate the rows again — it is to find what the plan "
            "MISSED.\n\n"
            f"A row is identified by: {' × '.join(key_cols)}.\n\n"
            "Method:\n"
            "- Read `candidate_row_plan` first.\n"
            "- Search the full document specifically for valid row identities that are "
            "NOT represented in the candidate plan.\n"
            "- Check results tables, results prose, figure captions, table footnotes, "
            "supplementary result sections, and the abstract when it reports study "
            "results. Tables continue past page separators ({N}------) — follow them.\n"
            "- Methods sections may be used as a SEARCH HINT, but a planned or "
            "mentioned outcome alone is NOT sufficient to create a row.\n"
            "- Add a row only when you can quote evidence showing that this specific "
            "combination of identity columns is actually reported in the paper.\n"
            "- Do NOT create rows by combining identity values that appear separately "
            "in different parts of the document.\n\n"
            "OUTPUT SHAPE: a JSON **array** of row objects, each with the identity "
            f"keys above plus `{_EVIDENCE_KEY}`. Return `[]` if the plan is complete "
            "— that is a normal, expected answer and is preferred over a guess.\n\n"
            "RULES:\n"
            "- Every row you return MUST have EVERY identity column filled. A row "
            "with a blank or 'NR' identity column is not a row and will be discarded.\n"
            f"- `{_EVIDENCE_KEY}` MUST be text copied VERBATIM from the document "
            "(≤30 words). It is checked against the document automatically; a "
            "paraphrased, stitched or invented quote causes the row to be DISCARDED.\n"
            f"- `{_EVIDENCE_KEY}` must support the proposed row ITSELF, not merely "
            "contain one of its anchor values. It must support the complete row "
            "identity, either directly or through an immediately associated table "
            "row / header context.\n"
            "- Do NOT infer a Cartesian combination — e.g. Arm A × Outcome X × 24h — "
            "merely because Arm A, Outcome X and 24h each appear somewhere in the "
            "paper.\n"
            "- Do NOT repeat rows already in the plan. Do NOT propose a row you cannot "
            "quote. Do NOT include measured values — identities and the quote only.\n"
            "- Prefer returning nothing over returning a row you are unsure of: a "
            "wrong addition corrupts the table, while a genuine omission is caught "
            "again later."
        ),
    }

    _scope = (parent_sig_def.get("review_scope") or "").strip()
    plan_input = {
        "name": "candidate_row_plan",
        "type": "str",
        "desc": (
            "The candidate record set to audit — a JSON array of row identities "
            "already discovered. Find what is MISSING from it; do not restate it."
        ),
    }
    return {
        "class_name": f"{parent_sig_def['class_name']}RecallAudit",
        "docstring": (
            (_compose_scope_block(_scope) if _scope else "")
            + f"Audit a candidate record set for {output_field_def['name']} and report "
              "only the rows it is missing."
        ),
        "input_fields": list(parent_sig_def.get("input_fields", [])) + [plan_input],
        "output_fields": [checker_field],
    }


def _canonical_record_key(row: dict, key_cols: List[str]) -> str:
    """Canonical identity for a row, computed on key columns only.

    Case- and whitespace-insensitive so 'Amoxicillin 2 g' and 'amoxicillin 2 g'
    are the same row. Accepts both the flat shape and the {value, source_text}
    cell shape.
    """
    flat = {}
    for a in key_cols:
        v = row.get(a)
        if isinstance(v, dict):
            v = v.get("value")
        if v is None:
            flat[a] = ""
            continue
        text = " ".join(str(v).split()).lower()
        # One meaning, one spelling. Now that an absent value can be part of an
        # identity, "NA" and "N/A" must not file the same record twice — and a
        # dedup that misses is a duplicated row in the reviewer's table.
        cls = absence.classify(v)
        if cls == absence.NOT_APPLICABLE:
            text = absence.NA_LABEL.lower()
        elif cls == absence.NOT_REPORTED and text:
            text = absence.NR_LABEL.lower()
        flat[a] = text
    return json.dumps(flat, sort_keys=True, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Phase 3 — plan-vs-output check + batched targeted repair
# ---------------------------------------------------------------------------
# The frozen record set means nothing until something verifies slot filling honoured it.
# Two ways a frozen record can come back unusable:
#   1. absent from the output entirely — impossible in the per-row path today
#      (the merge emits one row per frozen record) but a real risk once value
#      extraction is batched, where a truncated response silently drops rows;
#   2. present with EVERY value cell in a failure status (missing/error) —
#      the live case: a slot filling call that raised, or answered without the fields.
#
# NR is NOT failure. A paper genuinely may not report a row's numbers, and
# treating that as failure would fire repairs on most real papers and burn calls
# chasing values that do not exist. Only absence-of-an-answer is repaired.
#
# Repair is batched: up to _REPAIR_MAX_ROWS_PER_CALL identities per call, so a
# 10-row shortfall costs one call rather than ten.
_REPAIR_MAX_ROUNDS = 2

# Batched value extraction, ON by default again (Aug 12 2026).
#
# It had never succeeded in production — 4/4 jobs returned `filled_rows` None —
# for one reason: `out.get(...) if isinstance(out, dict)`. A dspy.Prediction is
# not a dict, so that read was unconditionally None. Fixed in _fill_slots_set_once;
# the tests now mock with dspy.Prediction so the production type is exercised.
#
# Set EXTRACTION_BATCH_VALUES=0 to force the per-row path, which remains the
# fallback under both the batch and its retry.
_SET_AT_A_TIME = os.getenv("EXTRACTION_BATCH_VALUES", "1") == "1"

# Batch sizing. Output cost per cell ≈ value + a ≤30-word quote + JSON keys.
_TOKENS_PER_CELL = 60
# Reserve headroom for ChainOfThought's reasoning field and JSON scaffolding.
_OUTPUT_BUDGET_FRACTION = 0.7
# A generous upper bound, not a caution: 40 rows × 13 value cols ≈ 31k output
# tokens, comfortable on Claude. It exists only so a 200-row table becomes a few
# calls instead of one enormous generation.
_MAX_RECORDS_PER_CALL = 40
# Used only when the served model can't be determined — the floor every fallback
# model imposes (MODEL_MAX_OUTPUT_DEFAULT / bedrock third-party).
_FALLBACK_OUTPUT_CEILING = 8192


def _active_output_ceiling(cot_instance=None) -> int:
    """The output ceiling of the model ACTUALLY serving this call.

    Read off the live LM rather than assumed, because `circuit_breaker` builds
    each LM with `resolve_max_output_tokens(model, EXTRACTION_MAX_TOKENS)` — so
    `lm.kwargs["max_tokens"]` is already the clamped, true ceiling. Sizing
    batches against the worst-case fallback floor instead would cap every batch
    at ~10 rows even on Claude, which has 6× the room.
    """
    try:
        lm = getattr(cot_instance, "lm", None) or dspy.settings.lm
        kwargs = getattr(lm, "kwargs", None) or {}
        mt = kwargs.get("max_tokens")
        if isinstance(mt, int) and mt > 0:
            return mt
        from config.models import EXTRACTION_MAX_TOKENS, resolve_max_output_tokens
        return int(resolve_max_output_tokens(getattr(lm, "model", "") or "", EXTRACTION_MAX_TOKENS))
    except Exception:
        return _FALLBACK_OUTPUT_CEILING


def _records_per_call(n_attr_cols: int, cot_instance=None) -> int:
    """How many rows one value-extraction call can carry.

    Derived from the served model's real ceiling: ~57 rows at 13 value columns on
    Claude's 64k, ~14 on gpt-4o's 16k, ~7 on an 8k fallback — capped at
    _MAX_RECORDS_PER_CALL. Truncation is not a data-loss risk here because the
    plan-vs-output check repairs any rows a cut-off batch drops; an over-large
    batch costs extra refill calls, never rows.
    """
    budget = int(_active_output_ceiling(cot_instance) * _OUTPUT_BUDGET_FRACTION)
    per_row = max(1, n_attr_cols) * _TOKENS_PER_CELL
    return max(1, min(_MAX_RECORDS_PER_CALL, budget // per_row))


def _coerce_value_cell(val: Any, col: str, field_name: str, col_options) -> Dict[str, Any]:
    """Normalize one value cell from any of the shapes models return.

    Shared by the batched pass, the per-row fallback and repair, so all three
    produce identical envelopes — they diverged before and repair silently
    skipped the enum normalisation.
    """
    if val is None:
        # Model answered but omitted this column → failure, not a genuine NR.
        return absence.failure_envelope(absence.MISSING)
    if isinstance(val, str):
        try:
            parsed = json.loads(val)
            val = parsed if isinstance(parsed, dict) else {"value": parsed, "source_text": ""}
        except (json.JSONDecodeError, TypeError):
            val = {"value": val, "source_text": ""}
    if not isinstance(val, dict):
        val = {"value": val, "source_text": ""}
    # Canonicalize against declared options first so a declared "NA"/"None"
    # reads as the answer it is.
    if col_options:
        val = _normalize_enum_cell(val, col_options, f"{field_name}.{col}")
    return absence.stamp(val, col_options)


def _record_needs_refill(row: dict, attr_cols: List[str]) -> bool:
    """True when every value cell failed — no answer at all, not a deliberate NR."""
    if not attr_cols:
        return False
    for col in attr_cols:
        cell = row.get(col)
        if not isinstance(cell, dict):
            return False
        if absence.normalize_status(cell.get("status")) not in absence.FAILURE_STATUSES:
            return False
    return True


def _build_set_slot_fill_sig_def(
    parent_sig_def: dict, output_field_def: dict, attr_col_defs: list
) -> dict:
    """Signature def for one set-at-a-time refill call: fill values for known rows.

    The row identities are an INPUT, so this signature cannot rediscover rows —
    it fills the value columns for rows already locked. Also the shape Phase 4
    will reuse for the main batched value pass.
    """
    field_name = output_field_def["name"]
    key_cols = field_key_columns(output_field_def)
    value_names = [c["field_name"] for c in attr_col_defs]

    refill_field = {
        "name": "filled_rows",
        "type": "List[Dict[str, Any]]",
        "source_grounded": False,
        "subform_fields": attr_col_defs,
        "description": (
            "VALUES FOR THE ROWS YOU WERE GIVEN.\n\n"
            "`rows_to_fill` lists row identities already established for this "
            "table. Fill in the value columns for each of them — nothing else.\n\n"
            "OUTPUT SHAPE: a JSON **array** with ONE object per row in "
            "`rows_to_fill`, in the same order. Each object repeats that row's "
            f"identity keys ({', '.join(key_cols)}) so it can be matched back, "
            f"plus the value columns ({', '.join(value_names)}).\n\n"
            "RULES:\n"
            "- Return EVERY row you were given, even if you can find none of its "
            "values — use NR for the cells you cannot find.\n"
            # The failure this prevents: given 8 rows whose `scale` said only
            # "VAS" while the paper reported VAS for four activities, the model
            # spent its whole response deciding which activity to use, wrote the
            # correct numbers into `reasoning`, and returned filled_rows = null.
            # Twice, on the same job (4c23c525, Aug 12 2026). It had done the work
            # and simply never put it in the answer.
            "- **The answer field is the only output that counts.** Values written "
            "in your reasoning and not in `filled_rows` are LOST. Never end with an "
            "empty answer.\n"
            "- If a row's identity does not pin down exactly one measurement — the "
            "instrument is reported for several activities, sites or conditions and "
            "the row does not say which — do NOT stall on the choice. Take the "
            "primary or first-reported one, fill the row, and name the choice in "
            "that cell's `source_text` (e.g. \"Chewing, 2 h: 3.7 ± 2.75\"). An "
            "answered row with a stated assumption is useful; an unanswered row is "
            "not.\n"
            "- Do NOT add rows. Do NOT drop rows. Do NOT change an identity value; "
            "copy it back exactly as given.\n"
            "- Each value cell is `{\"value\": <cell_value>, \"source_text\": "
            "<quote>}`, where the quote is copied VERBATIM from the document "
            "(≤30 words) — the table row as printed, or the sentence stating the "
            "value. ONE contiguous span, exactly as printed. Do NOT paraphrase, "
            "and do NOT stitch fragments together with an ellipsis or any other "
            "joiner (\"Ibuprofen ... 15 5\" is NOT a quote).\n"
            "- The value must appear inside its own quote, unless it is DERIVED — "
            "see below.\n"
            "- A value that is genuinely not reported for a row is "
            "`{\"value\": \"NR\", \"source_text\": \"NR\"}`. That is a correct "
            "answer, not a failure — do not invent a number, and do not borrow one "
            "from a neighbouring row.\n"
            # Without this the model has a real problem and no rule: a computed
            # sample size (15 boys + 5 girls = 20) appears nowhere in the paper,
            # so asked for a verbatim quote it invents a stitched one. Observed on
            # Polat 2005b (Aug 11 2026): every mean and SD grounded cleanly, while
            # all 64 n_arm cells failed — right numbers, unusable evidence.
            + _DERIVATION_CLAUSE
        ),
    }

    _scope = (parent_sig_def.get("review_scope") or "").strip()
    _legend = _key_column_legend(output_field_def)
    rows_desc = (
        "JSON array of row identities to fill. These rows are FIXED: return "
        "values for exactly these, in this order, without adding or removing any."
    )
    if _legend:
        # Without this the identity is uninterpretable — see _key_column_legend.
        # The per-row slot filling has always carried it; the batched path did not,
        # and returned NR for every measurement on a paper that reported them.
        rows_desc += (
            "\n\nWhat each identity column means (as defined during row discovery). "
            "A row's identity is a CLASSIFICATION of a reported measurement, not a "
            "demand for an aggregate: find the measurement in the document that "
            "belongs to this row under these definitions, and report ITS value.\n"
            + "\n".join(_legend)
        )
    rows_input = {"name": "rows_to_fill", "type": "str", "desc": rows_desc}
    return {
        "class_name": f"{parent_sig_def['class_name']}SetSlotFill",
        "docstring": (
            (_compose_scope_block(_scope) if _scope else "")
            + f"Fill the value columns of {field_name} for a given set of rows."
        ),
        "input_fields": [
            f for f in parent_sig_def.get("input_fields", [])
        ] + [rows_input],
        "output_fields": [refill_field],
    }


def _identity_missing(v: Any) -> bool:
    """True when a key cell carries no identity at all.

    **NA is not missing.** On a wide composite key most rows do not apply to
    every dimension — a global-assessment row has no adverse effect — and the
    model correctly writes NA there. That is a value: it says which record this
    is. Only a genuine gap (empty, or "not reported") leaves a row unidentified.

    This is exactly the distinction `utils/absence.py` exists to draw, and
    collapsing it is what broke the recall audit: `is_absent` is true for NA and
    NR alike, so the gate rejected 52 correctly-formed rows across three live
    runs — 16 of 16 proposals on job 05f205c7 and again on 3ba0c293, every one
    for an `adverse_effect` of "NA".
    """
    if v is None or not str(v).strip():
        return True
    return absence.classify(v) == absence.NOT_REPORTED


def _quote_mentions_identity(quote: str, values: Dict[str, Any]) -> bool:
    """Does the quote name at least ONE of this row's identity values?

    A deliberately permissive floor, not a proof. Requiring EVERY anchor to
    appear would reject legitimate table-row quotes, where some of the identity
    comes from the column header rather than the row itself. Requiring at least
    one rejects the case this exists for: a genuine quote about a different arm
    or timepoint, cited to justify a row assembled from values that appear only
    separately in the paper.

    Matching is loose on purpose — option values are often normalisations
    (`6_months` in the schema, "6 months" in the paper).
    """
    def _loose(s: Any) -> str:
        # Hyphens travel with the underscore for the same reason: the schema and
        # the paper spell the same dose differently. "Ibuprofen 100 mg/
        # paracetamol 250 mg" is what the form says; "ibuprofen 100-mg/
        # paracetamol 250-mg" is what the paper printed. That one character was
        # the only thing between the audit's real rows and acceptance once the
        # NA gate was fixed — verified against job 3ba0c293's proposals.
        return " ".join(str(s).replace("_", " ").replace("-", " ").split()).lower()

    hay = _loose(quote)
    if not hay:
        return False
    for v in values.values():
        # An absent key value proves nothing, and letting it match would gut
        # this check: "NA" is two characters and appears inside *analgesia*,
        # *nausea*, *management*. Once NA became a legal identity value, any row
        # carrying one would satisfy the quote test for free — the exact
        # fabricated-row case this function exists to catch. Identity must be
        # evidenced by a value the paper actually states.
        if absence.is_absent(v):
            continue
        needle = _loose(v)
        # Skip values too short to be evidence of anything (a 1-char arm code
        # would match almost any sentence).
        if len(needle) >= 2 and needle in hay:
            return True
    return False


def _reconcile_record_set(
    candidate_rows: List[dict],
    proposed: Any,
    key_cols: List[str],
    markdown: str,
) -> Tuple[List[dict], Dict[str, Any]]:
    """Merge verified checker additions into the candidate plan. Pure Python.

    Returns (rows, report). Rejections are counted by reason so a checker that
    is guessing shows up in the logs rather than silently padding the table.
    """
    report = {"proposed": 0, "accepted": 0, "already_present": 0,
              "incomplete_identity": 0, "no_quote": 0, "quote_unverified": 0,
              "quote_unrelated": 0}

    if not isinstance(proposed, list) or not proposed or not key_cols:
        return candidate_rows, report

    # Build the source index once — only when there is something to verify.
    index = None
    try:
        from utils.source_linker import build_source_index, locate_source, parse_page_boundaries
        index = build_source_index(markdown, parse_page_boundaries(markdown))
    except Exception:
        logger.warning("[recall-audit] source_linker unavailable — additions cannot be "
                       "verified, so none will be accepted", exc_info=True)
        return candidate_rows, report

    seen = {_canonical_record_key(r, key_cols) for r in candidate_rows}
    accepted: List[dict] = []

    for item in proposed:
        report["proposed"] += 1
        if isinstance(item, str):
            try:
                item = json.loads(item)
            except (json.JSONDecodeError, TypeError):
                report["incomplete_identity"] += 1
                continue
        if not isinstance(item, dict):
            report["incomplete_identity"] += 1
            continue

        def _plain(v):
            return v.get("value") if isinstance(v, dict) else v

        # Every identity column must carry a value — but "not applicable" IS a
        # value (see _identity_missing). Rejecting NA here rejected every row a
        # wide composite key produces, because few rows apply to every column.
        values = {a: _plain(item.get(a)) for a in key_cols}
        if any(_identity_missing(v) for v in values.values()):
            report["incomplete_identity"] += 1
            continue

        quote = _plain(item.get(_EVIDENCE_KEY))
        quote = str(quote or "").strip()
        if not quote or _is_not_reported(quote):
            report["no_quote"] += 1
            continue

        loc = locate_source(quote, index, threshold=_RECALL_AUDIT_QUOTE_THRESHOLD)
        if loc is None or getattr(loc, "confidence", 0) < _RECALL_AUDIT_QUOTE_THRESHOLD:
            report["quote_unverified"] += 1
            logger.info("[recall-audit] rejected addition %s — quote not found in paper: %r",
                        values, quote[:80])
            continue

        # Verifying the quote proves only that the TEXT exists — not that it
        # supports THIS row. A real quote about a different arm would otherwise
        # sail through, and the model could assemble a Cartesian row from anchor
        # values that appear separately. Require the quote to name at least one
        # of this row's identity values; the prompt carries the rest of the
        # burden (the quote must support the whole identity), because "does this
        # sentence support this tuple" is not decidable in Python.
        if not _quote_mentions_identity(quote, values):
            report["quote_unrelated"] += 1
            logger.info(
                "[recall-audit] rejected addition %s — quote is real but names none of "
                "the row's identity values: %r", values, quote[:80],
            )
            continue

        key = _canonical_record_key(values, key_cols)
        if key in seen:
            report["already_present"] += 1
            continue
        seen.add(key)

        # Same cell shape record discovery emits, so the merge downstream is unchanged.
        accepted.append({a: {"value": values[a], "source_text": quote} for a in key_cols})
        report["accepted"] += 1

    if accepted:
        logger.info("[recall-audit] record set %d → %d records (%s)",
                    len(candidate_rows), len(candidate_rows) + len(accepted), report)
    return candidate_rows + accepted, report


def build_keyed_extractor_class(
    parent_sig_def: dict,
    output_field_def: dict,
    task_name: str = "runtime",
) -> Type[dspy.Module]:
    """Build a composite dspy.Module for keyed table extraction.

    Four operations, all sharing one cached paper prefix:

      record discovery   1 call, key columns only     → candidate record set
      recall audit       1 call, "what did that miss?" → frozen record set
      slot filling       set-at-a-time by default, ceil(n/k) calls; falls back
                         to row-at-a-time if a set call fails twice
      refill             targeted re-extraction of records that came back empty

    Every record is identified by its composite key, so no operation ever
    depends on positional alignment with another.
    """
    field_name = output_field_def["name"]
    key_cols: List[str] = field_key_columns(output_field_def)
    key_set = set(key_cols)
    attr_col_defs = [
        sf for sf in (output_field_def.get("subform_fields") or [])
        if sf.get("field_name") not in key_set
    ]
    value_col_names = [sf["field_name"] for sf in attr_col_defs]

    discovery_def = _build_record_discovery_sig_def(parent_sig_def, output_field_def)
    row_fill_def = _build_row_slot_fill_sig_def(parent_sig_def, output_field_def, attr_col_defs)

    discovery_cls = build_signature_class(discovery_def, task_name)
    row_fill_cls = build_signature_class(row_fill_def, task_name)

    # Recall auditor (Phase 2). Needs anchors to have anything to audit; the
    # keyed branch guarantees them, but stay defensive since this builder is
    # also called directly from tests.
    recall_audit_cls = None
    if key_cols:
        recall_audit_cls = build_signature_class(
            _build_recall_audit_sig_def(parent_sig_def, output_field_def), task_name
        )

    # Batched repair (Phase 3): refills frozen records whose values all failed.
    set_fill_cls = None
    if key_cols and attr_col_defs:
        set_fill_cls = build_signature_class(
            _build_set_slot_fill_sig_def(parent_sig_def, output_field_def, attr_col_defs),
            task_name,
        )

    context_fields = [
        f["name"] for f in parent_sig_def.get("input_fields", [])
        if f.get("name") and f["name"] != "markdown_content"
    ]
    col_options = {
        sf["field_name"]: sf["options"]
        for sf in (output_field_def.get("subform_fields") or [])
        if sf.get("options")
    }

    class _TwoStageExtractor(dspy.Module):
        _is_keyed_pipeline: bool = True
        _field_name: str = field_name
        _key_cols: List[str] = key_cols
        _attr_cols: List[str] = value_col_names
        _context_fields: List[str] = context_fields
        _col_options: Dict[str, list] = col_options
        _record_discovery_class: Type[dspy.Signature] = discovery_cls
        _row_slot_fill_class: Type[dspy.Signature] = row_fill_cls
        _recall_audit_class: Optional[Type[dspy.Signature]] = recall_audit_cls
        # One batched value signature, used for BOTH the main Phase 4 pass and
        # Phase 3's repair — the task is identical (fill values for known rows),
        # so a second signature would only be a second thing to keep in sync.
        _set_slot_fill_class: Optional[Type[dspy.Signature]] = set_fill_cls

        def __init__(self):
            super().__init__()
            self.record_discovery = dspy.ChainOfThought(self.__class__._record_discovery_class)
            self.row_slot_filler = dspy.ChainOfThought(self.__class__._row_slot_fill_class)
            self.recall_auditor = (
                dspy.ChainOfThought(self.__class__._recall_audit_class)
                if self.__class__._recall_audit_class is not None else None
            )
            self.set_slot_filler = (
                dspy.ChainOfThought(self.__class__._set_slot_fill_class)
                if self.__class__._set_slot_fill_class is not None else None
            )

        async def _fill_slots_row(self, markdown_content, anchor_row, ctx_kwargs, dbg=None, tag="", row_idx=-1):
            """Per-row value extraction — one call, one row.

            Kept after Phase 4 made the batched pass the default: it is the
            fallback for rows a batch could not fill, and the most robust shape
            there is (tiny output, single job).
            """
            fname = self.__class__._field_name
            vcs = self.__class__._attr_cols
            row_anchor_json = json.dumps(anchor_row, ensure_ascii=False)
            if dbg:
                dbg.debug("[slotfill-row-start] paper=%r row=%d anchor=%r", tag, row_idx, row_anchor_json[:300])
            try:
                out = await async_dspy_forward(
                    self.row_slot_filler,
                    markdown_content=markdown_content,
                    row_anchor=row_anchor_json,
                    **ctx_kwargs,
                )
                if dbg:
                    dbg.debug("[slotfill-row-raw] paper=%r row=%d repr=%r", tag, row_idx, repr(out)[:6000])
                return {
                    col: _coerce_value_cell(
                        out.get(col), col, fname, self.__class__._col_options.get(col)
                    )
                    for col in vcs
                }
            except Exception as exc:
                logger.error("Slot fill (row-at-a-time) failed for %s record %d: %s",
                             fname, row_idx, exc, exc_info=True)
                if dbg:
                    dbg.debug("[slotfill-row-exception] row=%d %s: %s", row_idx, type(exc).__name__, exc)
                err = f"{type(exc).__name__}: {exc}"
                return {col: absence.failure_envelope(absence.ERROR, err) for col in vcs}

        async def _fill_slots_row_warmed(self, markdown_content, records, idxs, ctx_kwargs, dbg=None, tag=""):
            """Per-row calls with the prompt cache warmed first. Returns {idx: vals}.

            The first call runs ALONE. Anthropic only populates a cache entry once
            a response has begun, so firing every row through `asyncio.gather`
            makes all of them miss and pay the 1.25× cache-write rate — observed
            on job e5ecbfe5, where eight fallback calls each logged
            `prompt_cache write=25513 read=0`. Per-row calls also use their own
            signature, so their cached prefix is separate from record discovery's and has
            to be warmed on its own.
            """
            if not idxs:
                return {}
            out = {idxs[0]: await self._fill_slots_row(
                markdown_content, records[idxs[0]], ctx_kwargs, dbg, tag, idxs[0]
            )}
            if len(idxs) > 1:
                rest = await asyncio.gather(*[
                    self._fill_slots_row(markdown_content, records[i], ctx_kwargs, dbg, tag, i)
                    for i in idxs[1:]
                ])
                out.update(dict(zip(idxs[1:], rest)))
            return out

        async def _fill_slots_set(self, markdown_content, records, idxs, ctx_kwargs, dbg=None):
            """Batched value extraction, retried ONCE if it comes back with nothing.

            Measured Aug 11 2026: 16 batch calls, 1 returned no answer field —
            and the identical call succeeded on all 15 retries, across 10 papers,
            batch sizes 2-9. Paper size, row count and truncation were all
            excluded as causes (zero truncation flags; reasoning runs ~1k chars
            against a 64k ceiling), which leaves an infrequent bad completion —
            the one failure mode a retry actually fixes.

            Without this, one empty batch costs the whole per-row fan-out: the
            production case burned 12 calls for 8 rows where per-row alone costs
            9. With it, the same failure costs 2.
            """
            got = await self._fill_slots_set_once(
                markdown_content, records, idxs, ctx_kwargs, dbg
            )
            if got or not idxs:
                return got

            logger.warning(
                "[slot-fill] %s: set-at-a-time call returned no usable records for %d record(s) — retrying "
                "once before falling back to per-row calls",
                self.__class__._field_name, len(idxs),
            )
            got = await self._fill_slots_set_once(
                markdown_content, records, idxs, ctx_kwargs, dbg
            )
            if got:
                logger.info("[slot-fill] %s: retry recovered %d record(s)",
                            self.__class__._field_name, len(got))
                return got

            # ESCALATE, don't repeat again. A second identical failure means the
            # cause is the input, not luck — job 4c23c525 failed twice the same
            # way on an under-specified row identity, and repair then re-ran the
            # same batch shape for a third identical failure before per-row
            # finally did the work. Per-row uses a different signature and a much
            # smaller prompt, and it succeeded on both occasions, so go there now.
            logger.error(
                "[slot-fill] %s: set-at-a-time call failed twice for %d record(s) — escalating straight to "
                "per-row calls rather than repeating the same shape",
                self.__class__._field_name, len(idxs),
            )
            return await self._fill_slots_row_warmed(
                markdown_content, records, idxs, ctx_kwargs, dbg
            )

        async def _fill_slots_set_once(self, markdown_content, records, idxs, ctx_kwargs, dbg=None):
            """One batched value call — the attempt `_fill_slots_set` may repeat.

            Returns {row_index: row_vals} for whatever came back, matched by row
            IDENTITY rather than position: a model that reorders or omits a row
            would otherwise shift every later row's values onto the wrong row.
            Rows absent from the response are simply not in the returned dict —
            the plan-vs-output check picks them up and repairs them, which is why
            a truncated batch costs calls rather than data.
            """
            fname = self.__class__._field_name
            acs = self.__class__._key_cols
            vcs = self.__class__._attr_cols
            payload = [records[i] for i in idxs]
            try:
                out = await async_dspy_forward(
                    self.set_slot_filler,
                    markdown_content=markdown_content,
                    rows_to_fill=json.dumps(payload, ensure_ascii=False),
                    **ctx_kwargs,
                )
                if dbg:
                    dbg.debug("[slotfill-set-raw] n=%d repr=%r", len(idxs), repr(out)[:6000])
                # `out` is a dspy.Prediction, which subclasses Example — NOT a
                # dict. `isinstance(out, dict)` was therefore always False and
                # this read returned None no matter what the model produced.
                # That single wrong guard is why the batch "never worked in
                # production" (4/4 jobs), why the retry failed identically, and
                # why the harness scripts "could not reproduce" it — they use
                # hasattr(x, "get"), which is correct. The tests missed it because
                # their mocks return plain dicts, where the isinstance holds.
                # record discovery reads its field unguarded and has always been fine.
                filled = out.get("filled_rows") if hasattr(out, "get") else None
                if isinstance(filled, str):
                    filled = json.loads(filled)
                if not isinstance(filled, list):
                    # Check truncation BEFORE bailing out. This used to return
                    # first, so when the field came back None — the failure
                    # actually seen in production (job e5ecbfe5) — the one log
                    # line said "returned NoneType" and nothing about WHY.
                    # ChainOfThought emits `reasoning` before `filled_rows`, so a
                    # long reasoning that exhausts max_tokens leaves the answer
                    # field never written, which looks exactly like this.
                    _trunc = was_truncated(self.set_slot_filler)
                    logger.warning(
                        "Slot fill (set-at-a-time) for %s returned %s, not a list "
                        "(truncated=%s, keys=%s)",
                        fname, type(filled).__name__, _trunc,
                        list(out.keys()) if hasattr(out, "keys") else None,
                    )
                    if _trunc:
                        logger.error(
                            "Slot fill (set-at-a-time) for %s hit max_tokens before emitting "
                            "filled_rows — %d rows asked. Reduce the batch or the "
                            "reasoning budget.", fname, len(idxs),
                        )
                    # The reasoning text says what the model thought it was doing;
                    # without it a missing answer field is undiagnosable.
                    _reason = out.get("reasoning") if hasattr(out, "get") else None
                    if _reason:
                        logger.warning("Slot fill (set-at-a-time) for %s reasoning was: %s",
                                       fname, str(_reason)[:1200])
                    # The one fact still missing: what came back over the WIRE.
                    # `out` is DSPy's parsed Prediction, so a None field cannot
                    # distinguish "the model never emitted the array" from "the
                    # model emitted it and the adapter failed to parse it" —
                    # which need opposite fixes. DSPy has been seen logging
                    # "Failed to use structured output format, falling back to
                    # JSON mode" on this path, which makes the second more
                    # likely, but two wrong diagnoses have already come from
                    # reasoning about this instead of reading it.
                    try:
                        _lm = getattr(self.set_slot_filler, "lm", None) or dspy.settings.lm
                        _hist = getattr(_lm, "history", None) or []
                        if _hist:
                            _last = _hist[-1]
                            _outs = _last.get("outputs") if isinstance(_last, dict) else None
                            _text = _outs[0] if isinstance(_outs, list) and _outs else _outs
                            logger.warning(
                                "Slot fill (set-at-a-time) for %s RAW completion (%d chars): %s",
                                fname, len(str(_text or "")), str(_text or "")[:3000],
                            )
                    except Exception:
                        logger.debug("could not read raw completion", exc_info=True)
                    return {}
                if was_truncated(self.set_slot_filler):
                    # Rows past the cutoff are simply absent; say so plainly rather
                    # than letting them surface as unexplained missing rows.
                    logger.error(
                        "Slot fill (set-at-a-time) for %s truncated at max_tokens — %d of %d records "
                        "returned; the rest will be repaired",
                        fname, len(filled), len(idxs),
                    )

                by_key = {_canonical_record_key(records[i], acs): i for i in idxs}
                results: Dict[int, Dict[str, Any]] = {}
                unexpected = 0
                for item in filled:
                    if not isinstance(item, dict):
                        continue
                    idx = by_key.get(_canonical_record_key(item, acs))
                    if idx is None:
                        unexpected += 1
                        continue
                    results[idx] = {
                        col: _coerce_value_cell(
                            item.get(col), col, fname, self.__class__._col_options.get(col)
                        )
                        for col in vcs
                    }
                if unexpected:
                    logger.warning(
                        "Slot fill (set-at-a-time) for %s returned %d record(s) not in the frozen "
                        "plan — ignored", fname, unexpected,
                    )
                if dbg:
                    dbg.debug("[slotfill-set] asked=%d matched=%d unexpected=%d",
                              len(idxs), len(results), unexpected)
                return results
            except Exception as exc:
                logger.error("Slot fill (set-at-a-time) failed for %s (%d rows): %s",
                             fname, len(idxs), exc, exc_info=True)
                if dbg:
                    dbg.debug("[slotfill-set-exception] %s: %s", type(exc).__name__, exc)
                return {}

        async def _refill_incomplete_records(self, markdown_content, merged_rows, ctx_kwargs, dbg):
            """Plan-vs-output check, then refill only the rows that came back empty.

            A row qualifies when EVERY value cell is a failure (missing/error) — a
            deliberate NR is an answer and is left alone. Rows are refilled in
            batches so a 10-row shortfall costs one call, not ten.

            Fail-safe: any error leaves `merged_rows` as it was. Returns
            (rows, still_unfilled_count).
            """
            acs = self.__class__._key_cols
            vcs = self.__class__._attr_cols
            if self.set_slot_filler is None or not vcs:
                return merged_rows, 0

            batch_size = _records_per_call(len(vcs), self.set_slot_filler)
            rounds = 0
            while rounds < _REPAIR_MAX_ROUNDS:
                todo = [i for i, r in enumerate(merged_rows) if _record_needs_refill(r, vcs)]
                if not todo:
                    break
                rounds += 1
                logger.warning(
                    "[refill] %s: %d of %d frozen records came back with no values — "
                    "round %d, %d call(s) of up to %d rows",
                    self.__class__._field_name, len(todo), len(merged_rows), rounds,
                    -(-len(todo) // batch_size), batch_size,
                )

                async def _run_group(idxs):
                    payload = [
                        {a: self._unwrap_key_value(merged_rows[i].get(a)) for a in acs}
                        for i in idxs
                    ]
                    try:
                        out = await async_dspy_forward(
                            self.set_slot_filler,
                            markdown_content=markdown_content,
                            rows_to_fill=json.dumps(payload, ensure_ascii=False),
                            **ctx_kwargs,
                        )
                        # dspy.Prediction is not a dict — see _fill_slots_set_once.
                        filled = out.get("filled_rows") if hasattr(out, "get") else None
                        if isinstance(filled, str):
                            filled = json.loads(filled)
                        return filled if isinstance(filled, list) else []
                    except Exception as exc:
                        logger.warning("[refill] set-at-a-time call failed for %s: %s",
                                       self.__class__._field_name, exc, exc_info=True)
                        return []

                batches = [todo[i:i + batch_size] for i in range(0, len(todo), batch_size)]
                results = await asyncio.gather(*[_run_group(b) for b in batches])

                # Match returned rows back by identity, never by position: a model
                # that reorders or drops one would otherwise shift every later row
                # onto the wrong identity.
                applied = 0
                unexpected = 0
                by_key = {_canonical_record_key(merged_rows[i], acs): i for i in todo}
                for filled in results:
                    for item in filled:
                        if not isinstance(item, dict):
                            continue
                        key = _canonical_record_key(item, acs)
                        idx = by_key.get(key)
                        if idx is None:
                            unexpected += 1
                            continue
                        for col in vcs:
                            val = item.get(col)
                            if val is None:
                                continue
                            if isinstance(val, str):
                                try:
                                    parsed = json.loads(val)
                                    val = parsed if isinstance(parsed, dict) else {"value": parsed, "source_text": ""}
                                except (json.JSONDecodeError, TypeError):
                                    val = {"value": val, "source_text": ""}
                            if not isinstance(val, dict):
                                val = {"value": val, "source_text": ""}
                            _copts = self.__class__._col_options.get(col)
                            if _copts:
                                val = _normalize_enum_cell(val, _copts, f"{self.__class__._field_name}.{col}")
                            merged_rows[idx][col] = absence.stamp(val, _copts)
                        applied += 1

                if unexpected:
                    # Not silently accepted: the plan is authoritative, so a row
                    # the repair invented is dropped and recorded.
                    logger.warning(
                        "[refill] %s: ignored %d returned record(s) whose identity is "
                        "not in the frozen record set",
                        self.__class__._field_name, unexpected,
                    )
                if dbg:
                    dbg.debug("[refill] round=%d todo=%d applied=%d unexpected=%d",
                              rounds, len(todo), applied, unexpected)
                if applied == 0:
                    # A batch that landed nothing will land nothing again. Drop to
                    # per-row calls — tiny output, one job each — before giving up.
                    logger.warning(
                        "[refill] %s: set-at-a-time call landed nothing; falling back to %d "
                        "per-row call(s)", self.__class__._field_name, len(todo),
                    )
                    _records = {
                        i: {a: self._unwrap_key_value(merged_rows[i].get(a)) for a in acs}
                        for i in todo
                    }
                    per_row = await self._fill_slots_row_warmed(
                        markdown_content, _records, todo, ctx_kwargs, dbg,
                    )
                    for i, vals in per_row.items():
                        for col, cell in vals.items():
                            merged_rows[i][col] = cell
                    break

            still = sum(1 for r in merged_rows if _record_needs_refill(r, vcs))
            if still:
                logger.error(
                    "[refill] %s: %d record(s) still have no values after %d round(s) — "
                    "the table is incomplete and is marked partial",
                    self.__class__._field_name, still, rounds,
                )
            return merged_rows, still

        async def _audit_record_set(self, markdown_content, rows, ctx_kwargs, dbg):
            """One extra call: audit the candidate plan, keep only verified additions.

            Fail-safe — every failure path returns the candidate plan untouched, so
            the checker can never make the table worse than record discovery left it.
            """
            if self.recall_auditor is None or not rows:
                return rows
            acs = self.__class__._key_cols
            try:
                plan = json.dumps(
                    [{a: self._unwrap_key_value(r.get(a)) for a in acs} for r in rows],
                    ensure_ascii=False,
                )
                out = await async_dspy_forward(
                    self.recall_auditor,
                    markdown_content=markdown_content,
                    candidate_row_plan=plan,
                    **ctx_kwargs,
                )
                # Same bug as _fill_slots_set_once: dspy.Prediction is not a dict, so
                # this always read None — the recall auditor has been paying for
                # one LLM call per table field per paper and discarding the answer
                # since it shipped. Every "[recall-audit] proposed nothing" was this.
                proposed = out.get("missing_rows") if hasattr(out, "get") else None
                if isinstance(proposed, str):
                    try:
                        proposed = json.loads(proposed)
                    except (json.JSONDecodeError, TypeError):
                        proposed = None
                if dbg:
                    dbg.debug("[recall-audit-raw] proposed=%r", repr(proposed)[:2000])
                merged, report = _reconcile_record_set(
                    rows, proposed, acs, markdown_content
                )
                if report["proposed"]:
                    logger.info(
                        "[recall-audit] %s: %d proposed, %d accepted, rejected "
                        "(present=%d incomplete=%d no_quote=%d unverified=%d "
                        "unrelated=%d)",
                        self.__class__._field_name, report["proposed"], report["accepted"],
                        report["already_present"], report["incomplete_identity"],
                        report["no_quote"], report["quote_unverified"],
                        report["quote_unrelated"],
                    )
                return merged
            except Exception as exc:
                # A checker failure must never cost us record discovery's work.
                logger.warning(
                    "[recall-audit] check failed for %s (%s) — keeping the candidate plan",
                    self.__class__._field_name, exc, exc_info=True,
                )
                return rows

        @staticmethod
        def _unwrap_key_value(v: Any) -> Any:
            """Unpack source-grounded dicts so row_anchor JSON is clean strings."""
            if isinstance(v, dict):
                return v.get("value", v)
            return v

        async def __call__(self, markdown_content: str, **kwargs) -> Dict[str, Any]:
            fn = self.__class__._field_name
            acs = self.__class__._key_cols
            vcs = self.__class__._attr_cols

            # Context inputs from depends_on — the stage signatures declare
            # them, so they must actually be supplied or the prompt describes
            # an input that is silently absent from the message.
            ctx_kwargs: Dict[str, Any] = {}
            for ctx_field in self.__class__._context_fields:
                ctx_kwargs[ctx_field] = _format_context_value(
                    kwargs.get(ctx_field, _UPSTREAM_UNAVAILABLE)
                )

            # Short fingerprint so log lines can be traced back to a specific paper.
            _tag = ""
            try:
                for _ln in (markdown_content or "").splitlines():
                    _s = _ln.strip().lstrip("# ").strip()
                    if len(_s) > 8:
                        _tag = _s[:70]
                        break
            except Exception:
                _tag = ""

            _dbg = None
            if _DEBUG_KEYED:
                try:
                    _dbg = _get_keyed_logger()
                    _dbg.debug("=" * 80)
                    _dbg.debug("[discover-start] paper=%r  md_chars=%d  field=%s  key=%s  attrs=%s", _tag, len(markdown_content or ""), fn, acs, vcs)
                    try:
                        _sig = getattr(self.record_discovery, "signature", None) or getattr(self.record_discovery, "extended_signature", None)
                        _dbg.debug("[discover-sig] instructions=%r", repr(getattr(_sig, "instructions", "N/A"))[:500])
                        _dbg.debug("[discover-sig] output_fields=%s", list(getattr(_sig, "output_fields", {}).keys()))
                    except Exception as _se:
                        _dbg.debug("[discover-sig] could not inspect signature: %s", _se)
                except Exception as _le:
                    logger.warning("Keyed-pipeline debug logger init failed: %s", _le)
                    _dbg = None

            # ── record discovery ───────────────────────────────────────────────────
            # Bound before the try so the final-envelope read below can never
            # become an UnboundLocalError if this handler stops re-raising.
            _s1_truncated = False
            try:
                s1_out = await async_dspy_forward(
                    self.record_discovery, markdown_content=markdown_content, **ctx_kwargs
                )
                # record discovery defines the row set for the whole field, so a cutoff
                # here silently shrinks the final table no matter how well
                # slot filling performs. Carried to the final envelope below.
                _s1_truncated = was_truncated(self.record_discovery)
                if _s1_truncated:
                    logger.error(
                        "Record discovery %s: row discovery truncated at max_tokens — "
                        "the row set is incomplete, so the table will be missing rows.",
                        fn,
                    )

                if _dbg:
                    _dbg.debug("[discover-raw-out] type=%s  repr=%r", type(s1_out).__name__, repr(s1_out)[:2000])
                    _raw_fn = s1_out.get(fn, "__MISSING__")
                    _dbg.debug("[discover-raw-field] field=%s  type=%s  repr=%r", fn, type(_raw_fn).__name__, repr(_raw_fn)[:2000])

                rows: List[Dict] = s1_out.get(fn, []) or []
                if isinstance(rows, str):
                    if _dbg:
                        _dbg.debug("[discover-parse] rows is string, attempting json.loads")
                    try:
                        rows = json.loads(rows)
                        if not isinstance(rows, list):
                            logger.warning("Record discovery %s: json.loads gave %s not list — dropping", fn, type(rows).__name__)
                            rows = []
                    except (json.JSONDecodeError, TypeError) as _e:
                        logger.warning("Record discovery %s: json.loads failed: %s", fn, _e)
                        rows = []
                elif isinstance(rows, dict):
                    # LLM returned {"row_1":{...}} or {"rows":[...]} instead of a list.
                    # Unwrap single-key list wrapper first; fall back to dict values.
                    _dict_vals = list(rows.values())
                    if len(_dict_vals) == 1 and isinstance(_dict_vals[0], list):
                        rows = _dict_vals[0]
                    else:
                        rows = _dict_vals
                    if _dbg:
                        _dbg.debug("[discover-parse] rows was dict, converted to list len=%d", len(rows))

                # Normalize individual rows that DSPy may return as JSON strings
                normalized: List[Dict] = []
                for _row in rows:
                    if isinstance(_row, dict):
                        normalized.append(_row)
                    elif isinstance(_row, str):
                        try:
                            _parsed = json.loads(_row)
                            if isinstance(_parsed, dict):
                                normalized.append(_parsed)
                            else:
                                logger.warning("Record discovery %s: row string parsed to %s, dropping", fn, type(_parsed).__name__)
                        except (json.JSONDecodeError, TypeError) as _e:
                            logger.warning("Record discovery %s: row string json.loads failed: %s", fn, _e)
                    elif isinstance(_row, list):
                        # Nested list — flatten one level
                        for _inner in _row:
                            if isinstance(_inner, dict):
                                normalized.append(_inner)
                    else:
                        logger.warning("Record discovery %s: unexpected row type %s, dropping", fn, type(_row).__name__)
                rows = normalized

                if _dbg:
                    _dbg.debug("[discover-normalised] len(rows)=%d  first_row=%r", len(rows), repr(rows[0])[:500] if rows else "N/A")

            except Exception as exc:
                logger.error("Record discovery failed for %s: %s", fn, exc, exc_info=True)
                if _dbg:
                    _dbg.debug("[discover-exception] %s: %s", type(exc).__name__, exc)
                # Surface the failure to the pipeline retry machinery instead
                # of masking it as an empty-but-successful extraction.
                raise

            if not rows:
                if _dbg:
                    _dbg.debug("[discover-empty] rows is empty after normalization — explicit not_reported")
                # record discovery completed and found no rows: a genuine "not reported",
                # expressed in the same envelope shape single-call extraction
                # uses — an ambiguous bare [] would read as a silent failure and
                # burn pipeline retries on papers that just lack the table.
                return {
                    fn: {
                        "value": absence.NR_LABEL,
                        "source_text": absence.NR_LABEL,
                        "status": absence.NOT_REPORTED,
                    }
                }

            # Build clean per-row anchor dicts (unwrap source-grounded values)
            records = [
                {k: self._unwrap_key_value(row.get(k)) for k in acs}
                for row in rows
            ]

            # Dedup identical anchor rows — a duplicated Stage-1 row would fire
            # a duplicate Stage-2 call and produce a duplicate result row.
            _seen_record_keys: set = set()
            _dedup_rows: List[Dict] = []
            _dedup_records: List[Dict] = []
            for _row, _key_row in zip(rows, records):
                _key = json.dumps(_key_row, sort_keys=True, ensure_ascii=False, default=str)
                if _key in _seen_record_keys:
                    continue
                _seen_record_keys.add(_key)
                _dedup_rows.append(_row)
                _dedup_records.append(_key_row)
            rows, records = _dedup_rows, _dedup_records

            if _dbg:
                _dbg.debug("[discover-keys] len=%d  first=%r", len(records), repr(records[0])[:300] if records else "N/A")

            # ── Recall audit — audit the candidate plan before locking it ──
            # One extra call, asking what record discovery MISSED (not "list them again").
            # Additions are accepted only when their quote resolves in the paper.
            _s1_count = len(rows)
            rows = await self._audit_record_set(markdown_content, rows, ctx_kwargs, _dbg)
            # Additions carry anchors only, so recompute rather than keeping two
            # lists in step.
            records = [
                {k: self._unwrap_key_value(row.get(k)) for k in acs} for row in rows
            ]

            # ── ROW PLAN LOCKED ───────────────────────────────────────────
            # The row set is now authoritative: slot filling fills values for exactly
            # these rows and may neither invent nor drop any.
            if _dbg:
                _dbg.debug("[recordset-frozen] record_discovery=%d  locked=%d  keys=%r",
                           _s1_count, len(rows),
                           [_canonical_record_key(r, acs) for r in records][:6])

            # ── slot filling — batched value extraction ───────────────────────
            # One call carries many rows: the frozen record set goes in as an input, so
            # this call fills values and cannot rediscover rows. A 12-row table
            # costs ~1 call here instead of 12. Rows a batch drops (truncation,
            # omission, a failed call) are picked up by the plan-vs-output check
            # below and refilled — so batching trades calls for repair risk, never
            # for silent data loss.
            # DEFAULT IS PER-ROW. The batched pass is opt-in via
            # EXTRACTION_BATCH_VALUES=1 because on its first real production run
            # (job e5ecbfe5, 8 rows, Aug 11 2026) it returned `filled_rows` as
            # None twice, fell through to the per-row fallback, and cost 12 calls
            # where the old path cost 9 — a cost regression, not data loss. The
            # mocked tests could not catch it: they returned the shape this code
            # asked for, which is exactly the assumption in question. Re-enable
            # only once a real run is observed returning rows.
            _s2_map: Dict[int, Dict[str, Any]] = {}
            if records and self.set_slot_filler is not None and _SET_AT_A_TIME:
                batch_size = _records_per_call(len(vcs), self.set_slot_filler)
                batches = [
                    list(range(i, min(i + batch_size, len(records))))
                    for i in range(0, len(records), batch_size)
                ]
                logger.info(
                    "[slot-fill] %s: %d record(s) → %d set-at-a-time call(s) of up to %d",
                    fn, len(records), len(batches), batch_size,
                )
                # Warm the prompt cache with the first batch alone, then fan out.
                # Anthropic populates a cache entry only once a response begins, so
                # firing every batch via gather makes them all miss and pay the
                # 1.25× cache-write rate; the first batch alone lets the rest read
                # the warm prefix at 0.1×.
                _s2_map.update(
                    await self._fill_slots_set(markdown_content, records, batches[0], ctx_kwargs, _dbg)
                )
                if len(batches) > 1:
                    rest = await asyncio.gather(*[
                        self._fill_slots_set(markdown_content, records, b, ctx_kwargs, _dbg)
                        for b in batches[1:]
                    ])
                    for part in rest:
                        _s2_map.update(part)
            elif records:
                logger.info("[slot-fill] %s: %d row-at-a-time call(s)", fn, len(records))
                _s2_map = await self._fill_slots_row_warmed(
                    markdown_content, records, list(range(len(records))),
                    ctx_kwargs, _dbg, _tag,
                )

            s2_results = sorted(_s2_map.items(), key=lambda x: x[0])

            # ── Merge key cols + value cols into full row dicts ─────────
            # Keyed by index, not positional indexing: a short s2_results used to
            # raise IndexError and lose the whole field. Now a genuinely absent
            # slot filling result becomes a repairable row instead of a crash.
            _s2_by_idx = dict(s2_results)
            merged_rows: List[Dict] = []
            for i, row in enumerate(rows):
                row_vals = _s2_by_idx.get(
                    i, {col: absence.failure_envelope(absence.MISSING) for col in vcs}
                )
                merged: Dict[str, Any] = {}
                for cell_key, cell_val in row.items():
                    # Anchor cells get the same envelope + status stamp value
                    # cells receive, so every cell downstream has one shape.
                    if not isinstance(cell_val, dict):
                        cell_val = {"value": cell_val, "source_text": ""}
                    else:
                        cell_val = dict(cell_val)
                    _copts = self.__class__._col_options.get(cell_key)
                    if _copts:
                        cell_val = _normalize_enum_cell(cell_val, _copts, f"{fn}.{cell_key}")
                    if absence.normalize_status(cell_val.get("status")) is None:
                        cell_val = absence.stamp(cell_val, _copts)
                    merged[cell_key] = cell_val
                merged.update(row_vals)
                merged_rows.append(merged)

            if _dbg:
                _dbg.debug("[MERGE] len(merged_rows)=%d  first=%r", len(merged_rows), repr(merged_rows[0])[:500] if merged_rows else "N/A")

            # ── Plan vs output — every frozen record must have come back filled ──
            merged_rows, _unfilled = await self._refill_incomplete_records(
                markdown_content, merged_rows, ctx_kwargs, _dbg
            )

            # Same envelope shape as single-call table extraction, so every
            # downstream consumer sees ONE format regardless of strategy.
            _env: Dict[str, Any] = {
                "value": merged_rows,
                "source_text": "",
                "status": absence.REPORTED,
            }
            if _unfilled:
                # Rows exist but carry no values after repair. A reviewer must not
                # see that presented as a complete answer.
                _env["status"] = absence.PARTIAL
                _env["unfilled_rows"] = _unfilled
            if _s1_truncated:
                # Rows are real but the set is incomplete — same marking the
                # single-call path applies, so consumers need one rule.
                _env["status"] = absence.PARTIAL
                _env["truncated"] = True
            return {fn: _env}

    _TwoStageExtractor.__name__ = f"AsyncTwoStage_{parent_sig_def['class_name']}_Extractor"
    _TwoStageExtractor.__qualname__ = _TwoStageExtractor.__name__
    return _TwoStageExtractor


# ---------------------------------------------------------------------------
# Full schema builder (returns extractor_factories dict)
# ---------------------------------------------------------------------------

def build_schema_classes(
    schema_def: dict,
    task_name: Optional[str] = None,
) -> Dict[str, Type[dspy.Module]]:
    """Build the complete extractor_factories dict from a schema_def JSON blob.

    Returns {sig_class_name: ExtractorClass} — the same structure that
    DynamicSchemaConfig._build_staged_pipeline currently builds via importlib.
    """
    _task_name = task_name or schema_def.get("task_name", "runtime")
    fallback_structures: Dict[str, Dict] = schema_def.get("fallback_structures", {})

    # Build requires_fields map from pipeline stages
    sig_to_requires: Dict[str, List[str]] = {}
    for stage in schema_def.get("pipeline_stages", []):
        rf = stage.get("requires_fields", [])
        for sig_name in stage.get("signatures", []):
            sig_to_requires[sig_name] = rf

    # Form-level switch, set via PATCH /forms/{id}/fields (table_extraction_mode).
    # Kept as a back-compat fallback for forms saved before per-field strategy
    # existed — it forces every table field on the form into agentic. New forms
    # set extraction_strategy on each table field instead (see the loop below).
    # Lives on schema_def rather than forms.metadata because this function only
    # ever sees schema_def (get_schema() reads the `schemas` table, not `forms`).
    _form_agentic = resolve_strategy(schema_def.get("table_extraction_mode")) == AGENTIC

    extractor_factories: Dict[str, Type[dspy.Module]] = {}
    for sig_def in schema_def.get("signatures", []):
        sig_name = sig_def["class_name"]

        # ── Agentic branch (per-field opt-in, per-form fallback) ─────────
        # A table field routes to the Claude Agent SDK extractor when its own
        # extraction_strategy is "agentic". The legacy form-level fallback only
        # applies when the field carries NO strategy at all (forms saved before
        # per-field strategy existed) — an explicit single_call/row_then_columns
        # choice always wins over it, otherwise a user could never opt a field
        # back out of agentic on a form that was once switched to it. Scalar
        # signatures are untouched: they already score 0.96-0.99 and cost far
        # less on DSPy. Same single-output-field restriction as keyed, for
        # the same reason — a mixed table+scalar signature has no single
        # envelope to hand the agent.
        agentic_field: Optional[dict] = None
        if len(sig_def.get("output_fields", [])) == 1:
            of = sig_def["output_fields"][0]
            _field_strategy = field_strategy(of)
            if of.get("subform_fields") and (
                _field_strategy == AGENTIC or (not _field_strategy and _form_agentic)
            ):
                agentic_field = of

        if agentic_field is not None:
            # Imported here, not at module scope: agentic_table imports
            # _compose_field_desc from this module, so a top-level import
            # would be circular.
            try:
                from dspy_components.agentic_table import (
                    build_agentic_table_extractor_class,
                )
            except Exception:
                logger.error(
                    "Signature %s: extraction_strategy='agentic' but the "
                    "Claude Agent SDK is unavailable — falling back to the "
                    "standard extractor for this field.",
                    sig_name, exc_info=True,
                )
            else:
                extractor_factories[sig_name] = build_agentic_table_extractor_class(
                    sig_def, agentic_field, _task_name
                )
                logger.info(
                    "Agentic table extractor for %s — field=%s, columns=%d",
                    sig_name, agentic_field["name"],
                    len(agentic_field["subform_fields"]),
                )
                continue

        # ── Two-stage branch ─────────────────────────────────────────────
        # Only activate when the signature has exactly one output field that
        # carries extraction_strategy + anchor_columns + subform_fields.
        # Mixed sigs (table + scalar outputs) fall back to single-call to
        # keep this change contained.
        keyed_field: Optional[dict] = None
        if len(sig_def.get("output_fields", [])) == 1:
            of = sig_def["output_fields"][0]
            if (
                field_strategy(of) == DISCOVER_THEN_FILL
                and field_key_columns(of)
                and of.get("subform_fields")
            ):
                keyed_field = of

        if keyed_field is None:
            _ignored = [
                of["name"] for of in sig_def.get("output_fields", [])
                if field_strategy(of) == DISCOVER_THEN_FILL
            ]
            if _ignored:
                logger.warning(
                    "Signature %s: keyed pipeline on %s is IGNORED — record discovery "
                    "requires a single-output signature with a composite key and "
                    "columns; falling back to single-pass extraction.",
                    sig_name, _ignored,
                )

        if keyed_field is not None:
            extractor_factories[sig_name] = build_keyed_extractor_class(
                sig_def, keyed_field, _task_name
            )
            logger.info(
                "Keyed extractor for %s — composite key=%s, attributes=%d",
                sig_name,
                field_key_columns(keyed_field),
                len(keyed_field["subform_fields"]) - len(field_key_columns(keyed_field)),
            )
            continue

        # ── Original single-call path (unchanged) ────────────────────────
        sig_class = build_signature_class(sig_def, _task_name)

        fallback = dict(fallback_structures.get(sig_name, {}))
        if not fallback:
            # Derive fallback from output field types
            for out in sig_def.get("output_fields", []):
                field_type = out.get("type", "Dict[str, Any]")
                fallback[out["name"]] = (
                    [] if "List" in field_type
                    else absence.failure_envelope(absence.MISSING)
                )

        requires = sig_to_requires.get(sig_name, [])
        field_options = {
            out["name"]: out["options"]
            for out in sig_def.get("output_fields", [])
            if out.get("options")
        }
        extractor_factories[sig_name] = build_extractor_class(
            sig_class, fallback, requires, field_options
        )

    # Optional signature dump for inspection — set EVISTREAM_SIGNATURE_DUMP_DIR.
    _dump_dir = os.getenv("EVISTREAM_SIGNATURE_DUMP_DIR")
    if _dump_dir:
        try:
            os.makedirs(_dump_dir, exist_ok=True)
            _schema_name = schema_def.get("task_name") or schema_def.get("schema_name") or _task_name
            _dump_path = os.path.join(_dump_dir, f"{_schema_name}_signatures.py")
            _py = render_schema_to_python(schema_def)
            with open(_dump_path, "w") as _fh:
                _fh.write(f"# Auto-generated signature dump for {_schema_name}\n")
                _fh.write("# Remove this file when no longer needed.\n\n")
                _fh.write(_py)
            logger.info("Signature dump written to %s", _dump_path)
        except Exception as _dump_err:
            logger.debug("Signature dump failed: %s", _dump_err)

    return extractor_factories


# ---------------------------------------------------------------------------
# Debug / inspection utility
# ---------------------------------------------------------------------------

def _render_sig_def_to_lines(sig_def: dict) -> list:
    """Render one sig_def dict into Python class lines."""
    class_name = sig_def["class_name"]
    doc = (sig_def.get("docstring", "") or "").replace('"""', '\\"\\"\\"')
    lines = []
    lines.append(f"class {class_name}(dspy.Signature):")
    lines.append(f'    """{doc}"""')
    lines.append("")
    for inp in sig_def.get("input_fields", []):
        desc = (inp.get("desc", "") or "").replace('"""', '\\"\\"\\"')
        lines.append(f"    {inp['name']}: {inp.get('type', 'str')} = dspy.InputField(")
        lines.append(f'        desc="""{desc}"""')
        lines.append("    )")
    for out in sig_def.get("output_fields", []):
        full_desc = _compose_field_desc(out).replace('"""', '\\"\\"\\"')
        lines.append(f"    {out['name']}: {out.get('type', 'Dict[str, Any]')} = dspy.OutputField(")
        lines.append(f'        desc="""{full_desc}"""')
        lines.append("    )")
    lines.append("")
    lines.append("")
    return lines


def render_schema_to_python(schema_def: dict) -> str:
    """Render a schema_def back to human-readable Python for debugging.

    Includes Stage1 and Stage2Row sub-signatures for keyed fields.
    Replaces 'cat signatures.py'. Does not write to disk.
    """
    lines = [
        "import dspy",
        "from typing import Dict, Any, List",
        "",
        "",
        f"# schema_name: {schema_def.get('schema_name', 'unknown')}",
        f"# task_name:   {schema_def.get('task_name', 'unknown')}",
        "",
    ]

    for sig_def in schema_def.get("signatures", []):
        # Check if this sig uses keyed
        keyed_field = None
        if len(sig_def.get("output_fields", [])) == 1:
            of = sig_def["output_fields"][0]
            if (
                field_strategy(of) == DISCOVER_THEN_FILL
                and field_key_columns(of)
                and of.get("subform_fields")
            ):
                keyed_field = of

        if keyed_field is not None:
            key_set = set(field_key_columns(keyed_field))
            attr_col_defs = [
                sf for sf in (keyed_field.get("subform_fields") or [])
                if sf.get("field_name") not in key_set
            ]
            discovery_def = _build_record_discovery_sig_def(sig_def, keyed_field)
            row_fill_def = _build_row_slot_fill_sig_def(sig_def, keyed_field, attr_col_defs)

            lines.append(f"# ── TWO-STAGE: {sig_def['class_name']} ──────────────────────────")
            lines.append(f"# record discovery: row discovery (key cols only)")
            lines.append("")
            lines.extend(_render_sig_def_to_lines(discovery_def))

            lines.append(f"# slot filling: per-row (one call per discovered row, fills all {len(attr_col_defs)} value cols)")
            lines.append("")
            lines.extend(_render_sig_def_to_lines(row_fill_def))
        else:
            lines.extend(_render_sig_def_to_lines(sig_def))

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI entry point: python -m dspy_components.runtime_builders dump <name>
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    import os

    if len(sys.argv) < 3 or sys.argv[1] != "dump":
        print("Usage: python -m dspy_components.runtime_builders dump <schema_name>")
        sys.exit(1)

    schema_name = sys.argv[2]
    try:
        from supabase import create_client
        sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])
        row = sb.table("schemas").select("schema_def").eq("schema_name", schema_name).execute()
        if not row.data:
            print(f"Schema '{schema_name}' not found in schemas table.")
            sys.exit(1)
        sdef = row.data[0].get("schema_def")
        if not sdef:
            print(f"Schema '{schema_name}' has no schema_def (not yet migrated).")
            sys.exit(1)
        print(render_schema_to_python(sdef))
    except Exception as exc:
        print(f"Error: {exc}")
        sys.exit(1)


def clear_class_cache() -> None:
    """Evict all LRU-cached signature classes (call after schema invalidation)."""
    _build_signature_class_cached.cache_clear()


__all__ = [
    "build_signature_class",
    "build_extractor_class",
    "build_keyed_extractor_class",
    "build_schema_classes",
    "render_schema_to_python",
    "clear_class_cache",
]
