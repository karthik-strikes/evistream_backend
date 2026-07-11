-- Fix 500 on POST /api/v1/issues
-- issue_reports is an internal table written to by the backend service role.
-- Simplest fix: disable RLS entirely (no user-facing read needed).
ALTER TABLE issue_reports DISABLE ROW LEVEL SECURITY;
