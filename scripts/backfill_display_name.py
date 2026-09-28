"""
One-shot script: backfill display_name on all form fields that lack it.

Usage:
    python backfill_display_name.py [--dry-run]

For each form, walks the fields JSONB array and sets
display_name = humanize(field_name) where display_name is absent.
"""

import json
import sys
import os

# Allow running from the backend directory
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from supabase import create_client
from app.config import settings


def humanize(field_name: str) -> str:
    return " ".join(w.capitalize() for w in field_name.split("_"))


def backfill_form(form: dict, dry_run: bool) -> bool:
    """Return True if any field was updated."""
    raw = form.get("fields")
    if not raw:
        return False

    fields = json.loads(raw) if isinstance(raw, str) else raw
    changed = False

    for field in fields:
        if not field.get("display_name"):
            field["display_name"] = humanize(field["field_name"])
            changed = True

    if changed and not dry_run:
        supabase = create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_KEY)
        supabase.table("forms").update(
            {"fields": json.dumps(fields)}
        ).eq("id", form["id"]).execute()

    return changed


def main():
    dry_run = "--dry-run" in sys.argv
    if dry_run:
        print("[DRY RUN] No changes will be written.")

    supabase = create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_KEY)
    result = supabase.table("forms").select("id, form_name, fields").execute()
    forms = result.data or []

    updated = 0
    for form in forms:
        if backfill_form(form, dry_run):
            updated += 1
            print(f"  {'(dry)' if dry_run else 'Updated'}: {form['form_name']} ({form['id']})")

    print(f"\nDone. {updated}/{len(forms)} forms {'would be' if dry_run else 'were'} updated.")


if __name__ == "__main__":
    main()
