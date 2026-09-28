#!/usr/bin/env python3
"""Verify the two dataset-hash implementations agree.

    python backend/zscripts/check_synthesis_hash_mirror.py

``frontend/app/(dashboard)/synthesis/_lib/datasetHash.ts`` hashes a run's
analysis-ready dataset in the browser; ``backend/utils/synthesis_hash.py``
recomputes it when the run is posted and refuses a mismatch. If the two drift,
every run posted from the browser is rejected (or, worse, two different
datasets hash alike). Same arrangement as ``check_rob2_mirror.py``: the
TypeScript is *executed* under ``node --experimental-strip-types`` on the same
fixtures, never re-read.

Checks canonical JSON, the pure sha256 (also against node:crypto), and
shortHash, on hand-picked edge cases plus a seeded random sweep of numbers.

Read-only. Exit code 1 on any mismatch.
"""

import json
import math
import random
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]  # backend/zscripts/ -> project root
TS = ROOT / "frontend/app/(dashboard)/synthesis/_lib/datasetHash.ts"
sys.path.insert(0, str(ROOT / "backend"))

from utils.synthesis_hash import canonical_json, dataset_hash, sha256_hex, short_hash  # noqa: E402

NONFINITE = "__nonfinite__"


def fixtures():
    fx = [
        [],
        [None, True, False, 0, -0.0, 1, -1],
        [0.1 + 0.2, 0.3, 1e-12, -1e-12, 1.5e-7, 1e-6, 123456.789, 1 / 3, 2 / 3, math.pi, -math.e],
        [1234567890.5, -1234567890.5, 2.5, 0.5, 12345678905, 99999999995.0, 9.9999999995],
        [2 ** 53 - 1, 2 ** 53, 2 ** 53 + 1, 2 ** 60, 10 ** 20, 10 ** 21, 1e21, 1.5e21, 1e300, 5e-324, 1.7976931348623157e308],
        [float("nan"), float("inf"), float("-inf")],
        [{"b": 1, "a": 2, "A": 3, "é": 4, "z": {"y": [1, {"x": None}], "a": []}}],
        [{"ref": "study:1", "label": "Müller 2019 — ½ dose “quoted” \\ / \n\t\r\b\f \u0001 \u007f   😀",
          "effect": {"est": -0.2231435513142097, "se": 0.1414213562373095}}],
        [{"\U0001F600": 1, "￿": 2, "": 3, "": 4}],
        [[[[]]], [[1, [2, [3.25]]]], {"": {"": {"": "deep"}}}],
        [{"ref": "study:7c1f", "document_id": "7c1f", "label": "Raslan 2021", "kind": "dichotomous",
          "measure": "RR", "arms": {"treatment": {"events": 12, "n": 40}, "comparator": {"events": 20, "n": 41}},
          "n_randomised": 81, "transformations": [], "derived": False}],
        [{"sd": 12.300000000000001, "mean": -4.0000000001, "n": 60.0, "icc": 0.05, "m": 1e3}],
    ]
    rng = random.Random(20260925)
    sweep = []
    for _ in range(3000):
        kind = rng.random()
        if kind < 0.3:
            sweep.append(rng.uniform(-1, 1) * 10 ** rng.randint(-30, 30))
        elif kind < 0.5:
            sweep.append(rng.randint(-10 ** 18, 10 ** 18))
        elif kind < 0.7:
            sweep.append(round(rng.uniform(-1000, 1000), rng.randint(0, 12)))
        elif kind < 0.85:
            sweep.append(rng.randint(1, 10 ** 11) / 10 ** rng.randint(0, 11))
        else:
            sweep.append(rng.randint(-2 ** 40, 2 ** 40) + 0.5)
    fx.append(sweep)
    fx.append(["x" * n for n in (0, 1, 55, 56, 63, 64, 65, 119, 120, 1000)])
    return fx


def encode(value):
    """JSON transport that survives NaN/Infinity; revived on both sides."""
    if isinstance(value, float) and not math.isfinite(value):
        return {NONFINITE: "NaN" if math.isnan(value) else ("Infinity" if value > 0 else "-Infinity")}
    if isinstance(value, dict):
        return {k: encode(v) for k, v in value.items()}
    if isinstance(value, list):
        return [encode(v) for v in value]
    return value


PROBE = r"""
import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { canonicalJson, sha256Hex, datasetHash, shortHash } from '%(ts)s';

const revive = (v) => {
  if (Array.isArray(v)) return v.map(revive);
  if (v && typeof v === 'object') {
    if (Object.keys(v).length === 1 && '__nonfinite__' in v) return Number(v.__nonfinite__);
    const o = {};
    for (const k of Object.keys(v)) o[k] = revive(v[k]);
    return o;
  }
  return v;
};
const fixtures = JSON.parse(readFileSync(process.argv[2], 'utf8')).map(revive);
const out = fixtures.map(f => {
  const canon = canonicalJson(f);
  const hash = datasetHash(f);
  const ref = createHash('sha256').update(canon, 'utf8').digest('hex');
  return { canon, hash, short: shortHash(hash), sha_ok: hash === ref && sha256Hex(canon) === ref };
});
process.stdout.write(JSON.stringify(out));
"""


def run_ts(fx):
    with tempfile.TemporaryDirectory() as tmp:
        probe = Path(tmp) / "probe.mts"
        data = Path(tmp) / "fixtures.json"
        probe.write_text(PROBE % {"ts": TS.as_posix()}, encoding="utf-8")
        data.write_text(json.dumps([encode(f) for f in fx], ensure_ascii=False), encoding="utf-8")
        proc = subprocess.run(
            ["node", "--experimental-strip-types", "--no-warnings", str(probe), str(data)],
            capture_output=True, text=True, cwd=ROOT,
        )
    if proc.returncode != 0:
        print(proc.stderr)
        sys.exit(1)
    return json.loads(proc.stdout)


def main() -> int:
    fx = fixtures()
    ts = run_ts(fx)
    failures = 0
    for i, (f, t) in enumerate(zip(fx, ts)):
        canon = canonical_json(f)
        h = dataset_hash(f)
        if not t["sha_ok"]:
            failures += 1
            print(f"[{i}] TS sha256 disagrees with node:crypto")
        if sha256_hex(canon) != h:
            failures += 1
        if canon != t["canon"]:
            failures += 1
            # Point at the first differing element for a readable report.
            if isinstance(f, list):
                for j, item in enumerate(f):
                    pj = canonical_json(item)
                    if pj not in t["canon"]:
                        print(f"[{i}][{j}] python={pj!r}  (value {item!r}) not in TS canon")
                        break
            print(f"[{i}] canonical JSON differs\n  py={canon[:300]}\n  ts={t['canon'][:300]}")
        if h != t["hash"] or short_hash(h) != t["short"]:
            failures += 1
            print(f"[{i}] hash differs: py={h} ts={t['hash']}")
    # A sanity property worth pinning: float noise below 10 sig digits is not a new dataset.
    assert dataset_hash([0.1 + 0.2]) == dataset_hash([0.3])
    assert dataset_hash([1.0]) == dataset_hash([1])
    n_values = sum(len(f) if isinstance(f, list) else 1 for f in fx)
    if failures:
        print(f"FAIL: {failures} mismatch(es) across {len(fx)} fixtures")
        return 1
    print(f"OK: {len(fx)} fixtures ({n_values} top-level values) agree — canonical JSON, sha256, shortHash")
    return 0


if __name__ == "__main__":
    sys.exit(main())
