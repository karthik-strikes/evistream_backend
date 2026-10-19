"""Print the validated schema sequence for a new Supabase development project."""
import json
from pathlib import Path

root = Path(__file__).resolve().parents[1]
files = json.loads((root / "migrations/order.json").read_text())
print("-- EviStreams research release: new database setup\nBEGIN;")
for name in files:
    path = (root / name).resolve()
    if not path.is_relative_to(root):
        raise ValueError(f"Invalid schema path: {name}")
    print(f"\n-- Source: {name}\n{path.read_text()}\n")
print("COMMIT;")
