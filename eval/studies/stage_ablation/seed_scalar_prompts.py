"""Seed a hand-tunable single-call prompt for a SCALAR (decomposed) form.

Scalar analogue of eval/studies/table_ablation/seed_prompts.py. A scalar form has its fields
spread across several DSPy signatures; the single-call prompt flattens ALL output
fields into one list and asks for ONE flat JSON object {field: value, ...} (one row
per paper). The generated file is a STARTING POINT — edit by hand; re-seed --force.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, "/home/ubuntu/evistream")
from eval.studies.ablation.run_extraction import _output_field_order


def load_schema_def(spec) -> dict:
    return json.loads(Path(spec.schema_json).read_text())


def _all_output_fields(schema_def: dict) -> list[dict]:
    """Every output field def across all signatures, in pipeline-stage order when possible."""
    by_name = {}
    for sig in schema_def.get("signatures", []):
        for f in sig.get("output_fields", []):
            by_name.setdefault(f["name"], f)
    order = _output_field_order(schema_def)
    if not order:                                  # no pipeline_stages → signature order
        order = list(by_name)
    return [by_name[n] for n in order if n in by_name]


def scalar_field_order(schema_def: dict) -> list[str]:
    return [f["name"] for f in _all_output_fields(schema_def)]


def _field_block(f: dict) -> str:
    name = f.get("name", "")
    desc = (f.get("description") or "").strip()
    grounded = bool(f.get("source_grounded"))
    lines = [f"### `{name}`" + ("  _(source-grounded)_" if grounded else "")]
    if desc:
        lines.append(desc)
    opts = f.get("options") or []
    if opts:
        lines.append("Allowed values: " + ", ".join(f'"{o}"' for o in opts))
    for label, key in (("Hints", "hints"), ("Rules", "rules"), ("Examples", "examples")):
        items = f.get(key) or []
        if items:
            lines.append(f"{label}:")
            lines.extend(f"- {it}" for it in items)
    return "\n".join(lines)


def build_seed_prompt(spec, schema_def: dict) -> str:
    fields = _all_output_fields(schema_def)
    any_grounded = any(f.get("source_grounded") for f in fields)

    def _cell(f) -> str:
        if f.get("source_grounded"):
            return f'"{f["name"]}": {{"value": "...", "source_text": "..."}}'
        return f'"{f["name"]}": "..."'

    header = [
        f"# Single-call extraction — {spec.form_label} ({spec.slug})",
        "",
        "You are extracting a structured data-extraction form from ONE study report in a "
        "systematic review. There is exactly ONE record per study (one row per paper). Read "
        "the entire paper text provided in the next message and fill in EVERY field below.",
        "",
        "## Fields",
        'Fill in EVERY field. Use the string "NR" when a value is not reported. Copy values from '
        "the paper; never invent. Fields marked _(source-grounded)_ must be returned as an object "
        '{"value": ..., "source_text": ...}; any other field is a plain value.',
        "",
    ]
    blocks = []
    for f in fields:
        blocks.append(_field_block(f))
        blocks.append("")

    example = "{" + ", ".join(_cell(f) for f in fields) + "}"
    footer = [
        "## Output format",
        "Return ONLY a single JSON object — no prose, no explanation, no markdown code fences:",
        "",
        example,
        "",
        "- Include every field key above, exactly once; do not return a list.",
    ]
    if any_grounded:
        footer += [
            "- Each _(source-grounded)_ field is an object with two keys: \"value\" (the value, or "
            '"NR") and "source_text" — ONE sentence (≤30 words) copied VERBATIM from the paper that '
            "supports the value; the value (or the phrase it was derived from) must appear in it "
            '(use "NR" when value is "NR").',
        ]
    return "\n".join(header + blocks + footer) + "\n"
