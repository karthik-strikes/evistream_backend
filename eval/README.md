# eviStream — Evaluation Harness

Grounded **F1 / precision / recall** scoring for eviStream extractions, plus the ablation
studies behind the paper. Lives in `backend/eval/`; a top-level `eval` symlink points here so
the scripts' existing absolute paths run unchanged.

## Prerequisites
- Run in the **`topics` conda env** (`dspy`, `supabase`, `rapidfuzz`) —
  `/home/ubuntu/miniconda3/envs/topics/bin/python`. Plain `base` lacks `dspy`.
- API keys live in `eval/.env` (Anthropic / OpenAI / Gemini / Bedrock) — **git-ignored, never committed**.

## Running
- **Interactive:** open `eval_walkthrough.ipynb`, set `VARIANT / DATASET / MODEL`, run each form.
- **Score one form:** `python score_form.py ...`
- **Ablation studies:** `python -m eval.<study>.<script>` from the repo root
  (e.g. `eval.stage_ablation.run_prompt_arm`).

> **Data is not in the repo.** Only the *code* is committed. The two data trees —
> `sheets/` (ground-truth + AI sheets, ~22 MB) and `outputs/` (scored results, ~14 MB) — are
> git-ignored and live locally. The rest of this document describes their layout and the
> scoring methodology; recreate or mount them locally to run.

---

# eval/ — data layout

Inputs live in `sheets/ai sheets/`, scored outputs in `outputs/`, ground truth in `sheets/gt sheets/`.
Both data trees are **variant-first** with uniform nesting **`<variant>/<corpus>/<model>/`**: the top
folder is the experiment, then corpus, then the model/source (e.g. `claude`, `gemini`, `gpt`,
`claude_code`). `outputs/` mirrors `sheets/ai sheets/` exactly.
All paths are defined once in **`config/paths.py`** — never hardcode `sheets/ai sheets/...` subpaths in a
script; import the constant.

## The 5 experiments → where their data lives

| # | Experiment | Input (`sheets/ai sheets/`) | Scored (`outputs/`) | Producing scripts |
|---|---|---|---|---|
| — | **Full studies** (shared reference) | `full_studies/<corpus>/<model>/form_*_long.csv` | `full_studies/<corpus>/<model>/` | `scripts/run_per_form_reports.py` |
| 4 | Model comparison (A1+C1) | `full_studies/oral_cancer/{claude,gemini,gpt}/` | `full_studies/oral_cancer/cross_model/` | `scripts/build_model_comparison.py` |
| 3 | Field-spec ablation, full vs desc-only (D1) | `desc_only/<corpus>/<model>/form_*_long.csv` | `outputs/desc_only/<corpus>/<model>/` | `ablation/run_perio_ablation.py`, `score_perio_ablation.py` |
| 1 | Decomposed vs single prompt (D3a) | `staged/<corpus>/<model>/<form>_prompt_long.csv` | `outputs/staged/<corpus>/<model>/` | `stage_ablation/run_prompt_arm.py`, `score_all_prompt_arms.py` |
| 2 | Table: row-then-cols vs single (D3b) | `table/<corpus>/<model>/<form>_prompt_long.csv` | `outputs/table/<corpus>/<model>/` | `table_ablation/run_prompt_arm.py`, `score_all_prompt_arms.py` |
| 5 | User study / comparators (B1) | `user_study/<corpus>/<source>/` (e.g. `claude_code`) | `outputs/user_study/<corpus>/<source>/` | `user_study/score_participant.py` |

## Key idea: `full_studies/` is the reference

`full_studies/<corpus>/claude/` is the production full-spec extraction. Every other variant is the
**other arm** of a comparison against it:
- `desc_only/` vs `full_studies` → does the engineered spec layer help?
- `staged/` (single prompt) vs `full_studies` (decomposed) → does question decomposition help?
- `table/` (single prompt) vs `full_studies` (row-then-columns) → does table decomposition help?
- `user_study/` vs `full_studies` → do untrained users match the system?

So `full_studies` is never duplicated per experiment — it is the shared baseline.

## Naming convention

- lowercase, underscores, ASCII hyphen, no spaces, no `(N)` suffixes.
- Form files: `form_<form_name>_long.csv` (e.g. `form_patient_population_long.csv`), uniform across
  corpora and models.

## Conventions / housekeeping

