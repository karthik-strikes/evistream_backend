# ibuprofen_gt — ground-truth build for CD015432

Reproducibly builds `eval/sheets/gt sheets/ibuprofen.xlsx` from the Cochrane review
**"Ibuprofen for acute postoperative pain in children"** (CD015432.pub2, 2024), the
third eval dataset (alongside `oral_cancer`, `antibiotic`, `periodontitis`).

## Run

```bash
conda activate topics    # needs pdftotext (poppler) + openpyxl
python eval/studies/ibuprofen_gt/build_ibuprofen_gt.py \
    --pdf "/home/ubuntu/evistream/Ibuprofen for acute postoperative pain in children.pdf"
```

The build is fully deterministic (no LLM). It prints `0 warnings` when every forest-plot
block's per-arm N/event sums reconcile with its `Total (95% CI)` line.

## What it produces

`ibuprofen.xlsx` — aligned workbook consumed by `eval/engine/forms/ibuprofen.py`
(`IBUPROFEN_REGISTRY`, wired into `eval_walkthrough.ipynb`'s `DATASETS`). One `definitions`
codebook tab + 6 data tabs (row 0 = `ai_col` headers, row 1 = `EXTRACT/OPTIONAL/NEGLECT`
directive, `study_id` key):

| Tab | Rows | Source |
|---|---|---|
| `study_char` | 43 | Characteristics → Methods/Participants/Interventions/Outcomes/Setting/Notes (columns match the review's own "Table 1. Overview of study characteristics": country, surgery type, route, **outcomes measured**, design, centres, funding) |
| `patient_pop` | 43 | Characteristics → Participants |
| `interventions` | 118 arms | Characteristics → Participants (N breakdown) + Interventions |
| `continuous_outcomes` | 55 | Data & analyses (pain intensity, opioid consumption, time-to-rescue) |
| `dichotomous_outcomes` | 97 | Data & analyses (adverse events, rescue med, nausea/vomiting, bleeding, renal) |
| `risk_of_bias` | 43 | Characteristics → Risk of bias (7 RoB-1 domains) |

## Known source quirks (faithful, not parser bugs)

- **Steen Law 2000** (arms 22+19+23 = 64 vs "63 total") and **Viitanen 2003**
  (40+41+40+38 = 159 vs "160 total") — the review's own arm Ns don't sum to its stated total.
- **Polat 2005b** — 150 randomised, 120 analysed (6 arms × 20); placebo is implied in the
  Interventions "or" list, so its 6 arms are hand-encoded in `per_study_rows()`.
- **Schema is trimmed to what CD015432 actually reports.** The review's Characteristics
  tables give only N, age, and surgery type for participants — not sex %, eligibility
  criteria, ASA class, care setting, or analysed-N — so those columns (which would be 100%
  NR) are intentionally omitted rather than carried over from the periodontitis/antibiotic
  templates. `number_of_centres`, `age_sd`, and `age_range` are kept but legitimately sparse
  (NR = "not stated"; age_sd/age_range are complementary across mean-vs-median studies).

## Out of scope (review-level, not per-study extraction)

Deliberately **not** captured, matching the periodontitis GT's scope (which likewise excluded
review-computed effect sizes/CIs):
- **Summary of findings 1–4** (GRADE certainty per outcome per comparison) — meta-analytic
  output, not a per-study field. Can be added as a separate `summary_of_findings` tab if a
  meta-level GT is wanted.
- **Characteristics of excluded studies** (exclusion reasons) — not extraction of included trials.
- Table 1's derived "same/different administration route" flag — computable from `route`.

## Verification

`build_ibuprofen_gt.py` self-checks every forest block against its `Total` line. Beyond that,
the eval loader contract + a self-vs-self smoke score (AI := GT → macro-F1 = 1.000 on all 6
forms) confirm the schema, `study_id` key, level-2 matching keys, and strategies wire up.
