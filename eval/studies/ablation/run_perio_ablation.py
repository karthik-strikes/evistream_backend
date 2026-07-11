"""
Offline periodontitis field-spec ablation (D1) — build + extract, NO Supabase.

Loads the base schema_def from eval/studies/ablation/base_schemas/<schema>.json (pulled
and validated from the live DB), builds variants V1-V5 by cumulatively stripping
hints / rules / examples / source-grounding IN MEMORY, then runs each variant on
the 29 cached periodontitis markdowns via DynamicSchemaConfig.build_pipeline()
.run_batch — exactly the runtime path the app uses, but with the schema_def
supplied directly instead of fetched through the registry.

No service key, nothing written to the live `schemas` table, fully reproducible.

Variant CSVs are written to `eval/sheets/ai sheets/desc_only/periodontitis/_variants/` in the same wide
format the scorer (score_perio_ablation.py) and forms/periodontitis.py expect.

Usage:
    python -m eval.studies.ablation.run_perio_ablation --form patient_population --dry-run
    python -m eval.studies.ablation.run_perio_ablation --form patient_population --papers 2
    python -m eval.studies.ablation.run_perio_ablation --form patient_population
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, "/home/ubuntu/evistream/backend")
sys.path.insert(0, "/home/ubuntu/evistream")

from .config import VARIANTS, Variant, strip_list_for
from .build_variants import build_variant_schema_def
from .run_extraction import (
    _unwrap, _results_to_rows, _output_field_order, _write_csv, DEFAULT_CONCURRENCY,
)

REPO_ROOT = Path("/home/ubuntu/evistream")
EVAL_ROOT = REPO_ROOT / "eval"
BASE_SCHEMAS_DIR = EVAL_ROOT / "studies" / "ablation" / "base_schemas"
PERIO_CACHE = EVAL_ROOT / "sheets" / "markdown_perio"
OUT_DIR = EVAL_ROOT / "sheets/ai sheets" / "desc_only" / "periodontitis" / "claude"

# Periodontitis forms in scope for the ablation. Each maps to a saved base
# schema_def JSON. patient_population is the D1 base (mixed scalar field types).
PERIO_FORMS: dict[str, dict] = {
    "patient_population": {
        "schema_json": BASE_SCHEMAS_DIR / "dynamic_f0625cf9_PatientPopulation.json",
    },
    # The two table forms (row_then_columns) belong to D3, not D1, but are wired
    # so the same runner can drive them:
    "study_characteristics": {
        "schema_json": BASE_SCHEMAS_DIR / "dynamic_99c46506_StudyCharacteristics.json",
    },
    "interventions": {
        "schema_json": BASE_SCHEMAS_DIR / "dynamic_7d186a2f_InterventionCharacteristics.json",
    },
    "outcomes": {
        "schema_json": BASE_SCHEMAS_DIR / "dynamic_6c8eecce_ContinuousOutcomesV2.json",
    },
}


def _variant_by_name(name: str) -> Variant:
    for v in VARIANTS:
        if v.name == name:
            return v
    raise KeyError(f"Unknown variant {name!r}; known: {[v.name for v in VARIANTS]}")


def _load_base_schema_def(form: str) -> dict:
    path = PERIO_FORMS[form]["schema_json"]
    if not path.exists():
        sys.exit(f"ERROR: base schema_def not found: {path}")
    base = json.loads(path.read_text())
    if not base.get("signatures"):
        sys.exit(f"ERROR: {path} has no signatures — nothing to ablate")
    # build_variant_schema_def reads schema_name for ablation metadata
    base.setdefault("schema_name", base.get("task_name", form))
    return base


def _load_papers(limit: int | None) -> list[dict]:
    """Load the 29 periodontitis markdowns straight from the dir (no manifest).

    Paper key = file stem (e.g. 'Artese 2015'), which matches the GT key column.
    """
    if not PERIO_CACHE.exists():
        sys.exit(f"ERROR: {PERIO_CACHE} not found")
    papers: list[dict] = []
    for md in sorted(PERIO_CACHE.glob("*.md")):
        papers.append({
            "doc_id":           md.stem,
            "path":             str(md),
            "markdown_content": md.read_text(encoding="utf-8"),
            "_filename":        md.stem,
            "_author":          md.stem,
        })
        if limit and len(papers) >= limit:
            break
    print(f"  Loaded {len(papers)} periodontitis papers from {PERIO_CACHE}")
    return papers


def _make_config(base_def: dict, variant: Variant):
    """Construct a DynamicSchemaConfig for a variant WITHOUT touching the DB."""
    from schemas.config import DynamicSchemaConfig

    v_def = build_variant_schema_def(base_def, variant)
    schema_name = f"{base_def['schema_name']}__{variant.name}"
    cfg = DynamicSchemaConfig(
        schema_name           = schema_name,
        task_name             = base_def.get("task_name", schema_name),
        module_path           = "",   # unused on the runtime-builder path
        signatures_path       = "",   # unused on the runtime-builder path
        signature_class_names = [s["class_name"] for s in v_def.get("signatures", [])],
        pipeline_stages       = v_def.get("pipeline_stages", []),
        project_id            = "ablation",
        form_id               = "ablation",
        form_name             = f"PERIO patient_population [{variant.name}]",
        schema_def            = v_def,
    )
    return cfg, v_def


async def _run_one_variant(base_def: dict, variant: Variant, papers: list[dict],
                           field_order: list[str], concurrency: int) -> None:
    print(f"\n  Variant {variant.name}  (strips: {strip_list_for(variant) or 'nothing — full spec'})")
    cfg, _ = _make_config(base_def, variant)
    pipeline = cfg.build_pipeline()
    sem = asyncio.Semaphore(concurrency)
    results = await pipeline.run_batch(papers, sem)
    rows = _results_to_rows(results, papers, field_order)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    _write_csv(rows, field_order, OUT_DIR / f"patient_population_{variant.name}_long.csv")


async def _main_async(form: str, variant_names: list[str], papers_limit: int | None,
                      concurrency: int, dry_run: bool) -> int:
    os.environ.setdefault("USE_RUNTIME_BUILDERS", "true")
    base_def = _load_base_schema_def(form)
    field_order = _output_field_order(base_def)
    print(f"  Base schema: {base_def['schema_name']}  ({len(field_order)} fields)")

    selected = variant_names or [v.name for v in VARIANTS]

    if dry_run:
        for vname in selected:
            variant = _variant_by_name(vname)
            _, v_def = _make_config(base_def, variant)
            kept = {}
            for s in v_def["signatures"]:
                for f in s["output_fields"]:
                    has = [k for k in ("hints", "rules", "examples") if f.get(k)]
                    sg = "SG" if f.get("source_grounded") else "  "
                    sub = f" +{len(f['subform_fields'])} cols" if f.get("subform_fields") else ""
                    kept[f["name"]] = f"sg={sg} has=[{','.join(has) or '—'}]{sub}"
            print(f"\n  [{vname}] strips {strip_list_for(variant) or '(nothing)'}")
            for name, desc in kept.items():
                print(f"    {name:<32} {desc}")
        print("\n  Dry-run only — no extraction performed.")
        return 0

    papers = _load_papers(papers_limit)
    if not papers:
        sys.exit("ERROR: no periodontitis papers loaded")
    for vname in selected:
        await _run_one_variant(base_def, _variant_by_name(vname), papers, field_order, concurrency)
    print(f"\n  All variants done → {OUT_DIR}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--form", default="patient_population", choices=sorted(PERIO_FORMS))
    ap.add_argument("--variants", nargs="*", default=None,
                    help="Variant names to run (default: all V1-V5)")
    ap.add_argument("--papers", type=int, default=None, help="Smoke-test on first N papers")
    ap.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY)
    ap.add_argument("--dry-run", action="store_true",
                    help="Print per-field strip summary; do NOT extract")
    args = ap.parse_args()
    return asyncio.run(_main_async(args.form, args.variants, args.papers,
                                   args.concurrency, args.dry_run))


if __name__ == "__main__":
    sys.exit(main())
