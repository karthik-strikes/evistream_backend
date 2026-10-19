#!/usr/bin/env python3
"""Make a plain-English error-analysis workbook for one (dataset, model).

Reads the scored comparison_sheets, finds why each field fails (heuristics + an
optional LLM pass that reads the papers), and writes a 3-tab workbook:
  Summary · Fields, reasons & fixes · Evidence.

Examples
--------
  python analyze_errors.py --dataset ibuprofen --model claude
  python analyze_errors.py --dataset ibuprofen --model claude --no-llm
  python analyze_errors.py --dataset periodontitis --model claude
"""
import os, sys, argparse

# make the local package importable when run from anywhere
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from error_analysis import registry, read_sheets, llm_diagnose, workbook


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dataset", required=True, choices=registry.list_datasets())
    ap.add_argument("--model", required=True, help="claude / gpt / gemini / qwen")
    ap.add_argument("--no-llm", action="store_true",
                    help="skip the LLM step; use heuristic reasons only (free, weaker)")
    ap.add_argument("--out", default=None, help="output xlsx path (default: outputs/error_analysis/<dataset>_<model>.xlsx)")
    args = ap.parse_args()

    cmp_dir = registry.comparison_dir(args.dataset, args.model)
    if not os.path.isdir(cmp_dir):
        models = registry.list_models(args.dataset)
        sys.exit(f"No comparison sheets for {args.dataset}/{args.model}.\n"
                 f"  looked in: {cmp_dir}\n"
                 f"  models available for {args.dataset}: {models or '(none)'}")

    print(f"[1/3] reading scored sheets  ({args.dataset} / {args.model}) ...")
    analysis = read_sheets.analyze(args.dataset, args.model)
    t = analysis["totals"]
    print(f"      {len(analysis['fields'])} fields across {len(analysis['forms'])} forms; "
          f"{t['mismatches']} disagreements  "
          f"(BAD={t['n_bad']}, SO-SO={t['n_soso']}, fine={t['n_fine']})")

    use_llm = not args.no_llm
    if use_llm:
        print(f"[2/3] diagnosing BAD/SO-SO fields with the LLM (model={llm_diagnose._model()}) ...")
    else:
        print("[2/3] diagnosing with heuristics only (--no-llm) ...")

    def prog(i, n, field):
        print(f"      ({i}/{n}) {field}", flush=True)

    # check the key exists before we start, if using the LLM
    if use_llm:
        llm_diagnose._load_env()
        if not os.getenv("ANTHROPIC_API_KEY"):
            print("      WARNING: ANTHROPIC_API_KEY not found in eval/.env — falling back to --no-llm")
            use_llm = False

    diagnoses = llm_diagnose.diagnose_all(analysis, use_llm=use_llm, on_progress=prog if use_llm else None)

    out = args.out or os.path.join(registry.REPO, "eval", "outputs", "error_analysis",
                                   f"{args.dataset}_{args.model}.xlsx")
    print("[3/3] writing workbook ...")
    path = workbook.build(analysis, diagnoses, out)
    print(f"\nDONE  ->  {path}")
    # tiny worst-fields recap
    print("\nWorst fields:")
    for f in analysis["fields"][:6]:
        d = diagnoses.get((f["form"], f["field"]), {})
        print(f"  {round(f['err_rate']*100):3d}%  {f['field']:22s}  {d.get('cause_id','')}: "
              f"{d.get('why','')[:80]}")


if __name__ == "__main__":
    main()
