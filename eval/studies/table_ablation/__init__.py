"""Experiment 2 (D3b) — table extraction: row-then-columns vs single-call.

For a wide table form (one PDF → many rows, e.g. interventions / continuous outcomes),
the production pipeline extracts it with the two-stage `row_then_columns` strategy
(discover rows, then fill columns). This experiment compares that against a
`single_call` arm that asks the whole table in one prompt. Only the extraction
strategy of the table field changes; per-field text, model, and papers are held constant.

The single_call output is a list of row dicts, so it must be EXPLODED into one CSV
row per table row (matching the production wide-long shape) before scoring — see
`transform.explode_table_results`. The row-then-columns arm is the production
extraction already in `full_studies/<corpus>/<model>/`.
"""
