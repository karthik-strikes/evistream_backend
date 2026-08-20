# Issue: record discovery emits one verbatim quote per key cell

**Repo:** `/home/ubuntu/evistream` — `backend/` is Python (FastAPI + DSPy + litellm, Anthropic via
Bedrock), `frontend/` is Next.js.
**Evidence:** `backend/eval/profiling/table_extraction_timing.ipynb`, run 2026-08-18 14:39.
**Status:** unsolved. Two designs were considered and both have known problems (§5).

---

## 1 · The system, in one paragraph

A "table field" is extracted by a keyed three-call pipeline in
`backend/dspy_components/runtime_builders.py`:

1. **record discovery** — find every row the paper reports, emitting only the *key* columns
   (the composite key that identifies a row). `_build_record_discovery_sig_def:960`
2. **recall audit** — a second pass that reads the candidate row set and returns only rows it
   *missed*, each with an `evidence_quote` that is verified against the document.
   `_build_recall_audit_sig_def:1196`
3. **slot filling** — the row set is now frozen; fill the non-key (attribute) columns for exactly
   those rows. `_build_set_slot_fill_sig_def`

Every cell of the final stored row is a `{value, source_text, status}` envelope — key columns and
value columns alike. The merge normalizes both shapes so downstream code sees exactly one.

---

## 2 · The problem

`_build_record_discovery_sig_def` (`runtime_builders.py:1033-1036`) tells the model:

> SOURCE GROUNDING: each anchor cell must be `{"value": <cell_value>, "source_text": <quote>}`
> where the quote is ONE sentence (≤30 words) copied VERBATIM from the document that grounds that
> cell — never paraphrased.

On the profiled form the composite key is **7 columns wide** and the paper yielded **76 rows**, so
this asks for **532 verbatim quotes** before a single measurement is extracted.

### Measured cost

The whole run is decode-bound — 616.1 s of 616.3 s wall clock is LLM latency, our Python is 0.16 s,
queue wait 0 s, retries 0, truncation 0. Output tokens ÷ ~150 tok/s **is** the wall clock.

| step | calls | busy | share | out_tok |
|---|---|---|---|---|
| **record_discovery** | 1 | **260.3 s** | **42 %** | **41,164** |
| recall_audit | 1 | 125.8 s | 20 % | 15,455 |
| slot_fill_set | 2 | 230.0 s | 37 % | 35,443 |

That is 542 output tokens per discovered row, of which an estimated ~24k of the 41k call total is
the quotes themselves. **Roughly 160 s of a 616 s run.**

Form under test: `d9f6e1d9-449e-4aaa-a438-67923f77a448` (*Acute Dental Pain — Dichotomous
Outcomes*), field `dichotomous_outcomes`, `n_key=7`, `n_attr=5`, key columns
`adverse_effect × followup_timepoint × intervention × intervention_detail × outcome_other ×
outcome_type × population_type`.

---

## 3 · Already shipped (do not re-solve)

Two adjacent latency fixes landed 2026-08-18 and are **out of scope** here:

- Slot filling no longer echoes the 7 identity columns in its answer; it copies back an opaque
  `row_id` instead (~9.1k output tokens saved). Matching is still by identity, never position.
- `_MAX_RECORDS_PER_CALL = 40` removed — batch size now comes from the model's output budget alone
  (200 rows/call for this form), and `_TOKENS_PER_CELL` moved 60 → 70 to match measured output.

Untouched and also out of scope: the recall audit's 126 s, and a conditional
skip of the redundant prompt-cache warm call.

---

## 4 · Hard constraints — what a solution must not break

### 4a. Nothing verifies these quotes today

**This is the most important fact about the problem.** There is no gate on discovery key-cell
quotes anywhere. Only two places resolve a quote against the document:

- `_reconcile_record_set:1626` calls `locate_source(quote, index, threshold=0.65)` and *rejects the
  row* — but only for rows the **recall auditor** proposes, using its own `_EVIDENCE_KEY`
  (`"evidence_quote"`, `:1193`), never a discovery row's `source_text`.
- `source_linker.enrich_extraction_results` (`utils/source_linker.py:451-465`) reads the quote to
  attach page/bbox, but it is **enrichment, not a gate**: on failure it silently falls back to
  `locate_value:528` and attaches nothing. No rejection, no status change, no log.

