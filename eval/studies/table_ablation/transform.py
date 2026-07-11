"""Table explode helpers for the table-extraction prompt arm.

`explode_table_results(...)` turns the single-call output (one list-of-row-dicts per
paper) into one CSV row per table row with the subform columns unwrapped — the same
wide-long shape the production extraction and the scorer expect.
"""

from __future__ import annotations

import ast

import sys
sys.path.insert(0, "/home/ubuntu/evistream")
from eval.studies.ablation.run_extraction import _unwrap, _src


def table_field_name(schema_def: dict) -> str:
    """The single output field that carries the table (has subform_fields)."""
    for sig in schema_def.get("signatures", []):
        for f in sig.get("output_fields", []):
            if f.get("subform_fields"):
                return f["name"]
    raise ValueError("no table (subform) field found in schema_def")


def subform_cols(schema_def: dict, field_name: str) -> list[str]:
    for sig in schema_def.get("signatures", []):
        for f in sig.get("output_fields", []):
            if f["name"] == field_name:
                return [c["field_name"] for c in f.get("subform_fields", [])]
    return []


def explode_table_results(results: dict, papers: list[dict], field: str,
                          cols: list[str]) -> list[dict]:
    """{doc_id: {field: [row-dicts]}} → wide-long rows (one per table row)."""
    meta = {p["doc_id"]: p for p in papers}
    out_rows: list[dict] = []
    for doc_id, fields in results.items():
        m = meta.get(doc_id, {})
        paper = m.get("_filename") or doc_id
        author = m.get("_author") or paper
        tbl = fields.get(field)
        if isinstance(tbl, dict) and "value" in tbl:
            tbl = tbl["value"]
        if isinstance(tbl, str):
            try:
                tbl = ast.literal_eval(tbl)
            except (ValueError, SyntaxError):
                tbl = []
        if isinstance(tbl, dict):
            tbl = [tbl]
        if not isinstance(tbl, list):
            tbl = []
        for row in tbl:
            if not isinstance(row, dict):
                continue
            r = {"Paper": paper, "paper": author}
            for c in cols:
                r[c] = _unwrap(row.get(c))
            out_rows.append(r)
    return out_rows


def grounded_table_header(cols: list[str]) -> list[str]:
    header = ["Paper", "paper"]
    for c in cols:
        header += [c, c + "__source_text"]
    return header


def explode_table_grounded(results: dict, papers: list[dict], field: str,
                           cols: list[str]) -> list[dict]:
    """Like explode_table_results but adds a `<col>__source_text` column beside each value."""
    meta = {p["doc_id"]: p for p in papers}
    out_rows: list[dict] = []
    for doc_id, fields in results.items():
        m = meta.get(doc_id, {})
        paper = m.get("_filename") or doc_id
        author = m.get("_author") or paper
        tbl = fields.get(field)
        if isinstance(tbl, dict) and "value" in tbl:
            tbl = tbl["value"]
        if isinstance(tbl, str):
            try:
                tbl = ast.literal_eval(tbl)
            except (ValueError, SyntaxError):
                tbl = []
        if isinstance(tbl, dict):
            tbl = [tbl]
        if not isinstance(tbl, list):
            tbl = []
        for row in tbl:
            if not isinstance(row, dict):
                continue
            r = {"Paper": paper, "paper": author}
            for c in cols:
                r[c] = _unwrap(row.get(c))
                r[c + "__source_text"] = _src(row.get(c))
            out_rows.append(r)
    return out_rows
