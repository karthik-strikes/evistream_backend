#!/usr/bin/env python3
"""Verify the copies of RoB 2's questions, routing and algorithm still agree.

    python backend/zscripts/check_rob2_mirror.py

``frontend/app/(dashboard)/risk-of-bias/_lib/rob2.ts`` is the authority — what a
reviewer reads and answers. Two server-side files mirror it:

  * ``backend/utils/rob2_questions.py`` — the question text the drafting prompt
    shows the model. If this drifts, the model answers a question the screen
    never shows, and because both sides key on the question *id* the answer
    still lands in a box under different wording. Nothing would look broken.
  * ``backend/utils/rob2_engine.py`` — the executable routing and the judgement
    algorithm, used by consensus re-derivation, draft validation and exports.
    If this drifts, two surfaces disagree about the same assessment's risk
    label and neither is visibly wrong.

This checks three things, in increasing order of how quietly they fail:

  1. all 22 question texts match, word for word;
  2. the routing predicates agree — which questions each side asks, over every
     answer combination that can reach them;
  3. the five domain algorithms and the overall rule agree, over an exhaustive
     sweep of answer sets.

Checks 2 and 3 compare *behaviour*, not source text, because the two files are
written in different languages and a faithful port can look nothing alike. The
TypeScript side is evaluated by extracting its logic and re-implementing the
comparison against enumerated inputs — see ``ts_routing_table``.

Same arrangement, and the same reason, as
``backend/utils/study_label.py`` <-> ``frontend/lib/documentLabel.ts``.

Read-only. Exit code 1 on any mismatch, so this can gate a commit.
"""

import hashlib
import itertools
import json
import re
import subprocess
import sys
import tempfile
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]  # backend/zscripts/ -> project root
TS = ROOT / "frontend/app/(dashboard)/risk-of-bias/_lib/rob2.ts"
PY = ROOT / "backend/utils/rob2_questions.py"
ENGINE = ROOT / "backend/utils/rob2_engine.py"

#: The five real responses plus "not answered yet". `None` is in the sweep on
#: purpose: `judgeDomain` returning null rather than guessing on an incomplete
#: domain is a rule in its own right, and it is the one a careless port loses.
CODES = ["Y", "PY", "PN", "N", "NI", None]

# `text: 'a ' + 'b'` — TypeScript wraps long strings by concatenation.
_ENTRY = re.compile(
    r"id:\s*'(?P<id>\d+\.\d+)'.*?text:\s*(?P<text>(?:'(?:[^'\\]|\\.)*'\s*\+\s*)*'(?:[^'\\]|\\.)*')",
    re.S,
)
_PIECE = re.compile(r"'((?:[^'\\]|\\.)*)'")


def normalise(text: str) -> str:
    """Compare meaning, not typography.

    Both files are hand-maintained prose, so a curly vs straight apostrophe or a
    wrapped line is not drift worth failing on — a changed *word* is.
    """
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("’", "'").replace("‘", "'")
    return re.sub(r"\s+", " ", text).strip().lower()


def from_typescript() -> dict:
    source = TS.read_text(encoding="utf-8")
    start = source.index("export const ROB2_SIGNALLING")
    body = source[start:source.index("\n];", start)]
    out = {}
    for match in _ENTRY.finditer(body):
        pieces = _PIECE.findall(match.group("text"))
        out[match.group("id")] = "".join(p.replace("\\'", "'") for p in pieces)
    return out


def from_python() -> dict:
    sys.path.insert(0, str(ROOT / "backend"))
    from utils.rob2_questions import QUESTIONS  # noqa: E402
    return {q.id: q.text for q in QUESTIONS}


# ── Behaviour comparison ────────────────────────────────────────────────────
# The two implementations are in different languages, so comparing source text
# proves nothing — a faithful port looks nothing alike, and a broken one can
# look identical. What is comparable is behaviour, so both sides are swept over
# the same exhaustive set of answer combinations and the results are hashed.
#
# Sizes: D1 6^3, D2 6^7, D3 6^4, D4 6^5, D5 6^3 — about 289k evaluations per
# side. Exhaustive, and fast enough to run on every commit.

