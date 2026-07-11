# Naive-Expert User Study — Protocol

**Study:** Off-the-shelf LLM extraction by experts vs. the evistream harness
**Corpus:** Periodontitis CD004714 — all 29 papers × all 5 forms
**Format:** Take-home, self-paced, fixed deadline
**Version:** draft v1 (2026-06-15) — finalize at the Tuesday meeting

---

## 1. Why this study exists (the claim it supports)

The paper's contribution is being reframed (per Chris) as a **harness / framework whose value is
quality control**, not a new model. The argument: a clinical data-extraction task done with
off-the-shelf Claude / Claude Code produces results whose quality **depends heavily on the user's
prompting skill** — a wide, unreliable band across people. evistream's typed field-specs (description
+ hints + rules + examples + source-grounding) and three HITL loops collapse that into a **tight,
high band** that does not depend on the operator.

This study measures the "average case" we are improving on: **how far a competent expert who has
never seen the system gets with raw tools and their own prompts.** The headline result is the
*spread* across experts (e.g. F1 85% ± 10%) versus the system's tight value on the identical papers.

**Why this — and not the Claude Code agent comparison — is the baseline that matters.** The agent
comparison in `eval/studies/agent_baseline/metrics_summary.xlsx` handed Claude Code *the system's own
engineered field-specs*; its near-tie with the system is therefore expected and is best read as an
**ablation of the rules/hints layer**, not a head-to-head with the status quo. This user study is
the status quo: an expert, raw tools, their own prompts, no system.

**Research question.** When a domain expert who has never seen evistream reproduces the full
CD004714 extraction with off-the-shelf Claude / Claude Code, how much does quality, time, and trust
**vary across experts**, and how does that distribution compare to the tight band evistream produces
on the same 29 papers?

---

## 2. Design

| Element | Decision |
|---|---|
| **Participants** | 3–5 domain experts **unfamiliar with evistream** (e.g. Carolina, Francisco). 3 = quotable floor, 5 = stronger band. |
| **Inclusion** | Prior systematic-review / clinical data-extraction experience; able to use Claude or Claude Code. |
| **Exclusion** | Anyone who has seen the system's prompts or the gold standard. |
| **Condition** | Single, naturalistic: off-the-shelf Claude **or** Claude Code (their choice), **their own prompts**. No prompting coaching. |
| **Task** | Extract the **entire** CD004714 review — all 29 papers × all 5 forms — into the supplied blank workbook, "as if it were going into a real review," stopping each item when they trust it. |
| **Coverage** | **Each participant does the full review** → everyone is scored on identical items → the cross-user spread is a clean between-user comparison. |

### What every participant receives (identical kit)
1. **All 29 papers** — markdown from `eval/sheets/markdown_perio/` (plus original PDFs if available).
2. **Blank pre-headered workbook** — one CSV per form (`templates/*.csv`), columns matching the
   scoring harness exactly (§6), so output is realistic *and* directly scorable.
3. **`templates/FORM_DEFINITIONS.md`** — the normal reviewer's data-extraction form: column name,
   data type, allowed values, plain definition, one example per field.
   **Deliberately withheld:** the system's engineered hints / rules / source-grounding instructions.
   Giving participants the same human-readable form a reviewer would normally get — but *not* the
   engineered spec — is exactly the variable under test.

### What we deliberately do NOT do
- We do not coach prompting or suggest a strategy.
- We do not show the gold standard or any system prompt.
- We do not split the corpus across people (would break the identical-items comparison).

---

## 3. What we measure

Per participant:
1. **Filled workbook** → per-form and overall **precision / recall / F1** vs gold (§6, same rubric as
   the system).
2. **Time** — total review time and per-session time (self-logged time log; plus, for Claude Code
   users, the parsed session logs giving duration / turns / cost, the same fields
   `agent_baseline` captured).
3. **Prompts** — every prompt verbatim (exported transcript), the count, and how the prompt evolved
   over the 29 papers.
4. **Self-report** (questionnaire) — struggle, trust, re-checking the paper, lingering doubt,
   prompting strategy, adoption barriers.

**Primary outcome:** distribution of per-form macro-F1 across participants vs the system's value on
the same 29 papers (overlay against the existing `metrics_summary.xlsx` system + agent columns).
**Secondary:** total-review-time distribution vs system runtime; iteration counts; prompt-strategy
taxonomy; **trust-vs-measured-accuracy gap** (self-rated accuracy vs measured F1).

---

## 4. Logistics — take-home over a deadline

- **Window:** ~7–10 days (confirm Tuesday).
- **Flow:** **Kickoff call** (consent + intake + hand over kit + walkthrough of one example row) →
  async self-paced work → **debrief call** (administer the questionnaire while fresh).
- **Pilot first:** run **one** participant end-to-end before recruiting the rest, to calibrate the
  instructions and the time estimate.
- **Effort & incentive:** the full review with raw tools is plausibly **6–15 hours** per person —
  decide compensation / incentive and a realistic deadline (Tuesday).

### Capture mechanism (no live observation in take-home)
Triangulate with four sources:
- **(a) Time log** — participant fills a per-session row: date, form/papers worked, start, stop,
  number of prompts. Template lives in `QUESTIONNAIRE.md` §B.
