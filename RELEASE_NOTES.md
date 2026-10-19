# Public research release — 19 October 2026

## Review fixes

The follow-up release repairs clean installation by replacing the full server
environment freeze with dependencies resolved in an isolated Python 3.11
environment. Unused OCR/GPU and unrelated application packages were removed.

Project creation now accepts owner memberships. A repeatable upgrade adds
password-reset fields, restricts service policies to the service role, and closes
direct anon/authenticated access to all 24 application tables. API authorization
runs before serving cached summaries; entity history is scoped to its authorized
project. Blinding and adjudication queries use document-level assignments.

Regression coverage includes permission revocation with a warm cache, foreign
entity history, both reviewers and adjudicators, password-reset token consumption,
fresh database setup, upgrade preservation, and database role permissions.

## Provenance

Application source: 11 July 2026, `7530ee53fbf9e6d60c454744364b551c62607b50`.
This was the latest available local backend commit before 25 July 2026, 00:00 UTC.
The public repository starts with a fresh history. Benchmark artifacts are a
separately dated collection; see `eval/data-manifest.json`.

## Scope

Included: PDF ingestion, structured forms, extraction-plan review, pilot
calibration, compiled DSPy extraction and table anchors, supporting passages,
team permissions, reviewer assignments and blinding, adjudication, agreement
statistics, export, and the independent evaluation and ablation code.

Removed: admin and usage APIs, standalone QA/data-cleaning/vocabulary tools,
notification/activity browsing APIs, issue reporting, deployment scripts,
agent settings, legacy precompiled study modules, Streamlit UI, and redundant
migrations. Internal logging, job status, permission checks, and shared review
schema remain where needed by the included workflow.

Portability fixes: checkout-relative paths; local environment loading; hosted
demo disabled and seeded IDs removed; public placeholder configuration; explicit
database setup order and missing base review columns restored. Tests for table
extraction were updated from an obsolete per-column interface to the existing
per-row interface, including source evidence and error/missing statuses. The
production extraction and scoring algorithms were not replaced.

## Validation

- Python source syntax check.
- Nine offline table-extraction tests: factory selection, anchor identity,
  evidence preservation, empty results, failed calls, and missing cells.
- Offline API import and HTTP checks: schema routes, authentication, disabled demo.
- Synthetic evaluation smoke test and scoring of the bundled antibiotic
  study-characteristics predictions with LLM judging disabled.
- Empty PostgreSQL 14 initialization with all 28 schema files.
- Benchmark checksums and scan for credential patterns and server-specific paths.

Exact reproduction of every paper result, a paid live extraction, and a fresh
hosted Supabase deployment were not verified. Benchmark documentation records
the remaining artifact-version and coverage limits.