_PROBE = r"""
import { createHash } from 'node:crypto';
import { ROB2_SIGNALLING, isAsked, judgeDomain, judgeOverall } from '%(rob2)s';

const CODES = ['Y', 'PY', 'PN', 'N', 'NI', null];
const DOMAINS = ROB2_SIGNALLING.map(d => d.questions.map(q => q.id));
const BY_ID = new Map();
ROB2_SIGNALLING.forEach(d => d.questions.forEach(q => BY_ID.set(q.id, q)));

/** Every assignment of CODES to `ids`, as a plain object. */
function* combos(ids) {
  if (ids.length === 0) { yield {}; return; }
  const [head, ...rest] = ids;
  for (const code of CODES) {
    for (const tail of combos(rest)) {
      const row = { ...tail };
      if (code !== null) row[head] = code;
      yield row;
    }
  }
}

/** One line per combination: the routing flags, then the judgement. */
function* rows(di) {
  const ids = DOMAINS[di];
  for (const combo of combos(ids)) {
    let asked = '';
    for (const id of ids) asked += isAsked(BY_ID.get(id), combo) ? '1' : '0';
    const key = ids.map(id => combo[id] ?? '_').join(',');
    yield key + ' ' + asked + ' ' + (judgeDomain(di, combo) ?? '-');
  }
}

const dump = process.argv[2] === '--dump' ? Number(process.argv[3]) : null;

if (dump !== null) {
  for (const line of rows(dump)) process.stdout.write(line + '\n');
} else {
  const out = { domains: [], overall: null, ids: DOMAINS };
  for (let di = 0; di < DOMAINS.length; di++) {
    const h = createHash('sha256');
    for (const line of rows(di)) { h.update(line); h.update('\n'); }
    out.domains.push(h.digest('hex'));
  }
  const sev = [null, 'low', 'some', 'high'];
  const h = createHash('sha256');
  for (const a of sev) for (const b of sev) for (const c of sev)
    for (const d of sev) for (const e of sev) {
      const r = judgeOverall([a, b, c, d, e]);
      h.update(`${a},${b},${c},${d},${e} ${r.severity ?? '-'} ${r.considerHigh ? 1 : 0}\n`);
    }
  out.overall = h.digest('hex');
  process.stdout.write(JSON.stringify(out));
}
"""


def _run_probe(args: list) -> subprocess.CompletedProcess:
    """Execute rob2.ts itself. The authority is run, never re-read."""
    with tempfile.TemporaryDirectory() as tmp:
        probe = Path(tmp) / "probe.mts"
        probe.write_text(_PROBE % {"rob2": TS.as_posix()}, encoding="utf-8")
        return subprocess.run(
            ["node", "--experimental-strip-types", "--no-warnings", str(probe), *args],
            capture_output=True, text=True, cwd=ROOT,
        )


def _py_rows(domain_index: int, ids: list):
    """The Python side of the same sweep, in the same line format."""
    from utils.rob2_engine import is_asked, judge_domain  # noqa: E402
    for combo in itertools.product(CODES, repeat=len(ids)):
        answers = {qid: code for qid, code in zip(ids, combo) if code is not None}
        asked = "".join("1" if is_asked(qid, answers) else "0" for qid in ids)
        key = ",".join(code if code is not None else "_" for code in combo)
        yield f"{key} {asked} {judge_domain(domain_index, answers) or '-'}"


def _py_digest(domain_index: int, ids: list) -> str:
    h = hashlib.sha256()
    for line in _py_rows(domain_index, ids):
        h.update(line.encode()); h.update(b"\n")
    return h.hexdigest()


def _py_overall_digest() -> str:
    from utils.rob2_engine import judge_overall  # noqa: E402
    sev = [None, "low", "some", "high"]
    h = hashlib.sha256()
    for combo in itertools.product(sev, repeat=5):
        r = judge_overall(list(combo))
        joined = ",".join(s if s is not None else "" for s in combo)
        # JS prints `null` for a null entry; match that rendering exactly.
        joined = ",".join("null" if s is None else s for s in combo)
        h.update(f"{joined} {r['severity'] or '-'} {1 if r['consider_high'] else 0}\n".encode())
    return h.hexdigest()


def _first_disagreements(domain_index: int, ids: list, limit: int = 5) -> list:
    """Re-run the offending domain and name the inputs that actually differ."""
    proc = _run_probe(["--dump", str(domain_index)])
    if proc.returncode != 0:
        return [f"could not dump domain {domain_index}: {proc.stderr.strip()[:200]}"]
    ts_lines = proc.stdout.splitlines()
    out = []
    for ts_line, py_line in zip(ts_lines, _py_rows(domain_index, ids)):
        if ts_line != py_line:
            out.append(f"       ts: {ts_line}\n       py: {py_line}")
            if len(out) >= limit:
                break
    if len(ts_lines) != 6 ** len(ids):
        out.append(f"       ts produced {len(ts_lines)} rows, expected {6 ** len(ids)}")
    return out


MODEL_TS = ROOT / "frontend/app/(dashboard)/risk-of-bias/_lib/robModel.ts"