- **(b) Prompts** — Claude web: export the conversation or paste all prompts into the supplied doc.
  Claude Code: hand back the session directory; we parse it as in `agent_baseline`.
- **(c) Filled workbook** — the 5 CSVs.
- **(d) Post-questionnaire** — at debrief.

---

## 5. Returned-data layout

```
eval/studies/user_study/participants/<participant_id>/
    study_char.csv
    patient_pop.csv
    interventions.csv
    outcomes.csv
    risk_of_bias.csv
    prompts.txt              # or exported transcript / Claude Code session dir
    time_log.csv
    questionnaire.md         # filled
```
Anonymize: use `P01`, `P02`, … as `<participant_id>`; never store names alongside data.

---

## 6. Scoring — reuse the existing harness (no new metric code)

The participant workbook is scored exactly like the system, with the same field-typed comparison
strategies, study/arm matching, and metrics (P/R/F1, Cohen's κ, bootstrap 95% CI).

1. **Place** each participant CSV at `eval/sheets/ai sheets/user_study/<participant_id>/<form>.csv`
   (or score in place from `participants/<id>/` — `score_participant.py` accepts a directory).
2. **Required columns** (the templates pre-fill these headers; use `NR` for not-reported). The four
   harness-scored forms:
   - **study_char:** `Paper, country, setting, number_of_centres, trial_design, recruitment_period, funding_source, notes`
   - **patient_pop:** `Paper, age_arm_a, age_arm_a_sd, age_arm_b, age_arm_b_sd, pct_female_arm_a, pct_female_arm_b, diabetes_type, baseline_hba1c_arm_a, baseline_hba1c_arm_b, metabolic_control_level, duration_since_diabetes_dx, tobacco_use, alcohol_consumption, inclusion_criteria, exclusion_criteria, n_randomised, n_evaluated`
   - **interventions** (one row per arm): `Paper, arm_label, comparison_summary, intervention_description, n_in_arm, duration_of_followup, cochrane_subgroup_category`
   - **outcomes** (one row per outcome×timepoint×subgroup): `Paper, outcome_type, timepoint, subgroup, mean_arm1, sd_arm1, n_arm1, mean_arm2, sd_arm2, n_arm2`
3. **Run** `python eval/studies/user_study/score_participant.py <participant_id>`. This reuses
   `forms/base_form.py:run_form` + `forms/periodontitis.py:PERIODONTITIS_REGISTRY` (the exact path
   `agent_baseline/build_summary.py` uses) and scores against
   `eval/sheets/gt sheets/periodontitis.xlsx` (`gt_skiprows=[1]`). Validated end-to-end against the agent
   sheets: per-form F1 reproduces `metrics_summary.xlsx` exactly.
4. **Risk of bias:** a `perio_risk_of_bias` form config **does exist** in the registry, so ROB *can*
   be F1-scored (judgement enums via exact-match, reason text via the LLM judge). `score_participant.py`
   scores it when `risk_of_bias.csv` is present. Decision for Tuesday: include ROB in the headline
   numbers, or report it separately — the 16 reason fields are verbatim free-text and noisier than
   the four core forms.

---

## 7. Analysis & artifacts produced

- **Headline figure** — per-form macro-F1 box / dot plot: participants (scatter, showing the band)
  overlaid against the **system** point and the **agent baseline** point on the same 29 papers.
- **Table** — participants' F1 mean ± SD and min–max per form vs system; total-review-time
  distribution vs system runtime; mean prompt iterations (Q7); mean "re-checked the paper" (Q9);
  Q1–Q5 Likert means.
- **Result sentence (for §6 / abstract):** "Across *N* experts extracting the full 29-study review
  with off-the-shelf tools, per-form F1 ranged *X–Y* (mean ± SD) and took *A–B* hours; evistream
  scored *Z* on the same papers in *C* minutes — turning a wide, skill-dependent band into a tight
  one."

---

## 8. Ethics & threats to validity

- **Consent + anonymization** — intake item I6; store all artifacts under `P0x` only.
- **Threats and mitigations:**
  - *Small N* → frame as a "feedback session," report range, not significance.
  - *Fatigue / consistency drift over 29 papers* → measured directly (Q15); itself a finding.
  - *No live observation* → triangulate time via the time log + parsed Claude Code sessions.
  - *Ecological validity* → real corpus, real blank workbook, real reviewer definitions,
    naturalistic tool choice and prompting.
  - *Selection bias* → record LLM fluency (intake I3) as a covariate — which itself supports the
    variance story (skill-dependence is the point).
  - *Fairness* → participants get the normal reviewer form (definitions + examples) but not the
    engineered field-spec, which is precisely the variable the system contributes.

---

## 9. Open logistics to confirm at the Tuesday meeting

1. **Participants + count** — Carolina, Francisco + how many more (target 3–5); schedule kickoff +
   debrief calls.
2. **Deadline + incentive** — window length (recommend 7–10 days) and compensation for ~6–15 h.
3. **Pilot** — confirm a 1-person pilot first (recommended).
4. **ROB scoring** — a ROB form config exists and can score it; decide whether ROB joins the
   headline numbers or is reported separately (its 16 verbatim-reason fields are noisier).
5. **Prompt capture for Claude web** — export vs paste-into-doc; Claude Code users hand back the
   session directory.
