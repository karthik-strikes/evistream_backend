"""Export a project's form schema_defs from the live Supabase `forms` table to JSON.

The stage ablation runs fully offline from local `base_schemas/*.json` files. For a new
project those JSONs don't exist yet — this one-time helper pulls each form's `schema_def`
(the runtime pipeline spec: signatures + pipeline_stages) from the live DB and writes it
to the deterministic path `eval.studies.stage_ablation.projects` expects:
`base_schemas/<slug>_<form_label>.json`.

Each form label declared in `PROJECT_REGISTRY[slug].forms` is fuzzy-matched to a live
form by name; review the printed matches before trusting a run.

Reuses the Supabase bootstrap + project resolver from `eval.studies.ablation.fetch_markdown`.

Usage:
    python -m eval.studies.stage_ablation.export_schema_def --slug antibiotic  --project-name "Antibiotic"
    python -m eval.studies.stage_ablation.export_schema_def --slug oral_cancer --project-name "Oral Cancer" --dry-run
    python -m eval.studies.stage_ablation.export_schema_def --slug antibiotic  --project-id <uuid> --only outcomes
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, "/home/ubuntu/evistream")
sys.path.insert(0, "/home/ubuntu/evistream/eval")
sys.path.insert(0, "/home/ubuntu/evistream/eval/engine")

from eval.studies.ablation.fetch_markdown import _load_env, _supabase_client, _resolve_project_id
from eval.studies.stage_ablation.projects import PROJECT_REGISTRY, get_project


def _norm(s: str) -> str:
    return "".join(c for c in (s or "").lower() if c.isalnum())


def _match_form(form_label: str, db_forms: list[dict]) -> dict | None:
    """Fuzzy-match a form label (e.g. 'study_characteristics') to a live form by name."""
    from rapidfuzz import process, fuzz

    target = _norm(form_label)
    names = [_norm(f.get("name") or "") for f in db_forms]
    hit = process.extractOne(target, names, scorer=fuzz.token_sort_ratio, score_cutoff=55)
    if not hit:
        return None
    _, _, idx = hit
    return db_forms[idx]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--slug", required=True, choices=sorted(PROJECT_REGISTRY),
                    help="Project slug in projects.PROJECT_REGISTRY (sets output filenames + form list)")
    ap.add_argument("--project-name", help="case-insensitive substring match in projects.name")
    ap.add_argument("--project-id", help="exact project UUID (overrides --project-name)")
    ap.add_argument("--only", help="export only this form label (default: all forms for the slug)")
    ap.add_argument("--dry-run", action="store_true", help="resolve + match, but don't write JSON")
    ap.add_argument("--force", action="store_true", help="overwrite existing JSON files")
    args = ap.parse_args()

    proj = get_project(args.slug)
    want = {args.only: proj.forms[args.only]} if args.only else dict(proj.forms)
    if args.only and args.only not in proj.forms:
        ap.error(f"--only {args.only!r} not in project {args.slug!r}; choices: {sorted(proj.forms)}")

    _load_env()
    supabase = _supabase_client()
    project_id = _resolve_project_id(supabase, args.project_name, args.project_id)

    db_forms = (
        supabase.table("forms")
        .select("id, name, schema_name, schema_def, status")
        .eq("project_id", project_id)
        .execute()
        .data
        or []
    )
    db_forms = [f for f in db_forms if f.get("schema_def")]
    print(f"  Found {len(db_forms)} forms with schema_def for project {project_id}")
    if not db_forms:
        sys.exit("ERROR: no forms with schema_def found — is the project active/generated?")

    written = skipped = failed = 0
    for form_label, spec in want.items():
        match = _match_form(form_label, db_forms)
        if not match:
            print(f"    ✗ {form_label}: no live form matched "
                  f"(available: {[f.get('name') for f in db_forms]})")
            failed += 1
            continue

        out_path: Path = spec.schema_json
        print(f"    {form_label:<24} → form '{match.get('name')}' "
              f"(schema_name={match.get('schema_name')})  →  {out_path.name}")

        schema_def = match["schema_def"]
        if isinstance(schema_def, str):
            schema_def = json.loads(schema_def)
        if not schema_def.get("signatures"):
            print(f"      WARN: schema_def has no 'signatures' — run side will reject it")

        if args.dry_run:
            continue
        if out_path.exists() and not args.force:
            print(f"      SKIP: {out_path} exists (use --force to overwrite)")
            skipped += 1
            continue

        schema_def.setdefault("schema_name", match.get("schema_name") or form_label)
        schema_def.setdefault("task_name", schema_def["schema_name"])
        schema_def["_pulled_from"] = (
            f"project {project_id} · form {match.get('name')} ({match.get('id')}) "
            f"· slug {args.slug} · form_label {form_label}")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(schema_def, indent=2))
        written += 1

    print(f"\n  Done. written={written} skipped={skipped} failed={failed}")
    if args.dry_run:
        print("  --dry-run set; nothing written.")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
