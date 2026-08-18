-- Phase 4: Project-level review scope.
-- Free text stating what the systematic review is about (population, comparison
-- of interest, outcomes). It is injected into every extraction prompt at runtime
-- as CONTEXT ONLY — it helps the model resolve which arm/timepoint/measure a
-- field refers to. It does not filter rows and the model is explicitly told not
-- to drop data because it looks out of scope.
--
-- Deliberately its own column rather than a key inside review_settings: the
-- review-settings PATCH (app/api/v1/projects.py) replaces that JSONB wholesale,
-- which would silently wipe the scope on any blinding change.
--
-- Nullable with no default, so every existing project starts unset and composes
-- byte-identical prompts to before.

ALTER TABLE projects
  ADD COLUMN IF NOT EXISTS review_scope TEXT;
