# Evaluation and benchmark artifacts

This directory contains the independent field-typed evaluation harness and
ablation tools included in the backend's 11 July 2026 snapshot. All 30 files
under the historical `engine/` matched the separately maintained evaluation
repository when this release was prepared; public changes make paths portable.

Reference annotations, saved predictions, and study manifests were packaged
separately on 19 October 2026. Their provenance and hashes are in
`data-manifest.json`. Read [DATA.md](DATA.md) before interpreting their coverage.

## Install only the scorer

From the backend repository root:

```bash
pip install -r eval/requirements.txt
python eval/score_form.py --help
python eval/smoke_test.py
```

No Supabase, S3, frontend, or extraction workers are needed to score saved CSVs.
The deterministic smoke test is synthetic and makes no paid API calls.

Score a supplied study-level prediction sheet:

```bash
python eval/score_form.py \
  --ai 'eval/sheets/ai sheets/full_studies/antibiotic/claude/form_study_characteristics_long.csv' \
  --gt 'eval/sheets/gt sheets/antibiotic_prophylaxis.xlsx' \
  --gt-sheet study_char --no-llm --out-dir /tmp/evistream-example-score
```

`--no-llm` skips free-text judging; those partial scores are not paper scores.
The quick scorer infers field types. To follow the research protocols, use the
explicit field definitions in `engine/forms/`, their record-matching settings,
and the study runners rather than inferred types. The original harness's default
free-text judge is Haiku 4.5; set `LLM_JUDGE_MODEL=claude-sonnet-4-6` and provide
your own `ANTHROPIC_API_KEY` to use the judge named in the paper. This incurs API
usage. Record the model, prompt version, selected GT file, and matching overrides.

## Ablations and extraction

Run these from the backend root:

```bash
python -m eval.studies.ablation.run_perio_ablation --help
python -m eval.studies.stage_ablation.run_prompt_arm --help
python -m eval.studies.table_ablation.run_prompt_arm --help
```

`studies/ablation/base_schemas/` contains field specifications for all four corpora.
The stage/table runners include single-prompt comparison arms. Rerunning extraction
requires your own model credentials and legally obtained article text in the
Markdown directories configured in the registries. PDFs and full-text Markdown
are not distributed here. Historical scripts that export from Supabase require
your own project; there are no credentials or hosted account IDs in this release.

## Interpretation

This is an inspectable research-code and reference-artifact release. Exact
reproduction of every number in the September paper has **not** been verified.
Saved predictions include historical filename/version variants; file presence
alone does not establish which variant produced a published table. Ground-truth
conventions and later corrections can affect scores. The scorer reports missed
and extra rows; unclassified extra rows are not automatically false positives.
Keep these counts alongside precision and recall.
