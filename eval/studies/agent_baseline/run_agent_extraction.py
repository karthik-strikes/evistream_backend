"""
Claude Code agentic-extraction baseline — periodontitis (Cochrane CD004714).

For each (paper, form) we spin up an ISOLATED temp dir containing only that one
paper's markdown and launch a real Claude Code agent (`claude -p`) to extract the
data agentically — the agent reads/greps/reasons over as many turns as it needs and
writes `extraction.json`. This script is PURE ORCHESTRATION: it does no extraction
logic itself, only launches agents, captures cost/time, and assembles the CSVs.

Outputs (all isolated; the existing system results in
eval/sheets/ai sheets/full_studies/periodontitis/claude/ are never touched):
    eval/studies/agent_baseline/outputs/sheets/*.csv     — harness-format result sheets
    eval/studies/agent_baseline/outputs/cost_report.csv  — per (paper,form) cost/time/turns
    eval/studies/agent_baseline/outputs/summary.json     — per-form + grand totals
    eval/studies/agent_baseline/outputs/raw/{form}/{paper}.json — raw claude envelopes

Usage:
    python eval/studies/agent_baseline/run_agent_extraction.py --dry-run
    python eval/studies/agent_baseline/run_agent_extraction.py --papers "Artese 2015" --forms study_char
    python eval/studies/agent_baseline/run_agent_extraction.py            # full 29 x 4 run
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from forms_spec import FORMS, FormSpec, build_prompt, load_spec_json  # noqa: E402

_REPO = Path("/home/ubuntu/evistream")
MARKDOWN_DIR = _REPO / "eval" / "sheets" / "markdown_perio"
OUT_DIR = Path(__file__).resolve().parent / "outputs"
SHEETS_DIR = OUT_DIR / "sheets"
RAW_DIR = OUT_DIR / "raw"

MODEL = "claude-sonnet-4-6"
TIMEOUT_S = 300
CONCURRENCY = 4
RETRIES = 1
CLAUDE_BIN = shutil.which("claude") or "/usr/bin/claude"


# ── env ──────────────────────────────────────────────────────────────────────
def _load_env() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    for p in (_REPO / "eval" / ".env", _REPO / "backend" / ".env"):
        if p.exists():
            load_dotenv(p, override=False)


# ── one agent session ──────────────────────────────────────────────────────────
def _parse_extraction(raw_text: str):
    """Pull the first JSON object out of a string (tolerates stray prose / fences)."""
    start = raw_text.find("{")
    if start == -1:
        raise ValueError("no JSON object found")
    obj, _ = json.JSONDecoder().raw_decode(raw_text[start:])
    return obj


def run_session(spec: FormSpec, paper_path: Path, prompt: str) -> dict:
    """Launch one Claude Code agent in an isolated temp dir. Returns a record with
    the parsed extraction (or error) plus cost/time/turn metrics from the envelope."""
    paper = paper_path.stem
    rec = {
        "form": spec.key, "paper": paper, "status": "failed", "retries": 0,
        "duration_ms": None, "duration_api_ms": None, "num_turns": None,
        "total_cost_usd": None, "input_tokens": None, "output_tokens": None,
        "cache_read_tokens": None, "cache_creation_tokens": None,
        "extraction": None, "error": None,
    }

    for attempt in range(RETRIES + 1):
        rec["retries"] = attempt
        workdir = Path(tempfile.mkdtemp(prefix=f"agentx_{spec.key}_"))
        try:
            shutil.copyfile(paper_path, workdir / "paper.md")
            t0 = time.time()
            proc = subprocess.run(
                [CLAUDE_BIN, "-p", prompt,
                 "--output-format", "json",
                 "--model", MODEL,
                 "--allowedTools", "Read", "Grep", "Glob", "Write",
                 "--permission-mode", "bypassPermissions"],
                cwd=str(workdir),
                capture_output=True, text=True, timeout=TIMEOUT_S,
                env=os.environ.copy(),
            )
            wall_ms = int((time.time() - t0) * 1000)

            if proc.returncode != 0:
                rec["error"] = f"exit {proc.returncode}: {proc.stderr[-300:]}"
                continue

            env = json.loads(proc.stdout)
            usage = env.get("usage") or {}
            rec.update(
                duration_ms=env.get("duration_ms", wall_ms),
                duration_api_ms=env.get("duration_api_ms"),
                num_turns=env.get("num_turns"),
                total_cost_usd=env.get("total_cost_usd"),
                input_tokens=usage.get("input_tokens"),
                output_tokens=usage.get("output_tokens"),
                cache_read_tokens=usage.get("cache_read_input_tokens"),
                cache_creation_tokens=usage.get("cache_creation_input_tokens"),
            )
            # archive raw envelope
            raw_form_dir = RAW_DIR / spec.key
            raw_form_dir.mkdir(parents=True, exist_ok=True)
            (raw_form_dir / f"{paper}.json").write_text(json.dumps(env, indent=2))

            # prefer the file the agent wrote; fall back to the printed result
            ext_file = workdir / "extraction.json"
            try:
                if ext_file.exists():
                    rec["extraction"] = json.loads(ext_file.read_text())
                else:
                    rec["extraction"] = _parse_extraction(env.get("result", ""))
                rec["status"] = "ok"
                return rec
            except Exception as pe:
                rec["error"] = f"unparseable extraction: {pe}"
                continue
        except subprocess.TimeoutExpired:
            rec["error"] = f"timeout after {TIMEOUT_S}s"
        except Exception as e:
            rec["error"] = f"{type(e).__name__}: {e}"
        finally:
            shutil.rmtree(workdir, ignore_errors=True)

    return rec


# ── CSV assembly ────────────────────────────────────────────────────────────────
def _cell(v) -> str:
    if v is None:
        return "NR"
    s = str(v).strip()
    return s if s else "NR"


def rows_for(spec: FormSpec, rec: dict) -> list[dict]:
    """Turn one session's extraction into CSV row dict(s), Paper set by the driver."""
    paper = rec["paper"]
    field_cols = [c for c in spec.columns if c != "Paper"]
    ext = rec.get("extraction")

    if not spec.level2:
        ext = ext or {}
        row = {"Paper": paper}
        for c in field_cols:
            row[c] = _cell(ext.get(c))
        return [row]

    # level-2: pull the array; one CSV row per element
    arr = []
    if isinstance(ext, dict):
        arr = ext.get(spec.array_key) or []
        if not arr:  # tolerate a bare list or alternate key
            for v in ext.values():
                if isinstance(v, list):
                    arr = v
                    break
    elif isinstance(ext, list):
        arr = ext
    rows = []
    for item in arr:
        if not isinstance(item, dict):
            continue
        row = {"Paper": paper}
        for c in field_cols:
            row[c] = _cell(item.get(c))
        rows.append(row)
    return rows


