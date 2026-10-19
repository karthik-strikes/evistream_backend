# Database setup

Use a new development Supabase project. The backend uses custom JWT accounts
in `public.users` and accesses PostgREST with a server-only service-role key.
Project permissions are enforced by the API. Never place that key in the frontend.

From the backend root, generate the setup SQL:

```bash
python scripts/build_schema.py > /tmp/evistream-schema.sql
```

Paste the resulting SQL into the new project's Supabase SQL editor and execute
it. The script uses `migrations/order.json`, which orders the base schema and its
historical migrations by dependency. This is a **new-database setup**, not an
upgrade procedure for an existing installation.

The 28-file sequence was executed successfully against an empty PostgreSQL 14
database during release preparation. The test supplied the standard Supabase
roles and `auth.uid()` interface, then rolled back. Public-release fixes enable
creation of the users table and restore the extraction-result grouping and
manual-review columns missing from the historical base SQL. Core review and
audit tables retain their historical shared definitions.

Register users through the application. Add reviewers to a project and assign
their roles there. No account data, demo identity, or production records are
included. A fresh hosted Supabase/PostgREST setup and a paid end-to-end extraction
were not exercised during packaging.
