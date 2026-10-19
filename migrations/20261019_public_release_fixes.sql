-- Upgrade the initial public release. Safe to apply again to that release.
-- The application uses custom JWTs and a server-only Supabase service-role key;
-- browser users must access these tables through the authorized FastAPI routes.

ALTER TABLE public.project_members DROP CONSTRAINT IF EXISTS chk_project_member_role;
ALTER TABLE public.project_members DROP CONSTRAINT IF EXISTS project_members_role_check;
ALTER TABLE public.project_members ADD CONSTRAINT project_members_role_check
  CHECK (role IN ('owner', 'manager', 'member', 'viewer'));

ALTER TABLE public.users
  ADD COLUMN IF NOT EXISTS password_reset_token TEXT,
  ADD COLUMN IF NOT EXISTS password_reset_expires_at TIMESTAMPTZ;
CREATE UNIQUE INDEX IF NOT EXISTS idx_users_password_reset_token
  ON public.users(password_reset_token) WHERE password_reset_token IS NOT NULL;

-- Remove policies whose names implied service access but applied to PUBLIC.
ALTER POLICY schemas_service_all ON public.schemas TO service_role;
ALTER POLICY activities_service_insert ON public.activities TO service_role;
ALTER POLICY notifications_service_insert ON public.notifications TO service_role;

-- Limit this migration to EviStreams tables, including all review/support tables.
DO $$
DECLARE
  table_name TEXT;
BEGIN
  FOREACH table_name IN ARRAY ARRAY[
    'users', 'projects', 'project_members', 'documents', 'forms', 'jobs',
    'schemas', 'extractions', 'extraction_results', 'activities', 'notifications',
    'permission_audit_log', 'review_assignments', 'adjudication_results',
    'qa_reviews', 'controlled_vocabularies', 'field_vocabulary_mappings',
    'validation_rules', 'audit_trail', 'irr_metrics', 'llm_history',
    'consensus_results', 'project_invitations', 'user_settings'
  ] LOOP
    EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', table_name);
    EXECUTE format('REVOKE ALL ON TABLE public.%I FROM PUBLIC, anon, authenticated', table_name);
    EXECUTE format('GRANT ALL ON TABLE public.%I TO service_role', table_name);
  END LOOP;
END;
$$;
