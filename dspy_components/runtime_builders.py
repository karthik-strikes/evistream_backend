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
from typing import Any, Dict, List, Optional, Type

import dspy

from utils.dspy_async import async_dspy_forward

logger = logging.getLogger(__name__)

# ── Two-stage debug logging (opt-in via DEBUG_TWO_STAGE=1) ───────────────────
_DEBUG_TWO_STAGE = os.getenv("DEBUG_TWO_STAGE", "").lower() in ("1", "true", "yes")
_two_stage_debug_logger = None

def _get_two_stage_logger():
    global _two_stage_debug_logger
    if _two_stage_debug_logger is None:
        import os as _os
        _lg = logging.getLogger("two_stage_debug")
        _lg.setLevel(logging.DEBUG)
        _lg.propagate = False
        _log_path = _os.path.join(
            _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))),
            "..", "logs", "two_stage_debug.log",
        )
        _os.makedirs(_os.path.dirname(_log_path), exist_ok=True)
        _h = logging.FileHandler(_log_path, mode="a")
        _h.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
        _lg.addHandler(_h)
        _two_stage_debug_logger = _lg
    return _two_stage_debug_logger
# ─────────────────────────────────────────────────────────────────────────────

# Tokens the model uses to mean "not reported in the paper" (genuine NR).
_NR_TOKENS = {"", "NR", "NA", "N/A", "NONE", "NOT REPORTED", "NOT_REPORTED"}


def _is_not_reported(v) -> bool:
    """True when a model-returned value means a genuine 'not reported' (vs a real value)."""
    if v is None:
        return True
    if isinstance(v, str):
        return v.strip().upper() in _NR_TOKENS
    return False


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


