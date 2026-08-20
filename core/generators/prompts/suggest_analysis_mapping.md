# Map a table's columns onto meta-analysis roles

A systematic reviewer wants to pool this form's extracted table into a meta-analysis. Your job is to
read the table's columns and say **what kind of outcome data it holds**, **how its rows are organised**,
and **which column fills each analysis role**.

Your answer is a *suggestion*. A human confirms every slot before anything is computed, so a careful
"I am not sure, here is what I could fill" is far more useful than a confident guess. Never invent a
column name: every name you return must appear verbatim in the columns list below.

## Step 1 — the verdict

Pick exactly one:

- **`dichotomous`** — the table reports, for each group, how many participants had an event out of how
  many were assessed. Look for an event/responder/success count alongside a total or denominator.
- **`continuous`** — the table reports, for each group, a mean (or other central tendency) with a
  measure of spread and a sample size.
- **`effect`** — the table reports an **already-computed effect** rather than the arms behind it: an
  adjusted odds ratio, a hazard ratio, a rate ratio, a regression coefficient, a mean difference —
  together with its confidence interval or standard error. This is poolable, and it is the only shape
  where no arm-level counts are needed. Prefer `dichotomous` or `continuous` whenever arm-level data
  IS present: raw arms can produce any measure, whereas a reported effect is locked to the one the
  authors chose. `effect` is always `wide`.
- **`proportion`** — each row is **one group's** count out of a denominator, with no comparator: a
  prevalence, an event rate, a complication rate. Poolable on a variance-stabilising scale. If there
  are TWO groups' counts on the row it is `dichotomous`, not this.
- **`correlation`** — each row is one correlation coefficient together with the sample size it was
  computed from. Poolable on Fisher's z. Requires both: a correlation with no n cannot be weighted.
- **`diagnostic_accuracy`** — the table reports test performance as true/false positives and
  negatives (`tp`, `fp`, `fn`, `tn`), or sensitivity and specificity. This is a real meta-analysis but
  a different statistical family; say so and stop — do not force it into the other two.
- **`not_poolable`** — the table holds no effect estimate at all. Risk-of-bias judgements, study
  characteristics, index-test descriptions, and intervention/arm descriptions are all `not_poolable`,
  even when they contain numbers like a randomised count. A count of people in an arm is not an
  outcome.

When the verdict is `diagnostic_accuracy` or `not_poolable`, return empty `slots` and put a short,
plain-English explanation in `reasoning` that names the specific columns that led you there. That
sentence is shown directly to the reviewer, so write it for a person, not for a log.

## Step 2 — the layout

- **`wide`** — one row per comparison. Both groups appear side by side on the same row, usually as
  paired columns (`arm1_events` and `arm2_events`, `mean_arm1` and `mean_arm2`).
- **`long`** — one row per study arm. A single set of outcome columns (`events_n`, `central_tendency`)
  is repeated down the table, and a separate column says *which arm* each row describes. Rows have to
  be paired up before anything can be compared.

The tell is simple: if the outcome columns come in pairs, it is `wide`; if there is one set of outcome
columns plus an arm/intervention/group column, it is `long`.

## Step 3 — the slots

`slots` is a **list**. Each entry names one `role`, the `column` that fills it, and a short
`reasoning` clause. Fill only the roles that apply to your verdict and layout, and add an entry only
when a column genuinely fits — an omitted role is honest, a wrong one is not.

**`wide` + `dichotomous`** — `events_treatment`, `total_treatment`, `events_comparator`,
`total_comparator`, `outcome`, `timepoint`

**`wide` + `continuous`** — `mean_treatment`, `sd_treatment`, `n_treatment`, `mean_comparator`,
`sd_comparator`, `n_comparator`, `outcome`, `timepoint`

**`long` + `dichotomous`** — `value` (the event count on each row), `denominator`, `arm`, `outcome`,
`timepoint`

**`long` + `continuous`** — `value` (the central tendency on each row), `variability`, `denominator`,
`arm`, `outcome`, `timepoint`