So today's 532 quotes are unverified model output. A fabricated quote is stored and displayed
exactly like a real one. **Any redesign is therefore free to be cheaper without being *less*
grounded — but it should not pass up the chance to be *more* grounded.**

### 4b. Every cell must keep a `source_text`

Grounding is a core product feature. Three concrete regressions follow from a blank key-cell quote:

1. **`NA` silently reclassifies to `NR`.** `absence.stamp` (`utils/absence.py:157`) downgrades a
   bare `"NA"` to `not_reported` unless `has_grounding` (`:146`) sees a non-blank quote *or* `"NA"`
   is a declared option. On a wide composite key `NA` is a **legitimate identity value** (a
   global-assessment row has no `adverse_effect`) — this is the premise of
   `tests/test_recall_audit_gate.py`. Knock-on: display label flips NR/NA, `field_is_empty`
   coverage counts shift, and an AI cell now conflicts with a reviewer's `NA` in adjudication.
   **There is no test covering a key cell with value `NA` and no quote.**
2. **PDF highlight / bbox is lost.** `_enrich_cell` falls through to `locate_value`, which needs a
   "distinctive" token (decimal, `%`, 2+ digit int, uppercase code) and returns `None` for typical
   normalized key values (`2h_to_24h`, `placebo`). Confidence drops from `quote_exact` (≥0.999) to
   `value_text` 0.7 / `value_table` 0.8 where it hits at all.
3. **Frontend affordances degrade** (gracefully — no crashes): the Quote chip disappears from key
   columns in `LongFormatTable.tsx`, `<SourceEvidence>` renders nothing and jump-to-evidence is
   disabled in `UnifiedFieldCard.tsx`, and key-column quotes vanish from the synthesis
   `EvidenceDrawer.tsx`.

### 4c. Three prompt sites contradict each other if edited alone

Changing `:1033-1036` alone leaves the discovery prompt self-contradictory:

| # | Site | Why it fights back |
|---|---|---|
| A | `runtime_builders.py:1033-1036` | the SOURCE GROUNDING paragraph itself |
| B | `runtime_builders.py:591` — `'Table Columns (… EACH CELL VALUE MUST itself be {"value": ..., "source_text": ...}):'` | emitted by `_compose_field_desc:470`, gated on `subform_fields_data` and **not** on `source_grounded` — so discovery gets it even though the builder sets `source_grounded=False`. Ordering puts `description` first, so new "no per-cell quote" text lands *before* this line contradicts it |
| C | `runtime_builders.py:348` `_GROUNDING_PLACEHOLDER` | per-column `examples` on key subfields render `{"value": …, "source_text": "<one verbatim sentence…>"}`. The builder strips the *parent's* examples but copies key subfields whole |

**B and C are shared code.** `_compose_field_desc` also renders the `single_pass` strategy and the
agentic path (`agentic_table.py:67, 881-886`), whose grounding gate (`agentic_table.py:717-735`)
fails and re-repairs every cell that has a value but no quote. **Any edit to B/C must be scoped to
the discovery path** — e.g. a new flag on the field dict, defaulting to today's behaviour.

### 4d. Pilot calibration re-injects the contract

Thumbs-up feedback on a key column is harvested by `app/api/v1/pilot.py:138,146` into few-shot
examples carrying `source_text`, routed to the **discovery** signature
(`backend/schemas/config.py:277-297`) and rendered through `utils/pilot_feedback.py:105`. A
solution has to close this loop or it will quietly restore the old contract.

### 4e. Prompt text is pinned, deliberately

`tests/test_two_stage_extractor.py::TestPromptTextIsPinned` pins four literals; the SOURCE
GROUNDING sentence is **not** among them, so it is editable — but the class docstring carries a
standing rule: *reword the prompt only when the change has been measured on real papers, and update
these strings in the commit that publishes the numbers.* Field names (`row_anchor`,
`candidate_row_plan`, `filled_rows`, `missing_rows`, `rows_to_fill`) are doubly frozen: they are in
the prompt **and** they are the response parse keys.

### 4f. Structured-output gotcha

