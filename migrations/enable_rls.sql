-- =============================================================================
-- Enable Row Level Security on all public tables
-- =============================================================================
-- The backend uses SUPABASE_SERVICE_KEY which bypasses RLS entirely.
-- These policies protect against direct anon/JWT access to the database.
-- =============================================================================


-- =============================================================================
-- USERS
-- =============================================================================
ALTER TABLE public.users ENABLE ROW LEVEL SECURITY;

-- Users can only read their own record
CREATE POLICY "users_select_own"
  ON public.users FOR SELECT
  USING (id = auth.uid());

-- Users can update their own record
CREATE POLICY "users_update_own"
  ON public.users FOR UPDATE
  USING (id = auth.uid());


-- =============================================================================
-- PROJECTS
-- =============================================================================
ALTER TABLE public.projects ENABLE ROW LEVEL SECURITY;

-- Owner can do everything
CREATE POLICY "projects_owner_all"
  ON public.projects FOR ALL
  USING (user_id = auth.uid());

-- Project members can view projects they belong to
CREATE POLICY "projects_member_select"
  ON public.projects FOR SELECT
  USING (
    EXISTS (
      SELECT 1 FROM public.project_members
      WHERE project_members.project_id = projects.id
        AND project_members.user_id = auth.uid()
    )
  );


-- =============================================================================
-- PROJECT MEMBERS
-- =============================================================================
ALTER TABLE public.project_members ENABLE ROW LEVEL SECURITY;

-- Project owner can manage all memberships
CREATE POLICY "project_members_owner_all"
  ON public.project_members FOR ALL
  USING (
    EXISTS (
      SELECT 1 FROM public.projects
      WHERE projects.id = project_members.project_id
        AND projects.user_id = auth.uid()
    )
  );

-- Members can view their own membership record
CREATE POLICY "project_members_self_select"
  ON public.project_members FOR SELECT
  USING (user_id = auth.uid());


-- =============================================================================
-- DOCUMENTS
-- =============================================================================
ALTER TABLE public.documents ENABLE ROW LEVEL SECURITY;

-- Project owner has full access
CREATE POLICY "documents_owner_all"
  ON public.documents FOR ALL
  USING (
    EXISTS (
      SELECT 1 FROM public.projects
      WHERE projects.id = documents.project_id
        AND projects.user_id = auth.uid()
    )
  );

-- Members with can_view_docs can select
CREATE POLICY "documents_member_select"
  ON public.documents FOR SELECT
  USING (
    EXISTS (
      SELECT 1 FROM public.project_members
      WHERE project_members.project_id = documents.project_id
        AND project_members.user_id = auth.uid()
        AND project_members.can_view_docs = true
    )
  );

-- Members with can_upload_docs can insert/update/delete
CREATE POLICY "documents_member_write"
  ON public.documents FOR INSERT
  WITH CHECK (
    EXISTS (
      SELECT 1 FROM public.project_members
      WHERE project_members.project_id = documents.project_id
        AND project_members.user_id = auth.uid()
        AND project_members.can_upload_docs = true
    )
  );


-- =============================================================================
-- FORMS
-- =============================================================================
ALTER TABLE public.forms ENABLE ROW LEVEL SECURITY;

-- Project owner has full access
CREATE POLICY "forms_owner_all"
  ON public.forms FOR ALL
  USING (
    EXISTS (
      SELECT 1 FROM public.projects
      WHERE projects.id = forms.project_id
        AND projects.user_id = auth.uid()
    )
  );

-- Members with can_view_docs can select forms
CREATE POLICY "forms_member_select"
  ON public.forms FOR SELECT
  USING (
    EXISTS (
      SELECT 1 FROM public.project_members
      WHERE project_members.project_id = forms.project_id
        AND project_members.user_id = auth.uid()
        AND project_members.can_view_docs = true
    )
  );


-- =============================================================================
-- JOBS
-- =============================================================================
ALTER TABLE public.jobs ENABLE ROW LEVEL SECURITY;

-- Users can only see their own jobs
CREATE POLICY "jobs_owner_all"
  ON public.jobs FOR ALL
  USING (user_id = auth.uid());


-- =============================================================================
-- EXTRACTIONS
-- =============================================================================
ALTER TABLE public.extractions ENABLE ROW LEVEL SECURITY;

-- Project owner has full access
CREATE POLICY "extractions_owner_all"
  ON public.extractions FOR ALL
  USING (
    EXISTS (
      SELECT 1 FROM public.projects
      WHERE projects.id = extractions.project_id
        AND projects.user_id = auth.uid()
    )
  );

-- Members with can_view_results can select
CREATE POLICY "extractions_member_select"
  ON public.extractions FOR SELECT
  USING (
    EXISTS (
      SELECT 1 FROM public.project_members
      WHERE project_members.project_id = extractions.project_id
        AND project_members.user_id = auth.uid()
        AND project_members.can_view_results = true
    )
  );


-- =============================================================================
-- EXTRACTION RESULTS
-- =============================================================================
ALTER TABLE public.extraction_results ENABLE ROW LEVEL SECURITY;

-- Project owner has full access
CREATE POLICY "extraction_results_owner_all"
  ON public.extraction_results FOR ALL
  USING (
    EXISTS (
      SELECT 1 FROM public.projects
      WHERE projects.id = extraction_results.project_id
        AND projects.user_id = auth.uid()
    )
  );

-- Members with can_view_results can select
CREATE POLICY "extraction_results_member_select"
  ON public.extraction_results FOR SELECT
  USING (
    EXISTS (
      SELECT 1 FROM public.project_members
      WHERE project_members.project_id = extraction_results.project_id
        AND project_members.user_id = auth.uid()
        AND project_members.can_view_results = true
    )
  );


-- =============================================================================
-- SCHEMAS (system table — no direct client access)
-- =============================================================================
ALTER TABLE public.schemas ENABLE ROW LEVEL SECURITY;
-- Backend needs full access to manage schemas
CREATE POLICY "schemas_service_all"
  ON public.schemas FOR ALL
  USING (true)
  WITH CHECK (true);


-- =============================================================================
-- ACTIVITIES
-- =============================================================================
ALTER TABLE public.activities ENABLE ROW LEVEL SECURITY;

-- Users can only see their own activity
CREATE POLICY "activities_owner_select"
  ON public.activities FOR SELECT
  USING (user_id = auth.uid());

-- Allow inserts (backend handles auth; user_id is set server-side)
CREATE POLICY "activities_service_insert"
  ON public.activities FOR INSERT
  WITH CHECK (true);


-- =============================================================================
-- NOTIFICATIONS
-- =============================================================================
ALTER TABLE public.notifications ENABLE ROW LEVEL SECURITY;

-- Users can only see and update their own notifications
CREATE POLICY "notifications_owner_all"
  ON public.notifications FOR ALL
  USING (user_id = auth.uid());

-- Allow inserts (backend handles auth; user_id is set server-side)
CREATE POLICY "notifications_service_insert"
  ON public.notifications FOR INSERT
  WITH CHECK (true);
