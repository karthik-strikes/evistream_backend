-- Multi-owner support: move ownership from projects.user_id (single) to
-- project_members.role='owner' (multi-row).
--
-- projects.user_id is kept as a "created_by" pointer for legacy compatibility
-- (list_projects dedup, audit trail) but is no longer used for access decisions.
--
-- Run this migration BEFORE deploying the backend code that checks ownership
-- via project_members.role.

-- 1. Allow 'owner' as a valid project_members role
ALTER TABLE project_members DROP CONSTRAINT IF EXISTS project_members_role_check;
ALTER TABLE project_members ADD CONSTRAINT project_members_role_check
    CHECK (role IN ('owner', 'manager', 'member', 'viewer'));

-- 2a. Upgrade existing project_members rows to 'owner' where the user IS the
--     project's user_id (i.e., they were already a member AND the creator).
UPDATE project_members pm
SET
    role = 'owner',
    can_view_docs          = true,
    can_upload_docs        = true,
    can_create_forms       = true,
    can_run_extractions    = true,
    can_view_results       = true,
    can_adjudicate         = true,
    can_qa_review          = true,
    can_manage_assignments = true,
    can_manage_members     = true
FROM projects p
WHERE pm.project_id = p.id
  AND pm.user_id    = p.user_id;

-- 2b. Insert an owner row for creators who do NOT yet have a project_members row.
INSERT INTO project_members (
    project_id, user_id, role,
    can_view_docs, can_upload_docs, can_create_forms, can_run_extractions,
    can_view_results, can_adjudicate, can_qa_review,
    can_manage_assignments, can_manage_members,
    invited_by
)
SELECT
    p.id      AS project_id,
    p.user_id AS user_id,
    'owner'   AS role,
    true, true, true, true, true, true, true, true, true,
    p.user_id AS invited_by   -- self-invited (creator)
FROM projects p
WHERE NOT EXISTS (
    SELECT 1
    FROM project_members pm
    WHERE pm.project_id = p.id
      AND pm.user_id    = p.user_id
);
