"""
Run all ablation variants for a form on the 43 cached markdowns.

Reads `eval/sheets/markdown/*.md` (populated by `fetch_markdown.py`) and
hands them directly to `StagedPipeline.run_batch` — skips PDF processing,
skips S3 download, skips ExtractionService. Each variant is independent;
results are written as wide CSVs that match the existing eval ai-sheet
format so `eval/forms/<form>.py` can score them without modification.

Usage:
    python -m eval.studies.ablation.run_extraction --form patient_population
    python -m eval.studies.ablation.run_extraction --form patient_population --variants abl1_desc abl2_desc_hints
    python -m eval.studies.ablation.run_extraction --form patient_population --papers 2   # smoke test on first 2 docs
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, "/home/ubuntu/evistream/backend")
sys.path.insert(0, "/home/ubuntu/evistream")

from .config import (
    AI_SHEETS_DIR, CACHE_DIR, MANIFEST_PATH, ORAL_CANCER_FORMS,
    VARIANTS, variant_csv_path, variant_schema_name,
)
from .fetch_markdown import _load_env


# Max concurrent LLM calls across all (paper × extractor) tasks.
# Anthropic anthropic 4.7 free-tier rate limits are tight; bump if you have
# a higher tier. Match `ExtractionService` defaults.
DEFAULT_CONCURRENCY = 8


def _load_papers(limit: int | None) -> list[dict]:
    """Read manifest.json and load each markdown file into memory."""
    if not MANIFEST_PATH.exists():
        sys.exit(f"ERROR: {MANIFEST_PATH} not found. Run fetch_markdown.py first.")
    manifest = json.loads(MANIFEST_PATH.read_text())

    papers: list[dict] = []
    for doc_id, meta in manifest.items():
        path = Path(meta["local_path"])
        if not path.exists():
            print(f"  WARN: {path} missing for doc {doc_id}; skipping")
            continue
        papers.append({
            "doc_id":           doc_id,
            "path":             str(path),
            "markdown_content": path.read_text(encoding="utf-8"),
            "_filename":        meta.get("filename") or path.stem,
            "_author":          meta.get("author") or "",
        })
        if limit and len(papers) >= limit:
            break
    print(f"  Loaded {len(papers)} papers from {CACHE_DIR}")
    return papers


def _unwrap(value: Any) -> str:
    """Extract the scalar value from a {value, source_text} dict, or pass through.

    Pinned to the pre-absence-vocabulary strings so scores stay comparable with
    the published numbers. Before the four-state split, a failed cell and a
    genuine "not reported" both reached this function carrying the literal "NR",
    so both must keep emitting "NR" here; "NA" is already an NR token in
    eval/engine/config/nr_synonyms.py, so emitting it is inert.

    Making the eval reflect the real statuses is a worthwhile change, but a
    deliberate one to run on its own — not a side effect of the extraction fix.
    """
    if isinstance(value, dict) and "value" in value:
        from utils import absence

        st = absence.normalize_status(value.get("status"))
        if st in absence.FAILURE_STATUSES or st == absence.NOT_REPORTED:
            return "NR"
        if st == absence.NOT_APPLICABLE:
            return "NA"
        v = value["value"]
        if isinstance(v, list):
            return ", ".join(str(x) for x in v)
        return str(v) if v is not None else ""
    if isinstance(value, list):
        return ", ".join(str(x) for x in value)
    return str(value) if value is not None else ""


def _results_to_rows(
    results_by_doc: dict[str, dict],
    papers: list[dict],
    field_order: list[str],
) -> list[dict]:
    """Convert {doc_id: {field: {value, source_text}}} into wide CSV rows."""
    paper_meta = {p["doc_id"]: p for p in papers}
    rows = []
    for doc_id, fields in results_by_doc.items():
        meta = paper_meta.get(doc_id, {})
        row = {
            "Paper": meta.get("_filename") or doc_id,
            "paper": _unwrap(fields.get("paper")) or meta.get("_author") or "",
        }
        for fname in field_order:
            if fname == "paper":
                continue   # already handled above
            row[fname] = _unwrap(fields.get(fname))
        rows.append(row)
    return rows


def _output_field_order(schema_def: dict) -> list[str]:
    """Return all output field names in pipeline order (stage 1 first, then 2, ...)."""
    fields: list[str] = []
    sig_to_fields: dict[str, list[str]] = {}
    for sig in schema_def.get("signatures", []):
        sig_to_fields[sig["class_name"]] = [f["name"] for f in sig.get("output_fields", [])]
    for stage in sorted(schema_def.get("pipeline_stages", []), key=lambda s: s.get("stage", 0)):
        for sig_name in stage.get("signatures", []):
            for fname in sig_to_fields.get(sig_name, []):
                if fname not in fields:
                    fields.append(fname)
    return fields


def _write_csv(rows: list[dict], field_order: list[str], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    header = ["Paper", "paper"] + [f for f in field_order if f != "paper"]
    with path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=header, extrasaction="ignore", quoting=csv.QUOTE_ALL)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
    print(f"    ✓ wrote {len(rows)} rows → {path}")


# ---------------------------------------------------------------------------
# Source-grounding helpers (used by the prompt-arm runners). The value-only CSV
# above is unchanged; these emit a sibling CSV that keeps the verbatim quote
# beside each value as a paired `<field>__source_text` column.
# ---------------------------------------------------------------------------

def _src(cell: Any) -> str:
    """Extract source_text from a {value, source_text} cell; '' if flat/absent."""
    if isinstance(cell, dict) and "source_text" in cell:
        st = cell["source_text"]
        if isinstance(st, list):
            return ", ".join(str(x) for x in st)
        return str(st) if st is not None else ""
    return ""


def grounded_scalar_header(field_order: list[str]) -> list[str]:
    header = ["Paper", "paper"]
    for f in field_order:
        if f == "paper":
            continue
        header += [f, f + "__source_text"]
    return header


def _results_to_grounded_rows(results_by_doc: dict[str, dict], papers: list[dict],
                              field_order: list[str]) -> list[dict]:
    """Like _results_to_rows but adds a `<field>__source_text` column beside each value."""
    paper_meta = {p["doc_id"]: p for p in papers}
    rows = []
    for doc_id, fields in results_by_doc.items():
        meta = paper_meta.get(doc_id, {})
        row = {"Paper": meta.get("_filename") or doc_id,
               "paper": _unwrap(fields.get("paper")) or meta.get("_author") or ""}
        for fname in field_order:
            if fname == "paper":
                continue
            row[fname] = _unwrap(fields.get(fname))
            row[fname + "__source_text"] = _src(fields.get(fname))
        rows.append(row)
    return rows


def _write_grounded_csv(rows: list[dict], header: list[str], path: Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=header, extrasaction="ignore", quoting=csv.QUOTE_ALL)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
    print(f"    ✓ wrote grounded CSV ({len(rows)} rows) → {path}")


async def _run_one_variant(variant_name: str, base_schema_name: str, papers: list[dict],
                            concurrency: int) -> dict:
    """Run one ablation variant end-to-end. Returns {doc_id: {field: value-or-dict}}."""
    from schemas.registry import get_schema

    schema_name = variant_schema_name(base_schema_name, _variant_by_name(variant_name))
    print(f"\n  Variant {variant_name}  (schema={schema_name})")
    cfg = get_schema(schema_name)
    pipeline = cfg.build_pipeline()
    sem = asyncio.Semaphore(concurrency)
    results = await pipeline.run_batch(papers, sem)
    return results


def _variant_by_name(name: str):
    for v in VARIANTS:
        if v.name == name:
            return v
    raise KeyError(f"Unknown variant {name!r}; known: {[v.name for v in VARIANTS]}")


async def _main_async(form: str, variant_names: list[str], papers_limit: int | None,
                       concurrency: int) -> int:
    _load_env()

    form_cfg = ORAL_CANCER_FORMS[form]
    base_schema_name = form_cfg["base_schema_name"]

    papers = _load_papers(papers_limit)
    if not papers:
        sys.exit("ERROR: no papers loaded from cache")

    # field_order from the BASE schema_def — variants share signature structure
    from schemas.registry import get_schema
    base_cfg = get_schema(base_schema_name)
    field_order = _output_field_order(base_cfg.schema_def)
    print(f"  Field order ({len(field_order)} fields): {field_order[:5]}...")

    selected = variant_names or [v.name for v in VARIANTS]
    for vname in selected:
        results = await _run_one_variant(vname, base_schema_name, papers, concurrency)
        rows = _results_to_rows(results, papers, field_order)
        _write_csv(rows, field_order, variant_csv_path(form, _variant_by_name(vname)))

    print("\n  All variants done.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--form", required=True, choices=sorted(ORAL_CANCER_FORMS))
    ap.add_argument("--variants", nargs="*", default=None,
                    help="Variant names to run (default: all). e.g., abl1_desc abl5_full")
    ap.add_argument("--papers", type=int, default=None,
                    help="Smoke-test on first N papers only")
    ap.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY,
                    help=f"Max in-flight LLM calls (default {DEFAULT_CONCURRENCY})")
    args = ap.parse_args()

    return asyncio.run(_main_async(args.form, args.variants, args.papers, args.concurrency))


if __name__ == "__main__":
    sys.exit(main())