def _example_for_prompt(ex: dict) -> dict:
    """Rendered examples must model the grounding contract. An example whose
    source_text is blank teaches the model to skip citations — show the
    placeholder contract instead. NR examples keep their literal "NR"."""
    st = ex.get("source_text", "")
    if isinstance(st, str) and not st.strip() and not _is_not_reported(ex.get("value")):
        return {**ex, "source_text": _GROUNDING_PLACEHOLDER}
    return ex


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
    rejects — reviewers see the raw value plus the flag."""
    if not options or not isinstance(cell, dict) or cell.get("status") != "reported":
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
                'genuinely does not state a cell, use NR for that cell rather than a '
                'fabricated value.'
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
                'does not state the value, return "NR" rather than a fabricated one.'
            )

    if examples:
        parts.append("\nExamples:")
        for ex in examples:
            if isinstance(ex, dict):
                parts.append(json.dumps(_example_for_prompt(ex), ensure_ascii=False))
            else:
                parts.append(str(ex))

    return "\n".join(parts)


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
                    call_kwargs[field] = _format_context_value(kwargs.get(field, "NR"))
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

            result: Dict[str, Any] = {}
            for field_name, default in fallback.items():
                if default == []:
                    raw = outputs.get(field_name, [])
                    if isinstance(raw, str):
                        # Mirror the two-stage path: DSPy sometimes hands the
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
                        # Model omitted this field → failure (blank), not a genuine NR.
                        result[field_name] = {"value": "NR", "source_text": "NR", "status": "missing"}
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
                        cell["status"] = "not_reported" if _is_not_reported(cell.get("value")) else "reported"
                        opts = (self.__class__._field_options or {}).get(field_name)
                        if opts:
                            cell = _normalize_enum_cell(cell, opts, field_name)
                        result[field_name] = cell
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

def _build_stage1_sig_def(parent_sig_def: dict, output_field_def: dict) -> dict:
    """Signature def for Stage 1: anchor columns only, no value columns.

    The parent field's prose is reshaped for row discovery:
    - `examples` are dropped — they are full rows including value columns,
      which contradicts "populate only the anchor columns";
    - rules mandating the whole-field {"value": "NR"} envelope are dropped —
      Stage 1 returns a bare array, so an empty [] is the not-reported answer;
    - the dict-envelope Source Grounding block is suppressed
      (source_grounded=False) and the array-shaped grounding contract is stated
      inline instead. Without this the prompt demands three different output
      shapes at once (bare array vs envelope dict vs NR object).
    """
    anchor_set = set(output_field_def.get("anchor_columns") or [])
    anchor_subfields = [
        sf for sf in (output_field_def.get("subform_fields") or [])
        if sf.get("field_name") in anchor_set
    ]
    stage1_field = {
        k: v for k, v in output_field_def.items()
        if k not in ("extraction_strategy", "anchor_columns", "examples")
    }
    stage1_field["subform_fields"] = anchor_subfields
    stage1_field["source_grounded"] = False

    def _demands_nr_envelope(rule: str) -> bool:
        compact = str(rule).replace(" ", "").replace("'", '"').lower()
        return '{"value":"nr"' in compact

    stage1_field["rules"] = [
        r for r in (stage1_field.get("rules") or []) if not _demands_nr_envelope(r)
    ]
    if not stage1_field["rules"]:
        stage1_field.pop("rules")

    # Stage 1 always returns a list of row dicts — override whatever the schema_def
    # has (typically "Dict[str, Any]") so DSPy validates and coerces correctly.
    stage1_field["type"] = "List[Dict[str, Any]]"
    existing_desc = stage1_field.get("description", "")
    stage1_field["description"] = (
        existing_desc + (("\n\n") if existing_desc else "") +
        "STAGE 1 — ROW DISCOVERY ONLY: Identify every distinct row. "
        "Populate only the anchor columns listed below. "
        "Do NOT fill measurement/value columns — those are extracted separately.\n\n"
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
    return {
        "class_name": f"{parent_sig_def['class_name']}Stage1",
        "docstring": f"Stage 1 row discovery for {output_field_def['name']}. Anchor columns only.",
        "input_fields": parent_sig_def.get("input_fields", []),
        "output_fields": [stage1_field],
    }


def _build_stage2_row_sig_def(
    parent_sig_def: dict, output_field_def: dict, value_col_defs: list
) -> dict:
    """Signature def for one Stage 2 per-row call.

    Inputs:  markdown_content (from parent) + row_anchor (single row JSON from Stage 1).
    Outputs: all value columns (non-anchor) for that one row.

    Each call is anchored to exactly one row — no positional alignment required.
    The row_anchor desc carries a legend of what each anchor column means —
    without it, Stage 2 would have to reverse-engineer normalized codes like
    '2h_to_24h' whose definitions live only in Stage 1's prompt. Value columns
    are always source-grounded so the verbatim-quote contract applies to the
    cells that carry the actual measurements.
    """
    anchor_set = set(output_field_def.get("anchor_columns") or [])
    anchor_col_defs = [
        sf for sf in (output_field_def.get("subform_fields") or [])
        if sf.get("field_name") in anchor_set
    ]

    legend_lines: List[str] = []
    for sf in anchor_col_defs:
        line = f"- {sf.get('field_name')}"
        cdesc = (sf.get("field_description") or "").strip()
        if cdesc:
            line += f": {cdesc}"
        opts = sf.get("options") or []
        if opts:
            line += f" (one of: {', '.join(str(o) for o in opts)})"
        legend_lines.append(line)

    anchor_example = (
        json.dumps({sf.get("field_name"): "..." for sf in anchor_col_defs}, ensure_ascii=False)
        if anchor_col_defs else '{"<anchor_column>": "..."}'
    )
    row_anchor_desc = (
        "JSON dict of anchor column values identifying this specific row, e.g. "
        f"{anchor_example}. Extract all output fields for THIS ROW ONLY."
    )
    if legend_lines:
        row_anchor_desc += (
            "\n\nAnchor column meanings (as defined during row discovery):\n"
            + "\n".join(legend_lines)
        )

    output_fields = []
    for col_def in value_col_defs:
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
            # the verbatim/substring contract is part of the Stage 2 prompt.
            "source_grounded": True,
        })
    return {
        "class_name": f"{parent_sig_def['class_name']}Stage2Row",
        "docstring": (
            "Stage 2 per-row extraction. "
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


def build_two_stage_extractor_class(
    parent_sig_def: dict,
    output_field_def: dict,
    task_name: str = "runtime",
) -> Type[dspy.Module]:
    """Build a composite dspy.Module that implements 2-stage table extraction.

    Stage 1 — 1 ChainOfThought call with anchor columns only → row list.
    Stage 2 — N parallel ChainOfThought calls, one per ROW, each receiving
              a single row_anchor JSON dict and returning all value columns
              for that row.  No positional alignment required.
    """
    field_name = output_field_def["name"]
    anchor_cols: List[str] = list(output_field_def.get("anchor_columns") or [])
    anchor_set = set(anchor_cols)
    value_col_defs = [
        sf for sf in (output_field_def.get("subform_fields") or [])
        if sf.get("field_name") not in anchor_set
    ]
    value_col_names = [sf["field_name"] for sf in value_col_defs]

    stage1_def = _build_stage1_sig_def(parent_sig_def, output_field_def)
    stage2_row_def = _build_stage2_row_sig_def(parent_sig_def, output_field_def, value_col_defs)

    stage1_cls = build_signature_class(stage1_def, task_name)
    stage2_row_cls = build_signature_class(stage2_row_def, task_name)

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
        _is_two_stage: bool = True
        _field_name: str = field_name
        _anchor_cols: List[str] = anchor_cols
        _value_cols: List[str] = value_col_names
        _context_fields: List[str] = context_fields
        _col_options: Dict[str, list] = col_options
        _stage1_class: Type[dspy.Signature] = stage1_cls
        _stage2_row_class: Type[dspy.Signature] = stage2_row_cls

        def __init__(self):
            super().__init__()
            self.stage1 = dspy.ChainOfThought(self.__class__._stage1_class)
            self.stage2_row = dspy.ChainOfThought(self.__class__._stage2_row_class)

        @staticmethod
        def _unwrap_anchor_value(v: Any) -> Any:
            """Unpack source-grounded dicts so row_anchor JSON is clean strings."""
            if isinstance(v, dict):
                return v.get("value", v)
            return v

        async def __call__(self, markdown_content: str, **kwargs) -> Dict[str, Any]:
            fn = self.__class__._field_name
            acs = self.__class__._anchor_cols
            vcs = self.__class__._value_cols

            # Context inputs from depends_on — the stage signatures declare
            # them, so they must actually be supplied or the prompt describes
            # an input that is silently absent from the message.
            ctx_kwargs: Dict[str, Any] = {}
            for ctx_field in self.__class__._context_fields:
                ctx_kwargs[ctx_field] = _format_context_value(kwargs.get(ctx_field, "NR"))

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
            if _DEBUG_TWO_STAGE:
                try:
                    _dbg = _get_two_stage_logger()
                    _dbg.debug("=" * 80)
                    _dbg.debug("[S1-START] paper=%r  md_chars=%d  field=%s  anchors=%s  value_cols=%s", _tag, len(markdown_content or ""), fn, acs, vcs)
                    try:
                        _sig = getattr(self.stage1, "signature", None) or getattr(self.stage1, "extended_signature", None)
                        _dbg.debug("[S1-SIG] instructions=%r", repr(getattr(_sig, "instructions", "N/A"))[:500])
                        _dbg.debug("[S1-SIG] output_fields=%s", list(getattr(_sig, "output_fields", {}).keys()))
                    except Exception as _se:
                        _dbg.debug("[S1-SIG] could not inspect signature: %s", _se)
                except Exception as _le:
                    logger.warning("Two-stage debug logger init failed: %s", _le)
                    _dbg = None

            # ── Stage 1 ───────────────────────────────────────────────────
            try:
                s1_out = await async_dspy_forward(
                    self.stage1, markdown_content=markdown_content, **ctx_kwargs
                )

                if _dbg:
                    _dbg.debug("[S1-RAW-OUT] type=%s  repr=%r", type(s1_out).__name__, repr(s1_out)[:2000])
                    _raw_fn = s1_out.get(fn, "__MISSING__")
                    _dbg.debug("[S1-RAW-FIELD] field=%s  type=%s  repr=%r", fn, type(_raw_fn).__name__, repr(_raw_fn)[:2000])

                rows: List[Dict] = s1_out.get(fn, []) or []
                if isinstance(rows, str):
                    if _dbg:
                        _dbg.debug("[S1-PARSE] rows is string, attempting json.loads")
                    try:
                        rows = json.loads(rows)
                        if not isinstance(rows, list):
                            logger.warning("Two-stage S1 %s: json.loads gave %s not list — dropping", fn, type(rows).__name__)
                            rows = []
                    except (json.JSONDecodeError, TypeError) as _e:
                        logger.warning("Two-stage S1 %s: json.loads failed: %s", fn, _e)
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
                        _dbg.debug("[S1-PARSE] rows was dict, converted to list len=%d", len(rows))

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
                                logger.warning("Two-stage S1 %s: row string parsed to %s, dropping", fn, type(_parsed).__name__)
                        except (json.JSONDecodeError, TypeError) as _e:
                            logger.warning("Two-stage S1 %s: row string json.loads failed: %s", fn, _e)
                    elif isinstance(_row, list):
                        # Nested list — flatten one level
                        for _inner in _row:
                            if isinstance(_inner, dict):
                                normalized.append(_inner)
                    else:
                        logger.warning("Two-stage S1 %s: unexpected row type %s, dropping", fn, type(_row).__name__)
                rows = normalized

                if _dbg:
                    _dbg.debug("[S1-AFTER-NORM] len(rows)=%d  first_row=%r", len(rows), repr(rows[0])[:500] if rows else "N/A")

            except Exception as exc:
                logger.error("Two-stage Stage 1 failed for %s: %s", fn, exc, exc_info=True)
                if _dbg:
                    _dbg.debug("[S1-EXCEPTION] %s: %s", type(exc).__name__, exc)
                # Surface the failure to the pipeline retry machinery instead
                # of masking it as an empty-but-successful extraction.
                raise

            if not rows:
                if _dbg:
                    _dbg.debug("[S1-EMPTY] rows is empty after normalization — explicit not_reported")
                # Stage 1 completed and found no rows: a genuine "not reported",
                # expressed in the same envelope shape single-call extraction
                # uses — an ambiguous bare [] would read as a silent failure and
                # burn pipeline retries on papers that just lack the table.
                return {fn: {"value": "NR", "source_text": "NR", "status": "not_reported"}}

            # Build clean per-row anchor dicts (unwrap source-grounded values)
            anchor_rows = [
                {k: self._unwrap_anchor_value(row.get(k)) for k in acs}
                for row in rows
            ]

            # Dedup identical anchor rows — a duplicated Stage-1 row would fire
            # a duplicate Stage-2 call and produce a duplicate result row.
            _seen_anchor_keys: set = set()
            _dedup_rows: List[Dict] = []
            _dedup_anchor_rows: List[Dict] = []
            for _row, _anchor_row in zip(rows, anchor_rows):
                _key = json.dumps(_anchor_row, sort_keys=True, ensure_ascii=False, default=str)
                if _key in _seen_anchor_keys:
                    continue
                _seen_anchor_keys.add(_key)
                _dedup_rows.append(_row)
                _dedup_anchor_rows.append(_anchor_row)
            rows, anchor_rows = _dedup_rows, _dedup_anchor_rows

            if _dbg:
                _dbg.debug("[S1-ANCHORS] len=%d  first=%r", len(anchor_rows), repr(anchor_rows[0])[:300] if anchor_rows else "N/A")

            # ── Stage 2 — one call per row, parallel ─────────────────────
            _nr = {"value": "NR", "source_text": "NR"}

            async def _run_row(row_idx: int, anchor_row: dict):
                row_anchor_json = json.dumps(anchor_row, ensure_ascii=False)
                if _dbg:
                    _dbg.debug("[S2-START] paper=%r  row=%d  anchor=%r", _tag, row_idx, row_anchor_json[:300])
                try:
                    out = await async_dspy_forward(
                        self.stage2_row,
                        markdown_content=markdown_content,
                        row_anchor=row_anchor_json,
                        **ctx_kwargs,
                    )
                    if _dbg:
                        _dbg.debug("[S2-RAW] paper=%r  row=%d  repr=%r", _tag, row_idx, repr(out)[:6000])
                    row_vals: Dict[str, Any] = {}
                    for col in vcs:
                        val = out.get(col)
                        if val is None:
                            # Model answered but omitted this column → failure (blank), not genuine NR.
                            row_vals[col] = {"value": "NR", "source_text": "NR", "status": "missing"}
                            continue
                        if isinstance(val, str):
                            try:
                                parsed = json.loads(val)
                                val = parsed if isinstance(parsed, dict) else {"value": parsed, "source_text": ""}
                            except (json.JSONDecodeError, TypeError):
                                val = {"value": val, "source_text": ""}
                        if not isinstance(val, dict):
                            val = {"value": val, "source_text": ""}
                        # Model answered: a concrete value, or an explicit NR (genuine "not reported").
                        val["status"] = "not_reported" if _is_not_reported(val.get("value")) else "reported"
                        _copts = self.__class__._col_options.get(col)
                        if _copts:
                            val = _normalize_enum_cell(val, _copts, f"{fn}.{col}")
                        row_vals[col] = val
                    if _dbg:
                        _dbg.debug("[S2-VALS] row=%d  cols=%s", row_idx, list(row_vals.keys()))
                    return row_idx, row_vals
                except Exception as exc:
                    logger.error("Two-stage Stage 2 row %d failed for %s: %s", row_idx, fn, exc, exc_info=True)
                    if _dbg:
                        _dbg.debug("[S2-EXCEPTION] row=%d  %s: %s", row_idx, type(exc).__name__, exc)
                    # Extraction errored for this row → mark cells as error so they render blank.
                    err = f"{type(exc).__name__}: {exc}"
                    return row_idx, {
                        col: {"value": "NR", "source_text": "NR", "status": "error", "error": err}
                        for col in vcs
                    }

            # Warm the prompt cache with row 0 sequentially, then fan out the rest.
            # Anthropic populates a cache entry only after the first response begins —
            # firing all N rows via asyncio.gather causes every call to miss and pay
            # the 1.25× cache-write rate. Running row 0 alone first lets rows 1..N-1
            # hit the warm cache at 0.1× input cost.
            if anchor_rows:
                first = await _run_row(0, anchor_rows[0])
                if len(anchor_rows) > 1:
                    rest = await asyncio.gather(
                        *[_run_row(i, anchor_rows[i]) for i in range(1, len(anchor_rows))]
                    )
                    s2_results = [first, *rest]
                else:
                    s2_results = [first]
            else:
                s2_results = []
            # s2_results is [(row_idx, {col: val, ...}), ...] — sort by index to be safe
            s2_results = sorted(s2_results, key=lambda x: x[0])

            # ── Merge anchor cols + value cols into full row dicts ─────────
            merged_rows: List[Dict] = []
            for i, row in enumerate(rows):
                _, row_vals = s2_results[i]
                merged: Dict[str, Any] = {}
                for cell_key, cell_val in row.items():
                    # Anchor cells get the same envelope + status stamp value
                    # cells receive, so every cell downstream has one shape.
                    if not isinstance(cell_val, dict):
                        cell_val = {"value": cell_val, "source_text": ""}
                    else:
                        cell_val = dict(cell_val)
                    cell_val.setdefault(
                        "status",
                        "not_reported" if _is_not_reported(cell_val.get("value")) else "reported",
                    )
                    _copts = self.__class__._col_options.get(cell_key)
                    if _copts:
                        cell_val = _normalize_enum_cell(cell_val, _copts, f"{fn}.{cell_key}")
                    merged[cell_key] = cell_val
                merged.update(row_vals)
                merged_rows.append(merged)

            if _dbg:
                _dbg.debug("[MERGE] len(merged_rows)=%d  first=%r", len(merged_rows), repr(merged_rows[0])[:500] if merged_rows else "N/A")

            # Same envelope shape as single-call table extraction, so every
            # downstream consumer sees ONE format regardless of strategy.
            return {fn: {"value": merged_rows, "source_text": "", "status": "reported"}}

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

    extractor_factories: Dict[str, Type[dspy.Module]] = {}
    for sig_def in schema_def.get("signatures", []):
        sig_name = sig_def["class_name"]

        # ── Two-stage branch ─────────────────────────────────────────────
        # Only activate when the signature has exactly one output field that
        # carries extraction_strategy + anchor_columns + subform_fields.
        # Mixed sigs (table + scalar outputs) fall back to single-call to
        # keep this change contained.
        two_stage_field: Optional[dict] = None
        if len(sig_def.get("output_fields", [])) == 1:
            of = sig_def["output_fields"][0]
            if (
                of.get("extraction_strategy") == "row_then_columns"
                and of.get("anchor_columns")
                and of.get("subform_fields")
            ):
                two_stage_field = of

        if two_stage_field is None:
            _ignored = [
                of["name"] for of in sig_def.get("output_fields", [])
                if of.get("extraction_strategy") == "row_then_columns"
            ]
            if _ignored:
                logger.warning(
                    "Signature %s: row_then_columns on %s is IGNORED — two-stage "
                    "requires a single-output signature with anchor_columns and "
                    "subform_fields; falling back to single-call.",
                    sig_name, _ignored,
                )

        if two_stage_field is not None:
            extractor_factories[sig_name] = build_two_stage_extractor_class(
                sig_def, two_stage_field, _task_name
            )
            logger.info(
                "Two-stage extractor for %s — anchors=%s, value_cols=%d",
                sig_name,
                two_stage_field["anchor_columns"],
                len(two_stage_field["subform_fields"]) - len(two_stage_field["anchor_columns"]),
            )
            continue

        # ── Original single-call path (unchanged) ────────────────────────
        sig_class = build_signature_class(sig_def, _task_name)

        fallback = dict(fallback_structures.get(sig_name, {}))
        if not fallback:
            # Derive fallback from output field types
            for out in sig_def.get("output_fields", []):
                field_type = out.get("type", "Dict[str, Any]")
                fallback[out["name"]] = [] if "List" in field_type else {"value": "NR", "source_text": "NR"}

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

    Includes Stage1 and Stage2Row sub-signatures for two-stage fields.
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
        # Check if this sig uses two-stage
        two_stage_field = None
        if len(sig_def.get("output_fields", [])) == 1:
            of = sig_def["output_fields"][0]
            if (
                of.get("extraction_strategy") == "row_then_columns"
                and of.get("anchor_columns")
                and of.get("subform_fields")
            ):
                two_stage_field = of

        if two_stage_field is not None:
            anchor_set = set(two_stage_field.get("anchor_columns") or [])
            value_col_defs = [
                sf for sf in (two_stage_field.get("subform_fields") or [])
                if sf.get("field_name") not in anchor_set
            ]
            stage1_def = _build_stage1_sig_def(sig_def, two_stage_field)
            stage2_row_def = _build_stage2_row_sig_def(sig_def, two_stage_field, value_col_defs)

            lines.append(f"# ── TWO-STAGE: {sig_def['class_name']} ──────────────────────────")
            lines.append(f"# Stage 1: row discovery (anchor cols only)")
            lines.append("")
            lines.extend(_render_sig_def_to_lines(stage1_def))

            lines.append(f"# Stage 2: per-row (one call per discovered row, fills all {len(value_col_defs)} value cols)")
            lines.append("")
            lines.extend(_render_sig_def_to_lines(stage2_row_def))
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
    "build_two_stage_extractor_class",
    "build_schema_classes",
    "render_schema_to_python",
    "clear_class_cache",
]