- `outputs/_archive/` and `sheets/ai sheets/_archive/dupes/` hold legacy and superseded files — nothing is
  deleted, just parked. `_archive/dupes/` is where the reorg put non-canonical duplicate extractions.
- The one-shot reorg that produced this layout: `scripts/reorg_migrate.py` (log in
  `scripts/reorg_move_log.txt`); pre-migration backup at `eval/_backup_<stamp>.tgz`.
- Markdown caches: `cache/markdown_perio/` (29 periodontitis papers), `cache/markdown/` (oral cancer).

## Scope-aware precision & F1 — handling out-of-scope rows

evistream extracts **every** finding in a paper; it has no "scope filter" yet. A human doing a
systematic review only records the rows that are **in scope** for that review. So the AI routinely
returns **more rows than the GT** — e.g. GT has 10 rows, the AI returns 15. Those extra 5 are usually
**real data that is simply out of scope**, not mistakes. Scoring them as errors would punish a missing
*feature*, not bad *extraction*.

**Two different things are being measured — keep them apart:**
1. **Extraction quality** — for the rows that belong in the review, are the values right?
2. **Scope filtering** — did it include only in-scope rows? (evistream cannot do this yet.)

### The rule
An **out-of-scope-but-real** row is **not** a false positive. Drop it from precision entirely — it is
neither TP nor FP. Precision and recall are computed over **in-scope rows only**:

```
Precision = correct in-scope rows / (correct in-scope + WRONG in-scope)
Recall    = correct in-scope rows / (all in-scope GT rows)
F1        = 2·P·R / (P + R)
```