def check_trial_split(problems: list) -> None:
    """Which questions are answered once per study, on both sides.

    A drift here is the quietest of the lot: if one side thinks a question is
    study-level and the other thinks it is per assessment, the question is
    asked twice in one place and never in the other, and every screen still
    renders. Schema 3: only Domain 1 is study-level (`robModel.ts:D1_QUESTIONS`
    <-> `rob2_engine.STUDY_LEVEL_QUESTIONS`).
    """
    from utils.rob2_engine import STUDY_LEVEL_QUESTIONS  # noqa: E402

    if not MODEL_TS.exists():
        problems.append("frontend robModel.ts is missing — the study-level split is unpinned")
        return

    source = MODEL_TS.read_text(encoding="utf-8")
    match = re.search(r"D1_QUESTIONS[^=]*=\s*\[(?P<body>[^\]]*)\]", source)
    if not match:
        problems.append("could not find D1_QUESTIONS in robModel.ts")
        return

    ts_ids = re.findall(r"'(\d+\.\d+)'", match.group("body"))
    if ts_ids != list(STUDY_LEVEL_QUESTIONS):
        problems.append(
            "study-level questions differ\n"
            f"       ts: {ts_ids}\n"
            f"       py: {list(STUDY_LEVEL_QUESTIONS)}"
        )


def check_behaviour(problems: list) -> None:
    """Routing and algorithm, compared by exhaustive sweep."""
    if not ENGINE.exists():
        problems.append("backend/utils/rob2_engine.py is missing")
        return

    proc = _run_probe([])
    if proc.returncode != 0:
        problems.append(
            "could not execute rob2.ts — behaviour is unchecked.\n"
            f"       {proc.stderr.strip()[:400]}"
        )
        return
    try:
        ts = json.loads(proc.stdout)
    except json.JSONDecodeError:
        problems.append(f"probe returned non-JSON: {proc.stdout[:200]}")
        return

    from utils.rob2_engine import DOMAIN_QUESTIONS  # noqa: E402

    for di, ids in enumerate(ts["ids"]):
        mine = DOMAIN_QUESTIONS.get(di, [])
        if mine != ids:
            problems.append(
                f"D{di + 1}: question set differs — ts {ids}, py {mine}"
            )
            continue
        if _py_digest(di, ids) != ts["domains"][di]:
            detail = "\n".join(_first_disagreements(di, ids))
            problems.append(
                f"D{di + 1}: routing or judgement differs over the answer sweep\n{detail}"
            )

    if _py_overall_digest() != ts["overall"]:
        problems.append("judgeOverall differs from judge_overall over all 1024 combinations")


# ── Effect of adhering (Box 7 · Table 8) ────────────────────────────────────
# Only D2 changes under the adhering effect. Swept for every non-empty subset
# of pre-specified deviation types (7 subsets x 6^6 answer sets = ~327k per
# side), comparing the routing state of each question and the judgement.

ADHERING_IDS = ["2.1", "2.2", "2.3", "2.4", "2.5", "2.6"]
DEVIATION_SUBSETS = [
    list(c) for n in (1, 2, 3)
    for c in itertools.combinations(["nonprotocol", "implementation", "nonadherence"], n)
]

_ADHERING_PROBE = r"""
import { createHash } from 'node:crypto';
import { routeAllFor, judgeDomainFor } from '%(rob2)s';

const CODES = ['Y', 'PY', 'PN', 'N', 'NI', null];
const IDS = %(ids)s;
const SUBSETS = %(subsets)s;

function* combos(ids) {
  if (ids.length === 0) { yield {}; return; }
  const [head, ...rest] = ids;
  for (const code of CODES) {
    for (const tail of combos(rest)) {
      const row = { ...tail };
      if (code !== null) row[head] = code;
      yield row;
    }
  }
}

function* rows(si) {
  const opts = { effect: 'adherence', deviations: SUBSETS[si] };
  for (const combo of combos(IDS)) {
    const route = routeAllFor(combo, opts);
    const states = IDS.map(id => route[id][0]).join('');
    const key = IDS.map(id => combo[id] ?? '_').join(',');
    yield key + ' ' + states + ' ' + (judgeDomainFor(1, combo, opts) ?? '-');
  }
}

const dump = process.argv[2] === '--dump' ? Number(process.argv[3]) : null;
if (dump !== null) {
  for (const line of rows(dump)) process.stdout.write(line + '\n');
} else {
  const out = [];
  for (let si = 0; si < SUBSETS.length; si++) {
    const h = createHash('sha256');
    for (const line of rows(si)) { h.update(line); h.update('\n'); }
    out.push(h.digest('hex'));
  }
  process.stdout.write(JSON.stringify(out));
}
"""


