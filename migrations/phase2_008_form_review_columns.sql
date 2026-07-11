-- Phase 2.008 — form review audit columns
-- Adds compliance columns to forms table for approve/reject audit trail.

ALTER TABLE forms ADD COLUMN IF NOT EXISTS created_by_user_id UUID REFERENCES users(id);
ALTER TABLE forms ADD COLUMN IF NOT EXISTS reviewed_by_user_id UUID REFERENCES users(id);
ALTER TABLE forms ADD COLUMN IF NOT EXISTS reviewed_at TIMESTAMPTZ;
ALTER TABLE forms ADD COLUMN IF NOT EXISTS review_decision TEXT
    CHECK (review_decision IN ('approved', 'rejected'));

-- Index for quick lookups by reviewer and creator
CREATE INDEX IF NOT EXISTS idx_forms_reviewed_by ON forms(reviewed_by_user_id)
    WHERE reviewed_by_user_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_forms_created_by ON forms(created_by_user_id)
    WHERE created_by_user_id IS NOT NULL;