An extra AI row only counts as a **false positive** if it is **hallucinated** (not actually in the
paper). So every extra AI row must be labelled:
- **out-of-scope but real** → excluded from precision (not the AI's fault)
- **hallucinated** → counts as a false positive (a real extraction error)

### Worked example
GT = 10 rows; AI = 15 rows (the 10 in-scope are all correct, the extra 5 are out-of-scope-but-real):

| Method | Precision | Recall | F1 |
|---|---|---|---|
| **Naive (wrong)** — count the 5 extras as FP | 10 / (10+5) = **0.67** | 10/10 = 1.0 | **0.80** (unfairly low) |
| **Scope-aware (correct)** — exclude the 5 extras | 10 / 10 = **1.0** | 10/10 = **1.0** | **1.0** |

### Report the over-extraction separately (never hide it inside F1)
The extra rows are a real cost — a human still has to read and discard them — so report them as their
own number, **not** folded into F1:

```
Over-extraction rate = out-of-scope AI rows / total AI rows   (here 5/15 = 33%)
```

**Recommended reporting:** publish **extraction F1 (in-scope only)** *and* the **over-extraction rate**,
with one line noting that scope restriction is future work.

### How the code handles this today (implemented)
The level-2 (per-arm / per-outcome) scorer in `forms/base_form.py:_run_level2` now treats the two
"unmatched" sides differently — because they are not symmetric:

- **GT arm with no AI match** → the AI missed an in-scope row → **counted as a false negative** on each
  filled field (recall is honest). Surfaced as `n_missed_gt_arms` in the form summary. *(Previously these
  rows were silently dropped, which inflated recall — `core/matcher.py:match_subrecords` now emits them.)*
- **AI arm with no GT match** → **kept aside as `n_over_extraction`, NOT counted as a false positive.**
  It is either out-of-scope (legitimate) or hallucinated, so precision here is an honest **upper bound**.

So today: **recall is trustworthy; precision is an upper bound** reported next to the over-extraction count.

> ⚠️ **Still to do (grounding step).** Splitting `n_over_extraction` into **hallucinated** (→ becomes an
> FP, lowering precision to its true value), **out-of-scope** (stays free), and **matching-miss** (rescued
> and scored) requires checking each unmatched AI row **against the paper markdown** — the "scope tagging"
> work. Plan: batch per paper (one LLM grounding call per paper, all its unmatched rows, md as shared
> context), reusing `core/llm_judge.py`. Papers live in `sheets/markdown_*`.

---

## Metrics — how a GT sheet + an AI sheet become numbers

This section explains, from scratch, how the eval turns **one ground-truth (GT) sheet** and **one AI-extraction sheet** into scores. The scoring engine lives in `core/`; the notebook `eval_walkthrough.ipynb` drives it through `forms/base_form.py:run_form()`. You never edit `core/` to score a new sheet — you only describe your fields (see the runbook below).

### 1. The pipeline in one picture

1. **Load** both sheets into tables — `core/loader.py`.
2. **Match studies** — pair each AI row to the right GT row by fuzzy-matching study names (RapidFuzz `token_sort_ratio`, accept ≥ 75, "confident" ≥ 85) — `core/matcher.py`.
3. **Match sub-rows** — *only for "level-2" forms* (one row per arm / per outcome): within a matched study, align AI rows to GT rows optimally (Hungarian assignment, keep if similarity ≥ 0.70) — `core/matcher.py:202`.
4. **Compare each field** with that field's *strategy* (values are normalized first by `core/cleaner.py`) — `core/comparator.py`.
5. Each comparison yields **one verdict**: YES / NO / NA / SKIP.
6. **Aggregate** the verdicts into metrics — `core/metrics.py`.

### 2. The four verdicts (the atom everything is built from)

Defined in `core/comparator.py`:

- **YES** — AI value matches GT value.
- **NO** — they disagree (this *includes* the case where AI says nothing but GT has a value).
- **NA** — *either* both sides are "NR" (Not Reported) → treated as agreement but excluded from most metrics, *or* GT is NR while AI extracted something → a possible over-extraction.
- **SKIP** — excluded from every metric (field marked `skip`/`exclude`, or the LLM judge errored).

### 3. The NR (Not Reported) policy — the crux

"NR" = the value isn't in the paper. How each (AI × GT) combination is scored (`core/metrics.py:4-19`). This is **information-retrieval style: there are no true negatives.**

| AI \ GT | GT has a value | GT = NR |
|---|---|---|
| **AI matches** | YES → **TP** | — |
| **AI has a different real value** | NO → **1 FP (wrong_value) + 1 FN** | NA → **1 FP (over_extraction)** |
| **AI = NR** | NO → **1 FN** (missed it) | NA → agreement, **excluded** |

So errors are sub-typed for diagnosis:
- **FP → `fp_wrong_value`** (AI extracted the wrong value) vs **`fp_over_extraction`** (AI invented a value the GT says isn't there — hallucination).
- **FN → `fn_false_nr`** (AI explicitly wrote "NR" when it shouldn't have) vs **`fn_empty`** (AI returned blank / extraction failed).

### 4. How each field is compared (the 11 strategies)

Set per field in `forms/<study>.py`; implemented in `core/comparator.py` (+ `core/cleaner.py`). A comparison is **YES** when:

| Strategy | YES when… | Example |
|---|---|---|
| `exact_normalize` | strings equal after lowercase / typo-fix / whitespace / NR normalize | `"RCT"` = `"rct"` → YES |
| `numeric_exact` | integers equal (year suffixes stripped) | `120` vs `120` → YES; `120` vs `119` → NO |
| `numeric_tolerance` | `|AI − GT| ≤ tolerance` (set on the field) | age `54.2` vs `54.0`, tol `0.5` → YES |
| `duration_normalize` | both converted to days, `|Δ| ≤ tolerance` days | `"3 months"` vs `"90 days"` → YES |
| `date_range_parse` | min **and** max year both equal | `"2014–2019"` vs `"Jan 2014 to Dec 2019"` → YES |
| `set_compare_terms` | Jaccard overlap of vocab terms ≥ 0.5 | `{surgery, radiotherapy}` vs `{surgery}` → 0.5 → YES |
| `parse_counts` | the extracted count-dict is equal | `"15 mild, 25 moderate"` vs same → YES |
| `parse_percent` | `|AI% − GT%| ≤ tolerance` | `"67.5% (27/40)"` vs `"67%"`, tol `1.0` → YES |
| `llm_judge` | Claude Haiku 4.5 decides the two texts state the **same fact** | `"UK"` vs `"United Kingdom"` → YES |
| `skip` / `exclude` | never scored (always SKIP) | identifier / broken columns |

Notes: `numeric_tolerance` / `parse_percent` / `duration_normalize` use the field's `tolerance` (default `0.0` = exact — so set it explicitly). `llm_judge` results are cached in `outputs/llm_judgments.json`, so re-runs are cheap; both-NR and exact matches are decided locally and never sent to the model (`core/llm_judge.py`).

### 5. The metrics — plain English + formula + a worked number

All computed per field in `core/metrics.py`. Take counts **TP=2, FP=1, FN=2** (from the §8 example) for the worked numbers.

- **TP / FP / FN** — the counts above, sub-typed as in §3 (`metrics.py:124-169`).
- **Precision** — *of what AI extracted, how much was right.* `TP / (TP + FP)` → `2/(2+1) = 0.667` (`:149`).
- **Recall** — *of what was in the GT, how much AI got.* `TP / (TP + FN)` → `2/(2+2) = 0.500` (`:150`).
- **F1** — *balance of the two.* `2·P·R / (P + R)` → `2·0.667·0.5 / 1.167 = 0.572` (`:151-158`).
- **% Agreement** — *raw agreement rate.* `(YES + both-NR) / (all non-SKIP) × 100`. With 2 YES + 1 both-NR over 5 rows → `3/5 = 60.0%` (`:90-96`).
- **Cohen's κ** — *agreement corrected for lucky guesses* (0 = chance, 1 = perfect). `sklearn.cohen_kappa_score(GT, AI)` over the YES/NO pool; needs ≥ 2 comparisons and ≥ 2 distinct labels, else `None` (`:105-118`).
- **Bootstrap 95% CI** — *how shaky the P/R/F1 are given so few rows.* Resample the rows with replacement 1000× (seed 42), report the 2.5th & 97.5th percentiles; `None` if fewer than 3 rows (`:45-81`). Reported as `Precision_CI_low/high`, etc.
- **MAE** — *average miss size on parseable numbers* (mean of each comparison's distance: numeric `|Δ|` or Jaccard). Note: a distance average, **not** classic regression MAE (`:171-173`).

### 6. Field → form aggregation: macro vs micro (`metrics.py:210-250`)

- **Macro** = average each field's metric, every field counts equally (`:234-237`). Good when every field matters the same.
- **Micro** = pool all fields' TP/FP/FN first, then compute P/R/F1 — fields with more rows dominate (`:218-229`). Good for an overall "how many cells are right" number.

### 7. What you get out (the artifacts)

- **`all_forms_metrics.xlsx`** — one sheet per form; columns: `Field`, `Compared`, `Both NR`, `GT=NR, AI extracted`, `% Agreement`, `kappa`, `TP`, `FP`, `FN`, `Precision`, `Recall`, `F1`, `F1_CI_low/high`, `Precision_CI_low/high`, `Recall_CI_low/high`, `n_under_extraction`, `AI missed (NR)`, `fn_empty`, `fp_over_extraction`, `fp_wrong_value`, `MAE`, `Strategy` (`reports/metrics_writer.py:32-59`).
- **`<form>_evaluation.xlsx`** — 4 sheets: **Comparison** (`AI_`/`GT_`/`MATCH_` per field), **Agreement Stats** (same columns as above), **Discrepancies** (every non-match with a reason), **Confusion Matrices** (`reports/excel_writer.py:45-151`).
- **Figures** (`reports/visualizer.py`): per-form κ/F1 bar chart, cross-form κ heatmap, study/arm match-rate bars.

### 8. One end-to-end worked example

Field `n_randomised` (`numeric_exact`) across 5 studies:

| Study | AI | GT | Verdict | Counts |
|---|---|---|---|---|
| 1 | 120 | 120 | YES | TP |
| 2 | 85 | 85 | YES | TP |
| 3 | 200 | 210 | NO | FP (wrong_value) + FN |
| 4 | NR | 64 | NO | FN (`fn_false_nr`) |
| 5 | NR | NR | NA | both-NR → excluded |

→ **TP=2, FP=1, FN=2** → **Precision 0.667, Recall 0.500, F1 0.572**, **% Agreement 60.0%** (2 YES + 1 both-NR out of 5), **MAE 3.33** (mean of |0|, |0|, |10| over the parseable YES/NO rows — study 4's NR has no number, so it's not in MAE). Same numbers used in §5.

---

## Adding something new — runbook for next time

**The `core/` engine, `reports/`, and `eval_walkthrough.ipynb` are generic and do not change.** Only the per-study *spec* (`forms/<study>.py`) and where you *place data* change. Here's exactly what to touch.

| Adding… | Files to touch |
|---|---|
| New AI sheet | none — just place the file |
| New model | none — just place the folder + pick `MODEL` |
| New GT sheet | `sheets/gt sheets/` + wire via the form CFG / notebook `DATASETS` |
| New study/corpus | `forms/<study>.py`, notebook `DATASETS` (+ maybe `forms/__init__.py`, `config/paths.py`, `config/key_term_vocab.py`) |
| New compare strategy | `config/field_config.py` + `core/comparator.py` |
| New metric | `core/metrics.py` + `reports/metrics_writer.py` |

### New AI sheet (a re-run, or a new variant/model output)
1. Name it `form_<form_name>_long.csv` (see §Naming).
2. Drop it at `sheets/ai sheets/<variant>/<corpus>/<model>/`.
3. **No code change** — the notebook auto-resolves the path from `VARIANT/DATASET/MODEL`, or set the `AI_SHEET` override cell for a one-off filename.

### New model (e.g. a 4th model beside claude / gemini / gpt)
The model is a **path component**, so:
1. Create `sheets/ai sheets/<variant>/<corpus>/<newmodel>/` and put the AI sheets there.
2. In the notebook set `MODEL="<newmodel>"`. Input reads and output writes both route to that model's folder automatically. **No code change.**

### New GT sheet
1. Put it in `sheets/gt sheets/`. Prefer an **aligned** workbook: one sheet per form, **column headers = your `ai_col` names**, plus one study-id key column.
2. Tell the loader about two quirks: **`gt_key_col`** (which column is the study id — e.g. `study_id` vs `Paper`) and **`gt_skiprows`** (drop a directive/units row, e.g. `[1]`).
3. Wire it through the form CFG + notebook `DATASETS` (see below).

### New study / corpus (the big one)
1. **Create `forms/<study>.py`** — copy `forms/periodontitis.py` as the template. For each form write:
   - a list of `FieldSpec(ai_col=..., strategy=..., tolerance=..., vocab=...)` — **one per column**;
   - a `CFG` dict: `ai_filename`, `gt_key_col`, `gt_skiprows`, `canonical_form`, `gt_section`, `gt_aligned_sheet`, and `level2` / `level2_type_key` / `level2_label_key` if it's one-row-per-arm.
   - Bundle the forms into a `<STUDY>_REGISTRY` dict at the bottom.
2. **Register it** in `eval_walkthrough.ipynb`: import the registry and add a `DATASETS` entry, e.g.
   ```python
   "mystudy": {"registry": MYSTUDY_REGISTRY, "aligned_gt": f"{GT_DATA_DIR}/mystudy.xlsx",
               "gt_key_col": "study_id", "gt_skiprows": [1], "canonical_form": "mystudy_study_char"},
   ```
   (If you're instead adding forms to the oral-cancer set, also add them to `forms/__init__.py`'s `FORM_REGISTRY`.)

   **Registered datasets** (the `DATASETS` keys in the boilerplate cell): `oral_cancer` (5 forms), `antibiotic` (4), `periodontitis` (5), `ibuprofen` (6 — CD015432 "Ibuprofen for acute postoperative pain in children"; adds a second outcome form because the review reports **both** continuous pain/opioid outcomes *and* dichotomous safety/rescue-med outcomes → `ibu_continuous_outcomes` + `ibu_dichotomous_outcomes`).
3. **Place data**: GT workbook → `sheets/gt sheets/`; AI sheets → `sheets/ai sheets/<variant>/<corpus>/<model>/`.
4. **Paths**: add constants to `config/paths.py` only if you introduce a brand-new variant/corpus directory.
5. **Run**: in the notebook set `VARIANT / DATASET / MODEL` and run each form.
6. **Decisions only a human can make** (the real work — everything else is boilerplate): for each field, the compare **strategy** + `tolerance`; which fields to **skip/exclude**; which form is **canonical** for study matching; the GT quirks (`gt_key_col`, `gt_skiprows`); and any **vocab** for `set_compare_terms` (add it to `config/key_term_vocab.py`).

### New compare strategy (a new scoring rule)
1. Add a `STRATEGY_*` constant in `config/field_config.py`.
2. Implement `compare_<name>(...)` in `core/comparator.py`.
3. Dispatch it inside `compare_field()`.
4. Use it from a `FieldSpec(strategy=STRATEGY_<NAME>)`.

### New metric
1. Compute it in `core/metrics.py` (`compute_field_metrics` and/or `compute_form_summary`).
2. Add its column in `reports/metrics_writer.py` (and, if you want it plotted/exported, `reports/visualizer.py` / `excel_writer.py`).
