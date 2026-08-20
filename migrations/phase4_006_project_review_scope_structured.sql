-- Phase 4: structured form of the project review scope (UI STATE ONLY).
--
-- `review_scope` (phase4_005) stays the single source of truth for extraction:
-- it is the text injected into every signature docstring by
-- `runtime_builders.apply_review_scope`. Nothing in the extraction path reads
-- this column, and nothing ever should.
--
-- What it is for: the guided scope builder composes its chips (populations,
-- interventions, comparators, outcomes, timepoints, and which comparison pairs
-- the user unticked) down into that one prose string. The composition is
-- deliberately one-way -- unticked pairs simply vanish from the text, and an
-- intervention is indistinguishable from a comparator once written as
-- "A versus B" -- so the chips cannot be recovered by parsing the prose back.
-- They are stored here instead, purely so the builder can re-render them.
--
-- Contract enforced in app/api/v1/projects.py:_normalize_review_scope --
-- the two columns are always written together, and this one is NULLed whenever
-- the scope is cleared or saved as free text, so they can never disagree.
--
-- Nullable with no default, so every existing project starts unset, opens in
-- free-text mode, and composes byte-identical prompts to before.

ALTER TABLE projects
  ADD COLUMN IF NOT EXISTS review_scope_structured JSONB;
