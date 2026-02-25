"""
Create placeholder dev user for testing without authentication
"""
from supabase import create_client
import os
from dotenv import load_dotenv

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY")

if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
    print("ERROR: SUPABASE_URL and SUPABASE_SERVICE_KEY must be set in .env")
    exit(1)

supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)

# Check if dev user already exists
result = supabase.table("users").select("id").eq("id", "00000000-0000-0000-0000-000000000001").execute()

if result.data:
    print("✓ Dev user already exists")
else:
    # Create dev user
    result = supabase.table("users").insert({
        "id": "00000000-0000-0000-0000-000000000001",
        "email": "dev@localhost",
        "hashed_password": "dev_mode_no_auth_required",
        "full_name": "Development User",
        "is_active": True
    }).execute()

    if result.data:
        print("✓ Dev user created successfully")
    else:
        print("✗ Failed to create dev user")
        exit(1)
