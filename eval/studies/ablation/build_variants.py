"""
Build the 4 field-spec ablation variants from a production v3 schema_def.

Loads the v3 schema_def (signatures + pipeline_stages + field metadata) from
Supabase `schemas` table and produces V1-V5 by cumulatively stripping
hints / rules / examples / source-grounding from every output field — and
from every column inside table (subform) fields. V5 is identical to v3 and
is registered as a control so the eval harness can read it through the same
naming convention.

Variants are registered via `register_schema(...)` with schema_name suffix
`__abl{n}_*`. The production schema is untouched.

Usage:
    python -m eval.studies.ablation.build_variants --form patient_population
    python -m eval.studies.ablation.build_variants --form patient_population --dry-run
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, "/home/ubuntu/evistream/backend")
sys.path.insert(0, "/home/ubuntu/evistream")

from .config import ORAL_CANCER_FORMS, VARIANTS, Variant, strip_list_for, variant_schema_name
from .fetch_markdown import _load_env  # reuse env loader


def _strip_field_components(field_def: dict, strip: list[str]) -> dict:
    """Remove the named components from a single output_field dict in place.

    `strip` items map directly to schema_def keys:
        hints, rules, examples         → top-level list keys
        source_grounded                → bool flag (set to False)
    Subform (table) columns get the same treatment recursively so the
    composition order matches the top-level field stripping.
    """
    out = copy.deepcopy(field_def)

    for key in ("hints", "rules", "examples"):
        if key in strip:
            out.pop(key, None)

    if "source_grounded" in strip:
        out["source_grounded"] = False

    if "subform_fields" in out:
        new_cols = []
        for col in out["subform_fields"]:
            col_copy = copy.deepcopy(col)
            for key in ("hints", "rules", "examples"):
                if key in strip:
                    col_copy.pop(key, None)
            new_cols.append(col_copy)
        out["subform_fields"] = new_cols

    return out


def build_variant_schema_def(base_schema_def: dict, variant: Variant) -> dict:
    """Produce a new schema_def by stripping components per `variant.keeps`."""
    strip = strip_list_for(variant)
    new_def = copy.deepcopy(base_schema_def)

    for sig in new_def.get("signatures", []):
        sig["output_fields"] = [
            _strip_field_components(f, strip) for f in sig.get("output_fields", [])
        ]

    new_def["version"] = new_def.get("version", 1) + 1
    new_def.setdefault("ablation", {})
    new_def["ablation"] = {
        "variant_name": variant.name,
        "keeps": list(variant.keeps),
        "stripped": strip,
        "base_schema_name": base_schema_def.get("schema_name"),
    }
    return new_def


def _summarize(name: str, schema_def: dict) -> None:
    """Print a one-line per-signature diff so dry-runs are inspectable."""
    print(f"\n  [{name}]")
    for sig in schema_def.get("signatures", []):
        for f in sig.get("output_fields", []):
            keys_present = [k for k in ("hints", "rules", "examples") if k in f]
            sg = "SG" if f.get("source_grounded") else "  "
            print(f"    {sig['class_name']}.{f['name']:<40} "
                  f"sg={sg}  has=[{','.join(keys_present) or '—'}]")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--form", required=True, choices=sorted(ORAL_CANCER_FORMS),
                    help="Which form to build variants for")
    ap.add_argument("--dry-run", action="store_true",
                    help="Print per-field strip summary; do NOT register")
    ap.add_argument("--dump-dir", type=Path, default=None,
                    help="Optional: write each variant's schema_def as JSON to this dir")
    args = ap.parse_args()

    _load_env()

    form_cfg = ORAL_CANCER_FORMS[args.form]
    base_name = form_cfg["base_schema_name"]

    from schemas.registry import get_schema, register_schema
    from schemas.config import DynamicSchemaConfig

    print(f"  Loading base schema: {base_name}")
    base = get_schema(base_name)
    if base.schema_def is None:
        sys.exit(f"ERROR: base schema {base_name!r} has schema_def=NULL — cannot ablate")

    # Make sure the stored schema_def carries its own schema_name so the
    # ablation metadata round-trips cleanly.
    base_def = copy.deepcopy(base.schema_def)
    base_def.setdefault("schema_name", base_name)

    if args.dry_run:
        _summarize(f"BASE  {base_name}", base_def)

    for variant in VARIANTS:
        v_def = build_variant_schema_def(base_def, variant)
        v_name = variant_schema_name(base_name, variant)

        if args.dry_run:
            _summarize(f"{variant.name}  →  {v_name}", v_def)
        else:
            new_config = DynamicSchemaConfig(
                schema_name=v_name,
                task_name=base.task_name,
                module_path=base.module_path,
                signatures_path=base.signatures_path,
                signature_class_names=base.signature_class_names,
                pipeline_stages=base.pipeline_stages,
                project_id=base.project_id,
                form_id=base.form_id,
                form_name=f"{base.form_name} [{variant.name}]",
                schema_def=v_def,
            )
            register_schema(new_config)
            print(f"  ✓ registered {v_name}")

        if args.dump_dir:
            args.dump_dir.mkdir(parents=True, exist_ok=True)
            out = args.dump_dir / f"{v_name}.json"
            out.write_text(json.dumps(v_def, indent=2, sort_keys=True))
            print(f"    dumped → {out}")

    print(f"\n  Done. {len(VARIANTS)} variants {'inspected (dry-run)' if args.dry_run else 'registered'}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