def write_sheet(spec: FormSpec, rows: list[dict]) -> Path:
    SHEETS_DIR.mkdir(parents=True, exist_ok=True)
    path = SHEETS_DIR / spec.out_filename
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(spec.columns), quoting=csv.QUOTE_ALL)
        w.writeheader()
        for r in rows:
            w.writerow(r)
    return path


# ── orchestration ────────────────────────────────────────────────────────────────
def discover_papers(filter_names: list[str] | None) -> list[Path]:
    papers = sorted(MARKDOWN_DIR.glob("*.md"))
    if filter_names:
        wanted = {n.strip().lower() for n in filter_names}
        papers = [p for p in papers if p.stem.lower() in wanted]
    return papers


def main() -> int:
    global TIMEOUT_S
    ap = argparse.ArgumentParser()
    ap.add_argument("--forms", nargs="*", choices=sorted(FORMS), default=sorted(FORMS))
    ap.add_argument("--papers", nargs="*", help="exact study stems, e.g. \"Artese 2015\"")
    ap.add_argument("--limit", type=int, help="cap number of papers (after filtering)")
    ap.add_argument("--concurrency", type=int, default=CONCURRENCY)
    ap.add_argument("--timeout", type=int, default=TIMEOUT_S,
                    help="per-session wall-clock timeout in seconds (default 300)")
    ap.add_argument("--append", action="store_true",
                    help="merge results into existing sheets/cost_report instead of "
                         "overwriting: rows for papers in THIS run replace their old rows; "
                         "all other papers are preserved. summary.json is recomputed over "
                         "the merged cost_report.")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    TIMEOUT_S = args.timeout

    _load_env()
    if not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit("ERROR: ANTHROPIC_API_KEY not set (eval/.env).")

    papers = discover_papers(args.papers)
    if args.limit:
        papers = papers[: args.limit]
    specs = [FORMS[k] for k in args.forms]

    print(f"Model:       {MODEL}")
    print(f"Papers:      {len(papers)}")
    print(f"Forms:       {[s.key for s in specs]}")
    print(f"Sessions:    {len(papers) * len(specs)}  (concurrency {args.concurrency})")
    if args.dry_run:
        for s in specs:
            print(f"\n  [{s.key}] -> {s.out_filename}  cols={s.columns}")
            print(f"  prompt preview:\n{build_prompt(s)[:600]}\n  ...")
        for p in papers[:10]:
            print(f"    paper: {p.stem}")
        return 0

    prompts = {s.key: build_prompt(s) for s in specs}
    cost_rows: list[dict] = []
    sheet_rows: dict[str, list[dict]] = {s.key: [] for s in specs}

    jobs = [(s, p) for s in specs for p in papers]
    t_start = time.time()
    done = 0
    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        futs = {pool.submit(run_session, s, p, prompts[s.key]): (s, p) for s, p in jobs}
        for fut in as_completed(futs):
            s, p = futs[fut]
            rec = fut.result()
            done += 1
            cost_rows.append({k: rec[k] for k in (
                "form", "paper", "status", "retries", "duration_ms", "duration_api_ms",
                "num_turns", "total_cost_usd", "input_tokens", "output_tokens",
                "cache_read_tokens", "cache_creation_tokens", "error")})
            sheet_rows[s.key].extend(rows_for(s, rec))
            cost = rec.get("total_cost_usd")
            print(f"  [{done}/{len(jobs)}] {s.key:14} {p.stem:22} "
                  f"{rec['status']:6} ${cost if cost is not None else 0:.4f} "
                  f"{rec.get('num_turns') or '-'}t {(rec.get('duration_ms') or 0)//1000}s"
                  + (f"  ! {rec['error']}" if rec["status"] != "ok" else ""))
    wall_s = time.time() - t_start

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    cost_fields = ["form", "paper", "status", "retries", "duration_ms", "duration_api_ms",
                   "num_turns", "total_cost_usd", "input_tokens", "output_tokens",
                   "cache_read_tokens", "cache_creation_tokens", "error"]
    papers_in_run = {p.stem for p in papers}

    # write sheets (stable order: by Paper, preserving level-2 row order within paper).
    # In --append mode, keep existing rows for papers NOT in this run; replace the rest.
    for s in specs:
        new_rows = sheet_rows[s.key]
        if args.append:
            existing = SHEETS_DIR / s.out_filename
            preserved = []
            if existing.exists():
                with open(existing, newline="") as f:
                    preserved = [r for r in csv.DictReader(f)
                                 if r.get("Paper") not in papers_in_run]
            merged = preserved + new_rows
        else:
            merged = list(new_rows)
        rows = sorted(merged, key=lambda r: r["Paper"])
        path = write_sheet(s, rows)
        print(f"  wrote {path}  ({len(rows)} rows{', merged' if args.append else ''})")

    # cost report. In --append mode, merge: drop old rows for (form,paper) pairs we just
    # re-ran, then add the fresh ones; keep every other historical row.
    cost_path = OUT_DIR / "cost_report.csv"
    if args.append and cost_path.exists():
        rerun_keys = {(r["form"], r["paper"]) for r in cost_rows}
        with open(cost_path, newline="") as f:
            kept = [r for r in csv.DictReader(f)
                    if (r.get("form"), r.get("paper")) not in rerun_keys]
        all_cost_rows = kept + cost_rows
    else:
        all_cost_rows = cost_rows
    with open(cost_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cost_fields)
        w.writeheader()
        w.writerows(all_cost_rows)

    # summary — always recomputed over the full (merged) cost report, so it stays whole.
    def _num(v):
        try:
            return float(v) if v not in (None, "", "None") else 0.0
        except (TypeError, ValueError):
            return 0.0

    def _sum(rows, key):
        return round(sum(_num(r.get(key)) for r in rows), 4)

    all_forms = sorted({r["form"] for r in all_cost_rows})
    per_form = {}
    for fk in all_forms:
        fr = [r for r in all_cost_rows if r["form"] == fk]
        oks = [r for r in fr if r["status"] == "ok"]
        per_form[fk] = {
            "sessions": len(fr),
            "ok": len(oks),
            "failed": len(fr) - len(oks),
            "cost_usd": _sum(fr, "total_cost_usd"),
            "duration_ms_total": _sum(fr, "duration_ms"),
            "turns_total": _sum(fr, "num_turns"),
        }
    summary = {
        "model": MODEL,
        "papers": len({r["paper"] for r in all_cost_rows}),
        "forms": all_forms,
        "sessions_total": len(all_cost_rows),
        "failures": sum(1 for r in all_cost_rows if r["status"] != "ok"),
        "grand_cost_usd": _sum(all_cost_rows, "total_cost_usd"),
        "wall_clock_s_last_run": round(wall_s, 1),
        "mean_cost_per_session_usd": round(_sum(all_cost_rows, "total_cost_usd") / max(len(all_cost_rows), 1), 4),
        "per_form": per_form,
    }
    (OUT_DIR / "summary.json").write_text(json.dumps(summary, indent=2))

    print("\n=== SUMMARY ===")
    print(json.dumps(summary, indent=2))
    print(f"\ncost report: {cost_path}")
    print(f"sheets:      {SHEETS_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
