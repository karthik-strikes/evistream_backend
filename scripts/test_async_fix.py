"""
Phase 0 validation: proves async_dspy_forward (via .acall()) works end-to-end.

Run:
    source ~/.bashrc && conda activate topics
    cd backend
    python scripts/test_async_fix.py

Pass criteria:
  - Single call hits real LLM (takes >0.5s, not a cache/fallback)
  - 300 concurrent calls complete with all successes
  - Wall time for 300 calls is close to 1 call (true parallelism via .acall())
  - No RuntimeError, no thread pool exhaustion
  - All 300 results written to scripts/results/concurrency_results.csv
"""

import asyncio
import csv
import json
import os
import pathlib
import random
import sys
import time
import traceback
import uuid

sys.path.insert(0, ".")


# ---------------------------------------------------------------------------
# DSPy setup
# ---------------------------------------------------------------------------

def setup_dspy():
    """Initialize DSPy with Claude, bypassing all caches."""
    import dspy
    from dotenv import load_dotenv

    env_path = pathlib.Path(__file__).parent.parent / ".env"
    load_dotenv(dotenv_path=env_path, override=True)

    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        raise RuntimeError(f"ANTHROPIC_API_KEY not found in {env_path}")
    print(f"API key loaded: {key[:12]}...")

    # cache=False — prevents litellm disk cache from serving stale responses
    lm = dspy.LM(
        model="anthropic/claude-sonnet-4-6",
        max_tokens=4096,
        temperature=0.0,
        cache=False,
    )
    dspy.configure(lm=lm)
    print(f"DSPy LM: {lm.model} (cache=False)")


# ---------------------------------------------------------------------------
# Dynamic prompt generator
# ---------------------------------------------------------------------------

_STUDY_DESIGNS = [
    "randomised controlled trial", "prospective cohort study",
    "retrospective case-control study", "cross-sectional survey",
    "multi-centre observational study", "single-arm pilot study",
    "double-blind placebo-controlled trial", "pragmatic cluster RCT",
]

_CONDITIONS = [
    ("oral potentially malignant disorder (OPMD)", "opmd"),
    ("oral squamous cell carcinoma (OSCC)", "oral_cancer"),
    ("oral leukoplakia", "opmd"),
    ("oral submucous fibrosis", "opmd"),
    ("erythroplakia", "opmd"),
    ("oral lichen planus", "opmd"),
    ("verrucous carcinoma of the oral cavity", "oral_cancer"),
    ("recurrent aphthous stomatitis", "other"),
    ("chronic periodontitis", "other"),
    ("dental caries in high-risk adults", "other"),
]

_SETTINGS = [
    "a university dental hospital",
    "a community oral health clinic",
    "a tertiary referral cancer centre",
    "a rural primary care dental practice",
    "a specialist maxillofacial unit",
    "a veterans' healthcare dental department",
    "a municipal public health dental screening programme",
]

_COUNTRIES = [
    "India", "Taiwan", "Brazil", "the United Kingdom", "the United States",
    "China", "Sri Lanka", "Australia", "Germany", "South Korea",
]

_SELECTION_METHODS = [
    "consecutive enrolment",
    "random sampling from clinic registers",
    "purposive sampling stratified by lesion severity",
    "convenience sampling of attendees",
    "population-based registry recruitment",
]

_EXCLUSION_PHRASES = [
    "prior biopsy within 6 months",
    "concurrent systemic immunosuppression",
    "pregnancy or lactation",
    "prior head-and-neck radiotherapy",
    "inability to provide informed consent",
    "concurrent malignancy at any site",
    "tobacco cessation for more than 10 years",
]

_TOBACCO_HABITS = [
    "smokeless tobacco (betel quid) use in {pct}% of participants",
    "cigarette smoking (≥10 pack-years) in {pct}% of participants",
    "combined smoked and smokeless tobacco in {pct}% of participants",
    "never-tobacco history in all participants",
    "alcohol co-use reported in {pct}% of the cohort",
]

