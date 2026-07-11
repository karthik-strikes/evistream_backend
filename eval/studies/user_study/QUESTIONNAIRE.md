# Naive-Expert User Study — Questionnaire & Logs

Participant-facing instrument. Sections **A** (intake) and **B** (time log) are filled during the
study; section **C** is administered at the debrief call. The *rationale* notes are for the research
team and are not read aloud to participants.

> **Participant ID:** `P0__`   **Date started:** ______   **Tool used:** Claude web / Claude Code / both

---

## A. Intake / screener (kickoff call, ~2 min)

| # | Question | Answer | *(rationale — internal)* |
|---|---|---|---|
| I1 | Your role / title and field? | | *participant table* |
| I2 | Prior systematic-review or clinical data-extraction experience? Roughly how many studies? | | *inclusion; F1 covariate* |
| I3 | How often do you use Claude / Claude Code / other LLMs? **(Daily / Weekly / Monthly / Rarely)** | | *fluency covariate that explains variance* |
| I4 | Which will you use for this task? **(Claude web / Claude Code / both)** | | *records condition* |
| I5 | Have you ever seen the evistream system or its extraction prompts? **(Yes / No)** | | ***exclusion gate — must be No*** |
| I6 | Do you consent to us collecting your prompts, timing, and filled workbook for anonymized research? **(Yes / No)** | | *ethics / consent* |

---

## B. Time log (fill one row per working session)

Save as `time_log.csv` with these columns:

```
date, form_or_papers_worked, start_time, stop_time, n_prompts_this_session, notes
```

| date | form / papers worked | start | stop | # prompts | notes |
|---|---|---|---|---|---|
| | | | | | |
| | | | | | |

**Also save your prompts:** Claude web — export the conversation or paste every prompt into
`prompts.txt`. Claude Code — keep the session directory and hand it back.

---

## C. Post-task questionnaire (debrief call, ~10 min)

### Section 1 — Overall experience (1 = strongly disagree … 5 = strongly agree)

| # | Statement | 1 | 2 | 3 | 4 | 5 |
|---|---|---|---|---|---|---|
| Q1 | The values I extracted with the tool were accurate. | ☐ | ☐ | ☐ | ☐ | ☐ |
| Q2 | The tool saved me time versus extracting by hand. | ☐ | ☐ | ☐ | ☐ | ☐ |
| Q3 | It was easy to get the tool to give me what I needed. | ☐ | ☐ | ☐ | ☐ | ☐ |
| Q4 | I trust these outputs enough to put them in a real review. | ☐ | ☐ | ☐ | ☐ | ☐ |
| Q5 | I would use this approach in my next extraction task. | ☐ | ☐ | ☐ | ☐ | ☐ |

*(rationale: Q1–Q5 give Likert means; Q1 vs measured F1 = the over-confidence gap.)*

### Section 2 — Difficulty, trust, and verification

- **Q6.** Where did you struggle most across the review? _(open)_
- **Q7.** For a typical paper, how many rounds of prompting before you trusted the result? _(number)_
- **Q8.** Which fields do you have lingering doubt about, and why? _(open)_
- **Q9.** How often did you go back and re-check the paper to verify the tool?
  **(Never / Sometimes / Often / For almost every field)**
- **Q10.** When the tool and the paper seemed to disagree, what did you do? _(open)_

*(rationale: Q7/Q9 are the iteration + verification burden the harness's source-grounding removes.)*

### Section 3 — Prompting strategy

- **Q11.** What was your overall strategy?
  **(one big prompt / field-by-field / iterate on a draft / one reusable prompt across papers / other)**
- **Q12.** Did you build up your own rules, examples, or definitions for the model over time?
  **(Yes / No)** — if yes, describe. _(open)_
- **Q13.** Did your prompt change much between the first papers and the last? How? _(open)_

*(rationale: Q12/Q13 are gold — if good users **reinvent** the system's field-spec by hand, that is
direct evidence for what the harness automates.)*

### Section 4 — Take-home specifics

- **Q14.** Roughly how many hours total, across how many sessions? _(short text)_
- **Q15.** Did fatigue or consistency drift set in over the 29 papers? Where? _(open)_

### Section 5 — Adoption barriers

- **Q16.** What didn't work, or was most frustrating? _(open)_
- **Q17.** What would stop you from adopting a tool like this? _(open)_

---

## Reporting plan (internal)

- Means ± SD for Q1–Q5; the **Q1-vs-measured-F1** over-confidence gap.
- Distributions of Q7 (iterations) and Q9 (verification burden).
- Total time from Q14 cross-checked against the `time_log.csv`.
- A prompting taxonomy from Q11–Q13.
- One strong verbatim quote each from Q6, Q8, Q16, Q17.
