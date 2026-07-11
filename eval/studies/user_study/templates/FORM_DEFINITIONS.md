# CD004714 Data-Extraction Form — Field Definitions

This is the data-extraction form for the periodontitis-and-glycaemic-control systematic review
(Cochrane CD004714). For each paper, fill one row per study (Study Characteristics, Patient
Population), one row per **arm** (Interventions), one row per **outcome × timepoint × comparison**
(Continuous Outcomes), and one row per study (Risk of Bias).

**Conventions**
- Use `NR` when the paper does **not report** a value.
- Use the study's first-author + year as the `Paper` key (e.g. `Artese 2015`), matching the paper file name.
- For `enum` fields, choose **only** from the allowed values listed.
- Extract from the paper only — do not use outside knowledge.

> You may use Claude or Claude Code in whatever way you like, including writing your own prompts.
> Fill the spreadsheet as if it were going into a real review, and move on from a field once you
> trust the value.

---

## 1. Study Characteristics — `study_char.csv` (one row per study)

| Column | Type | Allowed values | Definition | Example |
|---|---|---|---|---|
| `Paper` | string | | First author + year (study key). | Artese 2015 |
| `country` | string | | Country where the study was conducted (from affiliations / setting). | Brazil |
| `setting` | string | | Setting type (hospital / primary care / university / NR). | hospital |
| `number_of_centres` | string | | Number of recruiting centres, sometimes with location. | 1 |
| `trial_design` | string | | Verbatim design description (n-arm, parallel/crossover, blinding). | 2-arm RCT |
| `recruitment_period` | string | | Recruitment dates if reported. | February 2011 to December 2013 |
| `funding_source` | string | | Funder(s) verbatim, or 'none declared' / 'NR'. | FAPESP |
| `notes` | string | | Free-text notes (sample-size calc, data limitations, conflicts). | HbA1c presented only in a graph |

## 2. Patient Population — `patient_pop.csv` (one row per study)

| Column | Type | Allowed values | Definition | Example |
|---|---|---|---|---|
| `Paper` | string | | First author + year (study key). | Artese 2015 |
| `age_arm_a` | float | | Mean age (years), intervention arm A. | 54.4 |
| `age_arm_a_sd` | float | | SD of age, arm A. | 5.8 |
| `age_arm_b` | float | | Mean age, arm B (usual care / control). | 52 |
| `age_arm_b_sd` | float | | SD of age, arm B. | 3.3 |
| `pct_female_arm_a` | float | | % female, arm A. | 56.3 |
| `pct_female_arm_b` | float | | % female, arm B. | 52 |
| `diabetes_type` | enum | T1DM; T2DM; mixed; NR | Diabetes type at enrolment. Use the base type; put qualifiers (e.g. 'poorly controlled') in notes. | T2DM |
| `baseline_hba1c_arm_a` | float | | Baseline HbA1c (%), arm A. | 7.1 |
| `baseline_hba1c_arm_b` | float | | Baseline HbA1c (%), arm B. | 8.2 |
| `metabolic_control_level` | enum | good; fair; poor; NR | Coarse classification of baseline metabolic control, derived from mean baseline HbA1c. | poor |
| `duration_since_diabetes_dx` | string | | Time since diabetes diagnosis (verbatim). | ≥3 yrs |
| `tobacco_use` | string | | Tobacco-use status verbatim (may be 'NR' or 'excluded by criteria'). | none (excluded) |
| `alcohol_consumption` | string | | Alcohol use verbatim or NR. | NR |
| `inclusion_criteria` | string | | Verbatim inclusion criteria. | ≥35 yrs, T2DM ≥3 yrs, severe chronic periodontitis, ≥15 teeth |
| `exclusion_criteria` | string | | Verbatim exclusion criteria. | pregnant, smokers, BMI>35, recent periodontal/antibiotic therapy |
| `n_randomised` | int | | Number randomised (total across arms). | 24 |
| `n_evaluated` | string | | Number evaluated at follow-up (may be heterogeneous). | 24 at 6 mths |

## 3. Interventions — `interventions.csv` (one row per arm)