A `Dict[str, str]` output field comes back **empty** from structured output in this stack. Any
mapping must be a closed `List` of objects with a `Literal` enum for the key field.

---

## 5 · Designs considered, and why each is unresolved

### Design A — one `evidence_quote` per row

Key cells become bare values; the row carries a single quote (mirroring the recall audit's
`_EVIDENCE_KEY`). Merge fans that quote into all 7 key cells' `source_text`, so §4b is fully
satisfied and no downstream consumer changes. ~7× fewer quotes.

**Rejected by the reviewer, with a concrete counterexample.** A table row's identity is routinely
assembled from *different parts of the page*:

```
intervention → table header
dose         → table header
outcome      → row label
timepoint    → column header
```

There may be **no single ≤30-word verbatim span that supports all seven key columns**. Forcing one
either degrades grounding quality (the quote genuinely doesn't support 6 of the 7 cells) or makes
discovery *harder* — the model hunts for an impossible sentence, or declines to emit rows.

### Design B — a value-level evidence legend

Emit each **distinct key value once** with its own quote, then list rows as bare values; cells
inherit by normalized `(column, value)` lookup:

```
identity_evidence: [{"column": "intervention", "value": "Ibuprofen 400 mg", "quote": "…"}, …]
rows: [{"intervention": "Ibuprofen 400 mg", "followup_timepoint": "8 hours", …}, …]
```

Rationale: a per-cell quote has never grounded the *row* — the prompt says it grounds *that cell*,
i.e. that this value exists in the paper — and that evidence is identical every time the value
recurs. On the profiled paper `population_type` was one constant string across all 76 rows, so the
model wrote 76 separate quotes for it. Distinct values ≈ **33** vs 532 cells ≈ **16× fewer**, and
each column keeps a quote drawn from its own part of the page, which answers Design A's objection
directly.

**Open problems with Design B:**

1. Evidence becomes per-value, not per-row-per-value. If a value legitimately has different
   evidence in different rows, all its cells show the same quote.
2. It does not evidence the *combination* ("does the paper actually report A × B × C?"). Nothing
   today does either — that is the recall audit's job — but it is worth being explicit about.
3. `NA` identity values have no quote in the paper **by definition**, so they get no legend entry
   and hit the §4b(1) `NA → NR` downgrade. Needs explicit handling regardless of design.
4. The legend→cell step is a **string match, not evidence**. It asserts "this cell's value is that
   value" and nothing more.

---

## 6 · The unexploited opportunity

Because Design B collapses 532 cells to ~33 distinct values, verification becomes affordable for
the first time — and both checks already exist, at **zero LLM cost** (local fuzzy matching over the
markdown plus the datalab bbox index):

| Check | Function | Question |
|---|---|---|
| Quote resolves in the document | `locate_source` (`utils/source_linker.py:271`), the call the audit gate uses | real sentence, or invented? |
| Quote mentions the value | `_quote_mentions_identity` (`runtime_builders.py:1584`) | evidence *for this value*, or an unrelated real sentence? |

The second is not hypothetical: in the profiled run the recall audit rejected **12 of 16** proposals
as `quote_unrelated` — quotes that were real but named none of the row's identity values. That exact
failure mode currently runs unchecked across discovery's 532 cells.

Suggested failure handling: **never drop the row** (discovery defines the row set; deleting rows
over a bad quote is data loss). Blank the quote, mark the cell ungrounded, and log a report dict the
way `_reconcile_record_set` does — which would yield the first real number for how often key-cell
grounding is fabricated.

---

## 7 · What to decide

1. Design B, Design A, a hybrid, or something else — given §4b (every cell keeps a quote), §4c
   (three prompt sites, two of them shared with other strategies), and the scattered-identity case
   in §5.
2. Whether to add the §6 verification gate in the same change, and what to do with a cell whose
   evidence fails to resolve.
3. How `NA` identity values keep `not_applicable` status without a quote.
4. What accuracy measurement gates the change, given §4e. The baseline to beat: **80 rows** from
   this paper, run recorded at `backend/eval/profiling/outputs/`. Rows found must not drop.

**Target:** ~24k of 41k output tokens on the discovery call, ≈ **160 s of a 616 s run**.
