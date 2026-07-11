-- Phase 3.5: Enforce dual-blind invariant at the DB level.
-- A single user must NEVER hold both reviewer_1 AND reviewer_2 for the same
-- document — that breaks the entire premise of dual-blind review.
--
-- Until now the only enforcement was an app-level pre-check in
-- assignment_service._find_r1_r2_conflicts that scanned only the *incoming*
-- payload. Two separate submissions could collude (Run #1: Wenrui as R2 doc-X;
-- Run #2: Wenrui as R1 doc-X) and the upserts would both succeed silently.
--
-- This partial unique index closes that hole. (Adjudicator overlap with R1/R2
-- is intentionally allowed under the "override" flag, so we scope the index to
-- reviewer_1/reviewer_2 only.)
--
-- Applied: 2026-05-07

-- Sanity check before creating the index — fail loudly if existing data already
-- violates the invariant. Resolve the violations manually before re-running.
DO $$
DECLARE
    bad_count INT;
BEGIN
    SELECT COUNT(*) INTO bad_count FROM (
        SELECT project_id, document_id, reviewer_user_id
        FROM review_assignments
        WHERE reviewer_role IN ('reviewer_1', 'reviewer_2')
        GROUP BY project_id, document_id, reviewer_user_id
        HAVING COUNT(*) > 1
    ) t;

    IF bad_count > 0 THEN
        RAISE EXCEPTION
            'Cannot create blind-review unique index: % document(s) already have the same user as both R1 and R2. Resolve those rows before re-running this migration.',
            bad_count;
    END IF;
END $$;

CREATE UNIQUE INDEX IF NOT EXISTS review_assignments_blind_unique_user_per_doc
ON review_assignments (project_id, document_id, reviewer_user_id)
WHERE reviewer_role IN ('reviewer_1', 'reviewer_2');