| Column | Type | Allowed values | Definition | Example |
|---|---|---|---|---|
| `Paper` | string | | First author + year (study key). | Artese 2015 |
| `arm_label` | string | | Unique within study (e.g. 'Gp A (n=12)', 'Group 1'). | Gp A (n=12) |
| `comparison_summary` | string | | One-line summary of the comparison the paper makes. | SRP vs supragingival scaling |
| `intervention_description` | string | | Verbatim arm description (procedure, anaesthesia, instruments, frequency). | supragingival scaling with ultrasonic device, single 60 min appointment |
| `n_in_arm` | int | | Participants randomised to this arm. | 12 |
| `duration_of_followup` | string | | Total follow-up duration. | 6 mths |
| `cochrane_subgroup_category` | enum | SI_alone; SI_plus_systemic_local_antimicrobial; SI_plus_mouthrinse; usual_care; supragingival_only | Treatment-type class of the arm, derived from the paper's arm description. | SI_alone |

## 4. Continuous Outcomes — `outcomes.csv` (one row per outcome × timepoint × comparison)

| Column | Type | Allowed values | Definition | Example |
|---|---|---|---|---|
| `Paper` | string | | First author + year (study key). | Bukleta 2018 |
| `outcome_type` | enum | HbA1c; CAL; PPD; BOP; PI; GI | Outcome measured (continuous). | HbA1c |
| `timepoint` | enum | 3-4_months; 6_months; 12_months | Timepoint at which the outcome was measured. | 3-4_months |
| `subgroup` | enum | SI_vs_usual_care; SI_plus_antimicrobial; SI_plus_mouthrinse | Which comparison this row belongs to, derived from the control arm. | SI_vs_usual_care |
| `mean_arm1` | float | | Mean of the intervention (subgingival instrumentation) arm. | 8.03 |
| `sd_arm1` | float | | SD of the intervention arm. | 1.67 |
| `n_arm1` | int | | N of the intervention arm at this timepoint. | 50 |
| `mean_arm2` | float | | Mean of the control arm (usual care / no active treatment). | 7.69 |
| `sd_arm2` | float | | SD of the control arm. | 2.09 |
| `n_arm2` | int | | N of the control arm. | 50 |

*(The review-computed effect-size columns — effect_measure, effect_value, CI — are NOT extracted
from the paper and are not part of this task.)*

## 5. Risk of Bias — `risk_of_bias.csv` (one row per study)

For each Cochrane domain: a judgement (`low` / `unclear` / `high`) and the verbatim supporting
sentence(s) from the paper.

| Column | Type | Allowed values | Definition |
|---|---|---|---|
| `Paper` | string | | First author + year (study key). |
| `random_sequence_generation_judgment` | enum | low; unclear; high | Random sequence generation. |
| `random_sequence_generation_reason` | string | | Verbatim supporting sentence(s). |
| `allocation_concealment_judgment` | enum | low; unclear; high | Allocation concealment. |
| `allocation_concealment_reason` | string | | Verbatim supporting sentence(s). |
| `blinding_participants_judgment` | enum | low; unclear; high | Blinding of participants/personnel. |
| `blinding_participants_reason` | string | | Verbatim supporting sentence(s). |
| `blinding_clinical_operator_judgment` | enum | low; unclear; high | Blinding of the clinical operator. |
| `blinding_clinical_operator_reason` | string | | Verbatim supporting sentence(s). |
| `blinding_outcome_assessor_judgment` | enum | low; unclear; high | Blinding of the outcome assessor. |
| `blinding_outcome_assessor_reason` | string | | Verbatim supporting sentence(s). |
| `incomplete_outcome_data_judgment` | enum | low; unclear; high | Incomplete outcome data. |
| `incomplete_outcome_data_reason` | string | | Verbatim supporting sentence(s). |
| `selective_reporting_judgment` | enum | low; unclear; high | Selective reporting. |
| `selective_reporting_reason` | string | | Verbatim supporting sentence(s). |
| `other_bias_judgment` | enum | low; unclear; high | Other bias. |
| `other_bias_reason` | string | | Verbatim supporting sentence(s). |

*(Risk of Bias is scored by the same harness; the judgement columns are matched exactly and the
reason columns via the LLM judge.)*
