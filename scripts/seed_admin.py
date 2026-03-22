"""
One-time script to promote a user to admin role.

Usage:
    ADMIN_EMAIL=you@example.com python backend/scripts/seed_admin.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from dotenv import load_dotenv
load_dotenv()

from supabase import create_client

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_KEY"]
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL")

if not ADMIN_EMAIL:
    print("Error: ADMIN_EMAIL environment variable is required.")
    print("Usage: ADMIN_EMAIL=you@example.com python backend/scripts/seed_admin.py")
    sys.exit(1)

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

result = supabase.table("users").update({"role": "admin"}).eq("email", ADMIN_EMAIL).execute()

if result.data:
    print(f"Successfully promoted {ADMIN_EMAIL} to admin.")
else:
    print(f"No user found with email {ADMIN_EMAIL}.")
    sys.exit(1)
