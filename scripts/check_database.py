"""Exercise release migrations in an EMPTY, disposable PostgreSQL database.

Usage: python scripts/check_database.py --dsn 'host=/tmp/pg port=5432 dbname=postgres user=postgres'
Requires a test database owner able to create roles. Every test transaction rolls back.
Never point this script at a hosted application database.
"""
import argparse
import json
from pathlib import Path

import psycopg
from psycopg import sql

ROOT = Path(__file__).resolve().parents[1]
FIX = 'migrations/20261019_public_release_fixes.sql'


def run_case(conn, upgrade=False):
    try:
        with conn.cursor() as c:
            c.execute("SELECT count(*) FROM pg_tables WHERE schemaname='public'")
            if c.fetchone()[0]:
                raise RuntimeError('Refusing to test a nonempty public schema')
            c.execute('CREATE SCHEMA auth; CREATE FUNCTION auth.uid() RETURNS uuid LANGUAGE sql AS $$ SELECT NULL::uuid $$')
            c.execute('CREATE ROLE anon; CREATE ROLE authenticated; CREATE ROLE service_role BYPASSRLS')
            # Reproduce public-schema API grants to verify that the migration closes them.
            c.execute('GRANT USAGE ON SCHEMA public TO anon, authenticated, service_role')
            c.execute('ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO anon, authenticated, service_role')
            for name in json.loads((ROOT / 'migrations/order.json').read_text()):
                if upgrade and name == FIX:
                    continue
                source = (ROOT / name).read_text()
                if upgrade and name == 'migrations/multi_owner.sql':
                    # The initial public release did not drop this legacy constraint.
                    source = source.replace('ALTER TABLE project_members DROP CONSTRAINT IF EXISTS chk_project_member_role;', '')
                if any(line.strip() and not line.strip().startswith('--') for line in source.splitlines()):
                    c.execute(source)
            c.execute("INSERT INTO users(email,hashed_password) VALUES ('test@example.org','synthetic') RETURNING id")
            user_id = c.fetchone()[0]
            c.execute("INSERT INTO projects(user_id,name) VALUES (%s,'Keep this project') RETURNING id", (user_id,))
            project_id = c.fetchone()[0]
            if upgrade:
                c.execute((ROOT / FIX).read_text())
            # Both fresh setup and upgrade must tolerate applying the fix again.
            c.execute((ROOT / FIX).read_text())
            c.execute("INSERT INTO project_members(project_id,user_id,role) VALUES (%s,%s,'owner')", (project_id,user_id))
            c.execute("UPDATE users SET password_reset_token='hashed-test-token', password_reset_expires_at=now()+interval '15 minutes' WHERE id=%s", (user_id,))
            c.execute('SELECT password_reset_token FROM users WHERE id=%s', (user_id,))
            assert c.fetchone()[0] == 'hashed-test-token'
            c.execute("SELECT tablename, rowsecurity FROM pg_tables WHERE schemaname='public'")
            tables = c.fetchall()
            assert len(tables) == 24 and all(enabled for _, enabled in tables)
            for role in ['anon', 'authenticated']:
                for table, _ in tables:
                    for privilege in ['SELECT', 'INSERT', 'UPDATE', 'DELETE']:
                        c.execute('SELECT has_table_privilege(%s,%s,%s)', (role, 'public.'+table, privilege))
                        assert not c.fetchone()[0], (role, table, privilege)
            # Even accidental future SELECT grants must not activate an unrestricted RLS policy.
            c.execute("INSERT INTO schemas(schema_name,task_name,signature_class_names,pipeline_stages) VALUES ('private','test','[]','[]')")
            for role in ['anon', 'authenticated']:
                c.execute(sql.SQL('GRANT SELECT ON schemas TO {}').format(sql.Identifier(role)))
                c.execute(sql.SQL('SET LOCAL ROLE {}').format(sql.Identifier(role)))
                c.execute('SELECT * FROM schemas')
                assert c.fetchall() == []
                c.execute('RESET ROLE')
            c.execute('SET LOCAL ROLE service_role')
            c.execute("UPDATE schemas SET task_name='still writable' WHERE schema_name='private' RETURNING schema_name")
            assert c.fetchone()[0] == 'private'
            c.execute('SELECT name FROM projects WHERE id=%s', (project_id,))
            assert c.fetchone()[0] == 'Keep this project'
            c.execute('RESET ROLE')
            print(f"PASS {'upgrade' if upgrade else 'fresh setup'}: owner insert, reset columns, all 24 table permissions, schema policies, service-role access, idempotency")
    finally:
        conn.rollback()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dsn', required=True)
    args = parser.parse_args()
    with psycopg.connect(args.dsn) as conn:
        run_case(conn)
        run_case(conn, upgrade=True)
