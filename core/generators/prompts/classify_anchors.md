# Classify table columns: ANCHOR (row identity) vs VALUE (measurement)

You are configuring a two-stage table extractor. A repeating table is extracted in two stages:

- **Stage 1 (anchors):** discovers each unique ROW by its identity / coordinate columns.
- **Stage 2 (values):** for each already-identified row, reads the measured numbers out of the paper's tables/text.

Your job: split the columns below into **anchor_columns** and **value_columns**.

## Definitions

- **ANCHOR** — a column whose value *identifies or locates* a row: a label, category, name, group/arm identity, timepoint, subgroup, comparison, condition, or other coordinate. Anchors are how you tell one row apart from another. They are usually short labels or categories, **not** measured numbers.
- **VALUE** — a column holding a *measured quantity or statistic* extracted for an already-identified row: a mean, standard deviation, sample size (n), count, percentage, p-value, score, change/delta, etc. Anything that is "the number you read out of a results table/figure for this row" is a value.

## Rules

1. Judge each column by **what it MEANS** — read its description first, then use its name as a hint. Do **not** classify based on keywords appearing in the name.
2. A measurement reported separately **per arm/group** (e.g. `mean_arm1`, `mean_arm2`, `sd_arm1`, `n_arm2`) is a **VALUE**, not an anchor. The arm is just part of the column name; the content is a measured number.
3. Classify **every** column as exactly one of anchor or value (each column name must appear in exactly one list).
4. There must be **at least one anchor** (so rows can be identified) and **at least one value** (so there is something to extract). Not every column can be an anchor.
5. When in doubt, prefer **fewer anchors**: an anchor must be needed to tell one row apart from another, not merely describe the row. Descriptive or commentary columns are values.

## Worked example

Columns:
- `outcome_type` — "Which clinical outcome this row reports (HbA1c, CAL, PPD…)"
- `timepoint` — "Follow-up timepoint (3 months, 6 months…)"
- `subgroup` — "Which arm comparison this row describes"
- `mean_arm1` — "Mean of the outcome in arm 1"
- `sd_arm1` — "Standard deviation in arm 1"
- `n_arm1` — "Number of patients in arm 1"
- `mean_arm2`, `sd_arm2`, `n_arm2` — "… the same statistics for arm 2"

Correct split:
- `anchor_columns`: `["outcome_type", "timepoint", "subgroup"]`
- `value_columns`: `["mean_arm1", "sd_arm1", "n_arm1", "mean_arm2", "sd_arm2", "n_arm2"]`
- `reasoning`: "The first three are coordinates that uniquely identify each row; the arm-suffixed columns are the measured statistics per arm."

## The columns to classify

[[COLUMNS_JSON]]

Return the classification.
