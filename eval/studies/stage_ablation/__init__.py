"""Experiment 1 (D3a) — decomposed (multi-signature) vs single-prompt extraction.

Tests the *question-decomposition* design: extracting a form's fields via several
focused signatures (the production pipeline) vs. collapsing every field into ONE
signature / ONE LLM call. Only the grouping changes — per-field descriptions,
hints, rules, examples, source-grounding, the backing model, and the papers are
all held constant, so this isolates decomposition from the field-spec ablation (D1).
"""