**`wide` + `effect`** — `effect_value`, then precision from **either** `effect_se` **or** both
`effect_ci_lower` and `effect_ci_upper` (map both routes when both exist), plus `outcome`, `timepoint`

**`wide` + `proportion`** — `prop_events` (the count), `prop_total` (the denominator), `outcome`,
`timepoint`

**`wide` + `correlation`** — `corr_r`, `corr_n`, `outcome`, `timepoint`

Slot meanings:

- **`arm`** — the column naming which intervention or group a row describes. Only meaningful in `long`.
- **`outcome`** — which clinical outcome the row reports.
- **`timepoint`** — when it was measured. If the form encodes the timepoint inside the outcome column
  rather than separately, leave `timepoint` out and say so in `reasoning`.
- **`effect_value`** — the effect estimate as the paper printed it (an OR of `1.42`, not `ln(1.42)`).
  Do not map a p-value, a percentage change, or an arm-level number here.
- **`effect_se` / `effect_ci_lower` / `effect_ci_upper`** — that estimate's precision. Map the CI
  bounds when the table has them: an interval needs no assumption about which scale the standard error
  was quoted on. An effect with neither an SE nor both bounds cannot be weighted, so if the table has
  no precision column at all the verdict is `not_poolable`, not `effect`.
- **`prop_events` / `prop_total`** — a count and the denominator it came out of, for ONE group. A
  column holding a percentage is not `prop_events`; the count is what the pooling needs.
- **`corr_r` / `corr_n`** — the correlation and its sample size. `corr_r` must be the coefficient
  itself, not an R² or a p-value.
- **`variability`** — the spread that goes with the central tendency. This is frequently a **text**
  column, because it may hold a range or an interval like `"1.2 to 3.4"`. Do not reject a column for
  being text. If a separate column declares *which* measure the spread is (SD, SE, IQR, 95% CI), name
  it in `variability_measure_column`.

## Step 4 — which arm is the comparator

For a `long` layout, look at the arm column's declared options and pick the one that reads as the
control or reference group — placebo, no treatment, standard care, or similar. Return it in
`comparator_value`. If the options are not listed or none of them reads as a control, leave it out;
the reviewer will choose.

## Rules

1. Every column name you return must appear **verbatim** in the columns list. Anything else is dropped.
2. Judge a column by **what its description says it means**, then by its name. Do not classify on
   keywords alone — `n_randomized` in an interventions table is a study characteristic, not an outcome
   denominator.
3. A column holding a **percentage** is not a substitute for a count. Prefer `events_n` over
   `events_pct`; only use a percentage if there is no count at all, and say so in `reasoning`.
4. Do not map the same column into two different slots.
5. Each slot's `reasoning` is one short clause explaining that choice. The reviewer sees it on hover
   when deciding whether to confirm the slot, so make it specific: "the only event count in the
   table" is useful, "matches the pattern" is not.

## Worked example

Columns: `comparison`, `outcome`, `timepoint`, `arm1_label`, `arm1_events`, `arm1_n`, `arm2_label`,
`arm2_events`, `arm2_n`

- `verdict`: `dichotomous`
- `layout`: `wide`
- `slots`:
  ```json
  [
    {"role": "events_treatment",  "column": "arm1_events", "reasoning": "the event count for arm 1"},
    {"role": "total_treatment",   "column": "arm1_n",      "reasoning": "the denominator for arm 1"},
    {"role": "events_comparator", "column": "arm2_events", "reasoning": "the event count for arm 2"},
    {"role": "total_comparator",  "column": "arm2_n",      "reasoning": "the denominator for arm 2"},
    {"role": "outcome",           "column": "outcome",     "reasoning": "names the clinical outcome"},
    {"role": "timepoint",         "column": "timepoint",   "reasoning": "names when it was measured"}
  ]
  ```
- `reasoning`: "Event counts and totals are reported per arm on the same row, so each row is already one comparison."

## The table to map

[[COLUMNS_JSON]]

Return the mapping.
