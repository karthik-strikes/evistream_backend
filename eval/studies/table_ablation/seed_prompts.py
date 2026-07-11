"""Seed a hand-tunable single-call prompt for a table form from its schema_def.

`build_seed_prompt(spec, schema_def)` renders the table field's columns (anchors +
value columns) with their description/hints/rules into a Markdown prompt that asks
the model to return a flat JSON object {"rows": [ {col: value, ...}, ... ]}. The
generated file is a STARTING POINT — edit it by hand; only re-seed with --force.
"""

from __future__ import annotations

import json
from pathlib import Path


def load_schema_def(spec) -> dict:
    return json.loads(Path(spec.schema_json).read_text())


def table_field_def(schema_def: dict) -> dict:
    """Return the single output field that carries the table (has subform_fields)."""
    for sig in schema_def.get("signatures", []):
        for f in sig.get("output_fields", []):
            if f.get("subform_fields"):
                return f
    raise ValueError("no table (subform) field found in schema_def")


def _col_block(col: dict) -> str:
    name = col.get("field_name", "")
    role = col.get("extraction_role", "")
    desc = (col.get("field_description") or col.get("description") or "").strip()
    lines = [f"### `{name}`" + (f"  _({role})_" if role else "")]
    if desc:
        lines.append(desc)
    opts = col.get("options") or []
    if opts:
        lines.append("Allowed values: " + ", ".join(f'"{o}"' for o in opts))
    for label, key in (("Hints", "hints"), ("Rules", "rules")):
        items = col.get(key) or []
        if items:
            lines.append(f"{label}:")
            lines.extend(f"- {it}" for it in items)
    return "\n".join(lines)


def build_seed_prompt(spec, schema_def: dict) -> str:
    field = table_field_def(schema_def)
    cols = field.get("subform_fields") or []
    col_names = [c.get("field_name") for c in cols]

    header = [
        f"# Single-call extraction — {spec.form_label} ({spec.slug})",
        "",
        f'You are extracting the "{field.get("name")}" table from ONE study report in a '
        "systematic review. Produce one row per study arm / record. Read the entire paper "
        "text provided in the next message and extract EVERY row the paper supports — do not "
        "stop early, and do not merge two distinct arms/records into one row.",
        "",
        (field.get("description") or "").strip(),
        "",
        "## Columns",
        'For every row, return each column as an object {"value": ..., "source_text": ...} '
        '(source grounding). Use "NR" when a value is not reported. Copy values from the paper; '
        "never invent.",
        "",
    ]
    blocks = []
    for c in cols:
        blocks.append(_col_block(c))
        blocks.append("")

    example_row = "{" + ", ".join(
        f'"{n}": {{"value": "...", "source_text": "..."}}' for n in col_names) + "}"
    footer = [
        "## Output format",
        "Return ONLY a single JSON object — no prose, no explanation, no markdown code fences:",
        "",
        '{"rows": [',
        f"  {example_row}",
        "]}",
        "",
        "Source grounding — EVERY cell is an object with two keys:",
        '- "value": the extracted value, or "NR" if the paper does not report it.',
        '- "source_text": ONE sentence (≤30 words) copied VERBATIM from the paper that supports '
        "the value; the value (or the phrase it was derived from) must appear in it. Use \"NR\" "
        'when value is "NR".',
        "- One object per row; include every column key above in every row; never return a bare "
        "string for a cell.",
        '- If the paper reports no rows for this table, return {"rows": []}.',
    ]
    return "\n".join(header + blocks + footer) + "\n"