_FOLLOW_UP = [
    "Participants were followed for {mo} months.",
    "Median follow-up was {mo} months (IQR {iqr}).",
    "All participants completed a {mo}-month follow-up visit.",
    "Follow-up duration ranged from {lo} to {hi} months.",
]


def make_dynamic_prompt(i: int) -> str:
    """
    Build a maximally varied synthetic clinical study abstract snippet.
    Every dimension is independently randomised so no two prompts are alike.
    A UUID is appended to defeat any residual LLM-side caching.
    """
    rng = random.Random(i * 9973 + 31337)   # deterministic per index, but unique

    n_total    = rng.randint(28, 1200)
    n_female   = rng.randint(int(n_total * 0.1), int(n_total * 0.9))
    n_male     = n_total - n_female
    pct_female = round(n_female / n_total * 100, 1)
    mean_age   = rng.randint(22, 74)
    sd_age     = rng.randint(4, 18)
    age_range  = f"{mean_age - rng.randint(10, 20)}–{mean_age + rng.randint(10, 20)}"

    design              = rng.choice(_STUDY_DESIGNS)
    condition, _ctype   = rng.choice(_CONDITIONS)
    setting             = rng.choice(_SETTINGS)
    country             = rng.choice(_COUNTRIES)
    selection           = rng.choice(_SELECTION_METHODS)
    exclusion1          = rng.choice(_EXCLUSION_PHRASES)
    exclusion2          = rng.choice([e for e in _EXCLUSION_PHRASES if e != exclusion1])
    tobacco_tmpl        = rng.choice(_TOBACCO_HABITS)
    tobacco_pct         = rng.randint(20, 95)
    tobacco_stmt        = tobacco_tmpl.format(pct=tobacco_pct)
    follow_tmpl         = rng.choice(_FOLLOW_UP)
    follow_mo           = rng.randint(3, 60)
    follow_iqr          = f"{follow_mo - rng.randint(1,5)}–{follow_mo + rng.randint(1,5)}"
    follow_lo, follow_hi = follow_mo - rng.randint(2, 10), follow_mo + rng.randint(2, 10)
    follow_stmt         = follow_tmpl.format(
        mo=follow_mo, iqr=follow_iqr, lo=follow_lo, hi=follow_hi
    )

    # Optionally add a sub-group note
    subgroup = ""
    if rng.random() > 0.5:
        sg_n = rng.randint(10, n_total // 2)
        subgroup = (
            f" A sub-group of {sg_n} participants with high-grade dysplasia "
            f"underwent additional cytological analysis."
        )

    # Optionally mention comorbidities
    comorbidity = ""
    if rng.random() > 0.6:
        comorbidities = ["type-2 diabetes", "hypertension", "HIV", "hepatitis B"]
        cmb = rng.choice(comorbidities)
        cmb_pct = rng.randint(5, 35)
        comorbidity = f" Co-morbid {cmb} was present in {cmb_pct}% of enrolled patients."

    text = (
        f"We conducted a {design} at {setting} in {country} enrolling patients with {condition}. "
        f"A total of {n_total} participants were recruited via {selection} "
        f"({n_female} female [{pct_female}%], {n_male} male; "
        f"mean age {mean_age} years, SD {sd_age}, range {age_range}). "
        f"Exclusion criteria included {exclusion1} and {exclusion2}. "
        f"{tobacco_stmt.capitalize()}. "
        f"{follow_stmt}{subgroup}{comorbidity} "
        f"[run-id:{uuid.UUID(int=i + rng.randint(0, 2**32))}]"
    )
    return text


# ---------------------------------------------------------------------------
# Individual test helpers
# ---------------------------------------------------------------------------

def make_unique_text(base: str) -> str:
    return f"{base} [run-id:{uuid.uuid4()}]"


async def test_single_call():
    """Single real LLM call — must take >0.5s to prove it hit the network."""
    from dspy_components.tasks.patient_population.modules import AsyncPatientPopulationExtractor

    extractor = AsyncPatientPopulationExtractor()
    test_text = make_dynamic_prompt(0)

    print("\n[1] Single call test (patient_population extractor)...")
    print(f"    Prompt preview: {test_text[:120]}...")
    t0 = time.time()
    result = await extractor(test_text)
    elapsed = time.time() - t0

    print(f"    Elapsed: {elapsed:.2f}s")
    print(f"    Result keys: {list(result.keys()) if result else 'empty'}")

    assert result is not None, "Result is None"
    assert elapsed > 0.5, (
        f"Elapsed {elapsed:.2f}s is suspiciously fast — "
        "likely a cache hit or fallback, not a real LLM call"
    )
    print("    PASS")


async def test_combiner():
    """Test a combiner (uses self.combiner not self.extract) — different code path."""
    import dspy
    import json
    from utils.dspy_async import async_dspy_forward
    from dspy_components.tasks.patient_population.signatures import CombinePatientPopulationCharacteristics

    combiner_cot = dspy.ChainOfThought(CombinePatientPopulationCharacteristics)

    # Use a realistic-ish partial result rather than all-NR
    partial_pop  = json.dumps({"population": {"opmd": {"selected": True, "comment": "oral lichen planus"}}})
    partial_demo = json.dumps({"patient_selection_method": "consecutive", "population_ses": "NR"})
    partial_age  = json.dumps({"age_central_tendency": {"mean": {"selected": True, "value": "48"}},
                               "age_variability": {"sd": {"selected": True, "value": "11"}}})
    partial_base = json.dumps({"baseline_participants": {"total": {"selected": True, "value": "312"},
                                                         "female_n": {"selected": True, "value": "178"}}})
    partial_cond = json.dumps({"target_condition": {"opmd": {"selected": True, "comment": ""}},
                               "target_condition_severity": "moderate", "target_condition_site": "buccal mucosa"})

    print("\n[2] Combiner call test (self.combiner code path)...")
    t0 = time.time()
    outputs = await async_dspy_forward(
        combiner_cot,
        patient_population_json=partial_pop,
        selection_demographics_json=partial_demo,
        age_characteristics_json=partial_age,
        baseline_json=partial_base,
        target_condition_json=partial_cond,
    )
    elapsed = time.time() - t0

    print(f"    Elapsed: {elapsed:.2f}s")
    print(f"    Output keys: {list(outputs.keys())[:5]}")
    assert elapsed > 0.5, f"Elapsed {elapsed:.2f}s — suspiciously fast"
    print("    PASS")


async def test_template_extractor():
    """Test a template-generated (task_*) extractor."""
    import glob
    import importlib

    task_dirs = sorted(glob.glob("dspy_components/tasks/task_*/modules.py"))
    if not task_dirs:
        print("\n[3] No task_* modules found — SKIP")
        return

    mod_path = task_dirs[0].replace("/", ".").replace(".py", "")
    mod = importlib.import_module(mod_path)
    extractor_cls = None
    for name in dir(mod):
        if name.startswith("Async") and name.endswith("Extractor"):
            extractor_cls = getattr(mod, name)
            break

    if extractor_cls is None:
        print(f"\n[3] No Async*Extractor found in {task_dirs[0]} — SKIP")
        return

    print(f"\n[3] Template extractor test ({extractor_cls.__name__})...")
    extractor = extractor_cls()
    test_text = make_dynamic_prompt(999)
    print(f"    Prompt preview: {test_text[:120]}...")
    t0 = time.time()
    result = await extractor(test_text)
    elapsed = time.time() - t0

    print(f"    Elapsed: {elapsed:.2f}s")
    print(f"    Result keys: {list(result.keys())[:5] if result else 'empty'}")
    assert elapsed > 0.5, f"Elapsed {elapsed:.2f}s — suspiciously fast"
    print("    PASS")


# ---------------------------------------------------------------------------
# CSV writer
# ---------------------------------------------------------------------------

def write_csv(rows: list[dict], out_path: pathlib.Path):
    """Write concurrency results to CSV."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "call_id", "input_preview", "input_length_chars",
        "success", "elapsed_s",
        "result_keys", "result_key_count", "result_json_snippet",
        "error",
    ]
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    print(f"    CSV written → {out_path}  ({len(rows)} rows)")


# ---------------------------------------------------------------------------
# Concurrency stress test
# ---------------------------------------------------------------------------

async def test_concurrency(n: int = 300):
    """Fire n calls simultaneously — wall time should be ~1 call, not n×."""
    from dspy_components.tasks.patient_population.modules import AsyncPatientPopulationExtractor

    extractor = AsyncPatientPopulationExtractor()

    print(f"\n[4] Concurrency test: {n} simultaneous calls (dynamic prompts)...")

    prompts = [make_dynamic_prompt(i) for i in range(n)]

    # Track per-call timing
    call_start = [0.0] * n
    call_end   = [0.0] * n

    async def timed_call(i: int):
        call_start[i] = time.time()
        result = await extractor(prompts[i])
        call_end[i] = time.time()
        return result

    t0      = time.time()
    results = await asyncio.gather(*[timed_call(i) for i in range(n)], return_exceptions=True)
    elapsed = time.time() - t0

    errors    = [(i, r) for i, r in enumerate(results) if isinstance(r, Exception)]
    successes = [(i, r) for i, r in enumerate(results) if not isinstance(r, Exception)]

    print(f"    Wall time  : {elapsed:.2f}s for {n} calls")
    print(f"    Successes  : {len(successes)} / {n}")
    if errors:
        print(f"    Errors ({len(errors)}): first = {errors[0][1]}")

    # Build CSV rows
    rows = []
    for i, result in enumerate(results):
        is_exc  = isinstance(result, Exception)
        prompt  = prompts[i]
        elapsed_i = round(call_end[i] - call_start[i], 4) if call_end[i] else 0.0

        if is_exc:
            row = {
                "call_id"            : i,
                "input_preview"      : prompt[:120],
                "input_length_chars" : len(prompt),
                "success"            : False,
                "elapsed_s"          : elapsed_i,
                "result_keys"        : "",
                "result_key_count"   : 0,
                "result_json_snippet": "",
                "error"              : str(result),
            }
        else:
            keys        = list(result.keys()) if result else []
            json_snippet = json.dumps(result)[:200] if result else ""
            row = {
                "call_id"            : i,
                "input_preview"      : prompt[:120],
                "input_length_chars" : len(prompt),
                "success"            : True,
                "elapsed_s"          : elapsed_i,
                "result_keys"        : "|".join(keys),
                "result_key_count"   : len(keys),
                "result_json_snippet": json_snippet,
                "error"              : "",
            }
        rows.append(row)

    out_path = pathlib.Path(__file__).parent / "results" / "concurrency_results.csv"
    write_csv(rows, out_path)

    assert len(errors) == 0, f"{len(errors)} / {n} calls raised exceptions"
    assert elapsed < 90, f"Wall time {elapsed:.2f}s — too slow, likely sequential not parallel"
    print("    PASS")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main():
    print("=" * 60)
    print("async_dspy_forward validation — Claude + 300 concurrent")
    print("Concurrency: DSPy .acall() → litellm.acompletion (zero threads)")
    print("=" * 60)

    try:
        setup_dspy()
    except Exception as e:
        print(f"\nSETUP FAILED: {e}")
        traceback.print_exc()
        sys.exit(1)

    try:
        await test_single_call()
        await test_combiner()
        await test_template_extractor()
        await test_concurrency(n=300)
        print("\n" + "=" * 60)
        print("ALL TESTS PASSED — safe to deploy")
        print("=" * 60)
    except AssertionError as e:
        print(f"\nFAIL: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\nERROR: {e}")
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