def _run_adhering_probe(args: list) -> subprocess.CompletedProcess:
    with tempfile.TemporaryDirectory() as tmp:
        probe = Path(tmp) / "adhering.mts"
        probe.write_text(_ADHERING_PROBE % {
            "rob2": TS.as_posix(), "ids": json.dumps(ADHERING_IDS),
            "subsets": json.dumps(DEVIATION_SUBSETS),
        }, encoding="utf-8")
        return subprocess.run(
            ["node", "--experimental-strip-types", "--no-warnings", str(probe), *args],
            capture_output=True, text=True, cwd=ROOT,
        )


def _py_adhering_rows(si: int):
    from utils.rob2_engine import judge_domain_for, route_all_for  # noqa: E402
    subset = DEVIATION_SUBSETS[si]
    for combo in itertools.product(CODES, repeat=len(ADHERING_IDS)):
        answers = {q: c for q, c in zip(ADHERING_IDS, combo) if c is not None}
        route = route_all_for(answers, "adherence", subset)
        states = "".join(route[q][0] for q in ADHERING_IDS)
        key = ",".join(c if c is not None else "_" for c in combo)
        yield f"{key} {states} {judge_domain_for(1, answers, 'adherence', subset) or '-'}"


def check_adhering(problems: list) -> None:
    proc = _run_adhering_probe([])
    if proc.returncode != 0:
        problems.append("could not execute the adhering pathway in rob2.ts\n"
                        f"       {proc.stderr.strip()[:400]}")
        return
    ts = json.loads(proc.stdout)
    for si, digest in enumerate(ts):
        h = hashlib.sha256()
        for line in _py_adhering_rows(si):
            h.update(line.encode()); h.update(b"\n")
        if h.hexdigest() == digest:
            continue
        dump = _run_adhering_probe(["--dump", str(si)]).stdout.splitlines()
        diffs = [f"       ts: {a}\n       py: {b}"
                 for a, b in zip(dump, _py_adhering_rows(si)) if a != b][:5]
        problems.append(f"D2 adhering, deviations={DEVIATION_SUBSETS[si]}: differs\n"
                        + "\n".join(diffs))


def check_adhering_text(problems: list) -> None:
    """The adhering D2 questions (Box 7) the drafting prompt shows the model.

    They reuse ids 2.1–2.6 with different wording for 2.3–2.6, so a drift here
    is invisible in storage: the answer lands in the same column either way.
    """
    source = TS.read_text(encoding="utf-8")
    start = source.index("export const ROB2_D2_ADHERING")
    body = source[start:source.index("\n};", start)]
    ts = {}
    for match in _ENTRY.finditer(body):
        pieces = _PIECE.findall(match.group("text"))
        ts[match.group("id")] = "".join(p.replace("\\'", "'") for p in pieces)
    sys.path.insert(0, str(ROOT / "backend"))
    from utils.rob2_questions import ADHERING_D2  # noqa: E402
    py = {q.id: q.text for q in ADHERING_D2}
    if sorted(ts) != sorted(py):
        problems.append(f"adhering D2 ids differ: ts={sorted(ts)} py={sorted(py)}")
    for qid in sorted(set(ts) & set(py)):
        if normalise(ts[qid]) != normalise(py[qid]):
            problems.append(f"adhering {qid}: wording differs\n       ts: {ts[qid]}\n       py: {py[qid]}")


def main() -> int:
    ts, py = from_typescript(), from_python()
    problems = []

    if len(ts) != 22:
        problems.append(f"parsed {len(ts)} questions from rob2.ts, expected 22")
    if len(py) != 22:
        problems.append(f"found {len(py)} questions in rob2_questions.py, expected 22")

    for missing in sorted(set(ts) - set(py)):
        problems.append(f"{missing}: in rob2.ts but not in rob2_questions.py")
    for missing in sorted(set(py) - set(ts)):
        problems.append(f"{missing}: in rob2_questions.py but not in rob2.ts")

    for qid in sorted(set(ts) & set(py)):
        if normalise(ts[qid]) != normalise(py[qid]):
            problems.append(
                f"{qid}: wording differs\n"
                f"       ts: {ts[qid]}\n"
                f"       py: {py[qid]}"
            )

    check_trial_split(problems)
    check_behaviour(problems)
    check_adhering(problems)
    check_adhering_text(problems)

    if problems:
        print(f"\n  RoB 2 mirror check FAILED — {len(problems)} problem(s):\n")
        for p in problems:
            print(f"   x {p}")
        print()
        return 1

    print(
        f"\n  RoB 2 mirror check — {len(ts)} questions agree, the study-level split agrees,\n"
        f"  and routing plus all five domain algorithms agree over an exhaustive answer\n"
        f"  sweep (~289k combinations); the effect-of-adhering D2 pathway agrees for\n"
        f"  all 7 deviation-type subsets (~327k combinations), and its six\n"
        f"  question texts agree.\n"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
