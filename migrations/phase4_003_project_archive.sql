-- Phase 4.003 — Project archiving
--
-- Adds soft-archive support to projects. `archived_at IS NULL` is the single
-- source of truth for "active" — there is deliberately no parallel boolean or
-- status enum, so the two can never disagree, and the UI gets an
-- "Archived on <date>" label for free.
--
-- Archived projects are hidden from the default project list and become
-- read-only (enforced in app/services/project_access.py:check_project_access).

ALTER TABLE projects
  ADD COLUMN IF NOT EXISTS archived_at TIMESTAMPTZ NULL,
  ADD COLUMN IF NOT EXISTS archived_by UUID NULL REFERENCES users(id) ON DELETE SET NULL;

-- Partial index: the hot path is "list the active projects, newest first".
CREATE INDEX IF NOT EXISTS idx_projects_active
  ON projects(created_at DESC) WHERE archived_at IS NULL;

-- permission_audit_log.action is a CHECK-constrained enum (see
-- access_control_redesign.sql). Widen it so archive/restore can be audited.
ALTER TABLE permission_audit_log DROP CONSTRAINT IF EXISTS permission_audit_log_action_check;

ALTER TABLE permission_audit_log
  ADD CONSTRAINT permission_audit_log_action_check CHECK (action IN (
    'member_invited', 'member_removed', 'member_role_changed',
    'permissions_updated', 'ownership_transferred',
    'project_archived', 'project_restored'
  ));
