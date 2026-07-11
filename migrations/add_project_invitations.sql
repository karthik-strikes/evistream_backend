-- Project invitations table.
-- Tracks pending invitations that allow any email address to join a project,
-- even before the recipient has registered an account.

CREATE TABLE IF NOT EXISTS project_invitations (
  id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id     UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  email          TEXT NOT NULL,
  role           TEXT NOT NULL CHECK (role IN ('manager', 'member', 'viewer')),
  -- Permission flags (copied from the invitation to project_members on accept)
  can_view_docs           BOOLEAN NOT NULL DEFAULT TRUE,
  can_upload_docs         BOOLEAN NOT NULL DEFAULT FALSE,
  can_create_forms        BOOLEAN NOT NULL DEFAULT FALSE,
  can_run_extractions     BOOLEAN NOT NULL DEFAULT FALSE,
  can_view_results        BOOLEAN NOT NULL DEFAULT TRUE,
  can_adjudicate          BOOLEAN NOT NULL DEFAULT FALSE,
  can_qa_review           BOOLEAN NOT NULL DEFAULT FALSE,
  can_manage_assignments  BOOLEAN NOT NULL DEFAULT FALSE,
  can_manage_members      BOOLEAN NOT NULL DEFAULT FALSE,
  -- Invitation lifecycle
  token          TEXT NOT NULL UNIQUE,
  invited_by     UUID REFERENCES users(id) ON DELETE SET NULL,
  expires_at     TIMESTAMPTZ NOT NULL,
  accepted_at    TIMESTAMPTZ,
  revoked_at     TIMESTAMPTZ,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Partial index for fast pending-invitation lookups
CREATE INDEX IF NOT EXISTS idx_project_invitations_pending
  ON project_invitations (project_id)
  WHERE accepted_at IS NULL AND revoked_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_project_invitations_token
  ON project_invitations (token);

CREATE INDEX IF NOT EXISTS idx_project_invitations_email
  ON project_invitations (lower(email));
