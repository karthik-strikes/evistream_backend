-- Fix: audit_trail.project_id lacked ON DELETE CASCADE, unlike every other
-- table referencing projects(id). Any project with review/adjudication/QA
-- activity accumulates audit_trail rows, so a raw `DELETE FROM projects`
-- hit a foreign-key violation and surfaced as an opaque 500 from
-- backend/app/api/v1/projects.py:delete_project (the FK error was swallowed
-- by a generic `except Exception`). This blocked project deletion for
-- owners and admins alike on any "active" project.
--
-- Confirm the actual constraint name before applying if it differs:
--   SELECT conname FROM pg_constraint
--   WHERE conrelid = 'audit_trail'::regclass AND contype = 'f';

ALTER TABLE audit_trail DROP CONSTRAINT IF EXISTS audit_trail_project_id_fkey;
ALTER TABLE audit_trail
  ADD CONSTRAINT audit_trail_project_id_fkey
  FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE;
