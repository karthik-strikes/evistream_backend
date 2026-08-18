# Classify table columns: ANCHOR (row identity) vs VALUE (measurement)

You are configuring a two-stage table extractor. A repeating table is extracted in two stages:

- **Stage 1 (anchors):** discovers each unique ROW by its identity / coordinate columns.
- **Stage 2 (values):** for each already-identified row, reads the measured numbers out of the document's tables/text.

Your job: split the columns below into **anchor_columns** and **value_columns**.

## Definitions

- **ANCHOR** — a column whose value *identifies or locates* a row: a label, category, name, group/arm identity, timepoint, subgroup, comparison, condition, or other coordinate. Anchors are how you tell one row apart from another. They are usually short labels or categories, **not** measured numbers.
- **VALUE** — a column holding a *measured quantity or statistic* extracted for an already-identified row: a mean, standard deviation, sample size (n), count, percentage, p-value, score, change/delta, etc. Anything that is "the number you read out of a results table/figure for this row" is a value.

**The test to apply to every column.** Could two legitimate rows in the same paper be identical on all the *other* anchors but differ on this column? If yes, it is an ANCHOR. If no — the column only describes or measures a row already pinned down by the others — it is a VALUE.

## Rules

1. Judge each column by **what it MEANS** — read its description first, then use its name as a hint. Do **not** classify based on keywords appearing in the name.
2. A measurement reported separately **per arm/group** (e.g. `mean_arm1`, `mean_arm2`, `sd_arm1`, `n_arm2`) is a **VALUE**, not an anchor. The arm is just part of the column name; the content is a measured number.
3. Classify **every** column as exactly one of anchor or value (each column name must appear in exactly one list).
4. There must be **at least one anchor** (so rows can be identified) and **at least one value** (so there is something to extract). Not every column can be an anchor.
5. **Repeated-measure coordinates are anchors.** If the same measurement can be reported at more than one time, phase, or visit, the column recording that time is an ANCHOR — `follow_up_duration`, `timepoint`, `visit`, `assessment_point` and the like — unless the table genuinely reports each outcome only once. The same applies to any column recording *which* subgroup, arm, comparison, outcome, scale or reporter a figure belongs to.
6. **Tie-breaks are asymmetric — the two mistakes are not equally bad.** Omitting a real coordinate MERGES two distinct rows into one and the lost row is unrecoverable and silent. Adding one column too many only splits rows more finely, which costs a little extra work and loses nothing. So:
   - In doubt about a **coordinate** column (a time, phase, group, arm, comparison, outcome, subgroup, condition, scale, reporter) → make it an **ANCHOR**.
   - In doubt about a **descriptive or commentary** column (a definition, a note, a free-text detail, or a restatement of another column in prose) → make it a **VALUE**. It describes a row; it does not identify one.
7. **Use the row key the table already declares.** If `table_description` states what one row represents (e.g. "one row per comparison × outcome × timepoint"), treat that as strong evidence for the anchor set. If your split departs from it, say why in `reasoning`.

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
