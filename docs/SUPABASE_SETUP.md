# Supabase Quick Setup Guide

**Purpose:** Temporary database for Phase 1 testing. Will migrate to AWS RDS before production.

---

## Step 1: Create Supabase Project

1. Go to https://supabase.com
2. Click "Start your project"
3. Sign in with GitHub (or email)
4. Click "New Project"
5. Fill in:
   - **Name:** evistream-dev (or any name)
   - **Database Password:** Generate a strong password (save it!)
   - **Region:** Choose closest to you
6. Click "Create new project"
7. Wait 2-3 minutes for provisioning

---

## Step 2: Create Users Table

1. In your Supabase dashboard, click "SQL Editor" in the left sidebar
2. Click "New query"
3. Paste this SQL:

```sql
-- Create users table
CREATE TABLE users (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  email TEXT UNIQUE NOT NULL,
  hashed_password TEXT NOT NULL,
  full_name TEXT,
  is_active BOOLEAN DEFAULT TRUE,
  created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Create index for faster email lookups
CREATE INDEX idx_users_email ON users(email);

-- Verify table was created
SELECT * FROM users;
```

4. Click "Run" or press Ctrl+Enter
5. You should see "Success. No rows returned" (table is empty but created)

---

## Step 3: Get API Credentials

1. Click "Project Settings" (gear icon) in the left sidebar
2. Click "API" in the settings menu
3. You'll see:
   - **Project URL** - Copy this (e.g., https://xxxxx.supabase.co)
   - **Project API keys:**
     - `anon` `public` - Copy this (safe for frontend)
     - `service_role` `secret` - Copy this (backend only, KEEP SECRET!)

---

## Step 4: Update Backend .env File

Edit `backend/.env`:

```bash
# Database (Supabase)
SUPABASE_URL=https://your-project-id.supabase.co
SUPABASE_KEY=your-anon-public-key-here
SUPABASE_SERVICE_KEY=your-service-role-secret-key-here
```

**Important:**
- `SUPABASE_URL` = Project URL from Step 3
- `SUPABASE_KEY` = `anon` `public` key
- `SUPABASE_SERVICE_KEY` = `service_role` `secret` key

---

## Step 5: Restart Backend Server

```bash
# Stop server (Ctrl+C)
# Restart
cd /mnt/nlpgridio3/data/karthik9/Sprint1/Dental/eviStream/backend
conda activate topics
python -m app.main
```

Server should start without errors.

---

## Step 6: Test Authentication

### Option A: Swagger UI (Easiest)

1. Open http://localhost:8000/api/docs
2. Find **POST** `/api/v1/auth/register`
3. Click "Try it out"
4. Enter:
   ```json
   {
     "email": "test@example.com",
     "password": "testpass123",
     "full_name": "Test User"
   }
   ```
5. Click "Execute"
6. You should get:
   ```json
   {
     "access_token": "eyJ0eXAiOiJKV1QiLCJhbGc...",
     "token_type": "bearer",
     "user_id": "uuid-here"
   }
   ```

### Option B: curl

```bash
# Register
curl -X POST "http://localhost:8000/api/v1/auth/register" \
  -H "Content-Type: application/json" \
  -d '{
    "email": "test@example.com",
    "password": "testpass123",
    "full_name": "Test User"
  }'

# Login
curl -X POST "http://localhost:8000/api/v1/auth/login" \
  -H "Content-Type: application/json" \
  -d '{
    "email": "test@example.com",
    "password": "testpass123"
  }'
```

### Option C: Python Script

```python
import requests

BASE = "http://localhost:8000/api/v1"

# Register
resp = requests.post(f"{BASE}/auth/register", json={
    "email": "test@example.com",
    "password": "testpass123",
    "full_name": "Test User"
})
print("Register:", resp.json())

# Login
resp = requests.post(f"{BASE}/auth/login", json={
    "email": "test@example.com",
    "password": "testpass123"
})
token = resp.json()["access_token"]
print("Token:", token[:50] + "...")

# Get user info
headers = {"Authorization": f"Bearer {token}"}
resp = requests.get(f"{BASE}/auth/me", headers=headers)
print("User:", resp.json())
```

---

## Step 7: Verify in Supabase Dashboard

1. Go back to Supabase dashboard
2. Click "Table Editor" in left sidebar
3. Select "users" table
4. You should see your test user with:
   - Email: test@example.com
   - Hashed password (bcrypt hash)
   - Full name: Test User
   - is_active: true

---

## Troubleshooting

### Error: "Email already registered"
- User already exists. Try a different email or use login instead.

### Error: "Failed to create user"
- Check SUPABASE_SERVICE_KEY is correct
- Check users table exists (run SQL from Step 2 again)

### Error: "Invalid authentication credentials"
- Check SUPABASE_URL and SUPABASE_KEY are correct
- Check .env file is in backend/ directory
- Restart server after updating .env

### Error: Connection refused
- Make sure server is running: `python -m app.main`
- Check port 8000 is not used by another process

---

## Next Steps After Testing

Once authentication works:

1. ✅ Test all auth endpoints (register, login, /me)
2. ✅ Test protected endpoints (projects, documents, etc.)
3. ✅ Validate JWT tokens work correctly
4. 🔄 Continue implementing remaining endpoints
5. 🔄 Plan RDS migration (before production)

---

## Migration to AWS RDS (Later)

**When to migrate:**
- Before deploying to production
- When you need HIPAA compliance
- When you want full control over the database

**Estimated effort:** 2-3 hours

**What changes:**
- Replace Supabase client with SQLAlchemy ORM
- Add database.py with SQLAlchemy models
- Use Alembic for migrations
- Update .env with RDS connection string

I'll help with this migration when you're ready!

---

## Security Notes

⚠️ **Important for Production:**
- Never commit `.env` file to git (already in .gitignore)
- `service_role` key has full database access - keep it secret!
- Use row-level security (RLS) policies in production
- Rotate keys regularly
- Consider migrating to AWS RDS for production

✅ **Safe for Development:**
- Supabase free tier is fine for testing
- Test data is low-risk
- Easy to reset/recreate projects
