-- Access Control Redesign Migration
-- Adds project-level roles (manager/member/viewer), permission audit logging,
-- and the can_manage_members column.
--
-- Safe to run multiple times (uses IF NOT EXISTS).

-- 1. Add project role column to project_members
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_name = 'project_members' AND column_name = 'role'
  ) THEN
    ALTER TABLE project_members ADD COLUMN role TEXT NOT NULL DEFAULT 'member';
    ALTER TABLE project_members ADD CONSTRAINT chk_project_member_role
      CHECK (role IN ('manager', 'member', 'viewer'));
  END IF;
END $$;

-- 2. Add can_manage_members if not already present
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_name = 'project_members' AND column_name = 'can_manage_members'
  ) THEN
    ALTER TABLE project_members ADD COLUMN can_manage_members BOOLEAN NOT NULL DEFAULT FALSE;
  END IF;
END $$;

-- 3. Create permission audit log table
CREATE TABLE IF NOT EXISTS permission_audit_log (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  actor_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  target_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
  action TEXT NOT NULL CHECK (action IN (
    'member_invited', 'member_removed', 'member_role_changed',
    'permissions_updated', 'ownership_transferred'
  )),
  old_values JSONB,
  new_values JSONB,
  created_at TIMESTAMPTZ DEFAULT NOW()
);

-- 4. Indexes for permission audit log
CREATE INDEX IF NOT EXISTS idx_perm_audit_project ON permission_audit_log(project_id);
CREATE INDEX IF NOT EXISTS idx_perm_audit_created ON permission_audit_log(created_at DESC);

-- 5. Index for role lookups
CREATE INDEX IF NOT EXISTS idx_project_members_role ON project_members(role);
