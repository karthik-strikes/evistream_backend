"""
One-shot repair: sync metadata.decomposition with forms.fields for all active forms
where the decomposition is missing field names that exist in schema_def.

Run from backend/ directory:
  python repair_decomposition.py
"""
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from utils.secrets_loader import load_secrets
load_secrets()

from app.config import settings
from supabase import create_client

supabase = create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_KEY)

from core.generators.signature_splicer import add_decomposition_field

def get_all_decomposition_field_names(decomposition: dict) -> set:
    names = set()
    for sig in decomposition.get("signatures", []):
        names.update(sig.get("fields", {}).keys())
    return names

def get_schema_def_field_names(schema_def: dict) -> dict:
    """Returns {field_name: signature_class} for all output fields."""
    result = {}
    for sig in schema_def.get("signatures", []):
        cls = sig.get("class_name") or sig.get("name")
        for f in sig.get("output_fields", []):
            name = f.get("name")
            if name:
                result[name] = cls
    return result

def main():
    print("Fetching active forms with schema_def...")
    result = supabase.table("forms").select("id, form_name, fields, schema_def, metadata").eq("status", "active").execute()
    forms = result.data or []
    print(f"Found {len(forms)} active forms.")

    repaired = 0
    for form in forms:
        form_id = form["id"]
        form_name = form.get("form_name", "?")

        schema_def = form.get("schema_def")
        if not schema_def:
            continue

        meta = form.get("metadata") or {}
        if isinstance(meta, str):
            import json
            meta = json.loads(meta)

        decomposition = meta.get("decomposition")
        if not decomposition:
            continue

        # What fields exist in schema_def
        schema_fields = get_schema_def_field_names(schema_def)
        # What fields are already in decomposition
        decomp_fields = get_all_decomposition_field_names(decomposition)

        missing = {name: cls for name, cls in schema_fields.items() if name not in decomp_fields}
        if not missing:
            continue

        print(f"\n[{form_name}] ({form_id})")
        print(f"  Missing from decomposition: {list(missing.keys())}")

        patched_decomp = decomposition
        for field_name, sig_class in missing.items():
            patched_decomp = add_decomposition_field(patched_decomp, sig_class, field_name)
            print(f"  + Added '{field_name}' → '{sig_class}'")

        meta["decomposition"] = patched_decomp
        supabase.table("forms").update({"metadata": meta}).eq("id", form_id).execute()
        print(f"  ✓ Patched.")
        repaired += 1

    print(f"\nDone. Repaired {repaired} form(s).")

if __name__ == "__main__":
    main()
