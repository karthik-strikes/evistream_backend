#!/usr/bin/env python3
"""Report — and optionally repair — reviewer-slot damage.

Three states the R1/R2 model can get into that no code path can resolve on its
own. Read-only by default; `--apply` performs only the repairs marked SAFE.

    python backend/scripts/audit_reviewer_slots.py              # report
    python backend/scripts/audit_reviewer_slots.py --apply      # do the safe repairs

1. **orphan** — a manual row sits under a role now assigned to somebody else.
   The current holder is refused on save (they cannot overwrite another
   person's work) and completion counting used to credit them with it.
   SAFE repair: when the author still holds a *free* reader seat on that
   document, re-tag the row to the seat they actually hold. Otherwise it needs a
   human: either move the assignment back to the author, or delete the row
   (`DELETE /api/v1/results/{id}`).

2. **same author in both seats** — one person authored both the reviewer_1 and
   the reviewer_2 row for a (document, form). This is the serious one: the
   adjudication screen presents it as two independent extractions, which is the
   single guarantee dual review exists to provide. NEVER repaired automatically
   — deciding which row is the real one, and who should redo the other, is a
   judgement about the review, not about the data.

3. **untagged** — a manual row with no reviewer_role. Invisible to conflict
   detection and IRR, and (before the fix) readable past the blind by anyone.
   SAFE repair: when the author holds exactly one reader seat on that document
   and that seat has no row for the form, tag it.

Nothing here deletes anything, ever.
"""

import argparse
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

# Secrets come from AWS Secrets Manager, not from backend/.env (which holds only
# the Bedrock keys) — and `app.config.Settings` validates at import, so this must
# run BEFORE that import or the script dies on six missing fields. Same order as
# app/main.py. Needs the service's env: `set -a; . /etc/evistream/bootstrap.env; set +a`.
from utils.secrets_loader import load_secrets  # noqa: E402

load_secrets()

from app.config import settings  # noqa: E402
from supabase import create_client  # noqa: E402
from utils import reviewer_slots  # noqa: E402

READERS = reviewer_slots.READER_ROLES


def _client():
    return create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_KEY)


def _page(build, size=1000):
    """PostgREST caps a response at max-rows; read to the end."""
    out, off = [], 0
    while True:
        rows = build().range(off, off + size - 1).execute().data or []
        out.extend(rows)
        if len(rows) < size:
            return out
        off += size


def load(sb):
    manual = _page(lambda: sb.table("extraction_results")
                   .select("id, project_id, document_id, form_id, reviewer_role, "
                           "extracted_by, extracted_data, created_at")
                   .eq("extraction_type", "manual"))
    assigns = _page(lambda: sb.table("review_assignments")
                    .select("project_id, document_id, reviewer_role, reviewer_user_id"))
    users = {u["id"]: (u.get("full_name") or u.get("email") or u["id"])
             for u in (sb.table("users").select("id, full_name, email").execute().data or [])}
    docs = {d["id"]: d.get("filename") for d in
            _page(lambda: sb.table("documents").select("id, filename"))}
    return manual, assigns, users, docs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true",
                    help="perform the repairs marked SAFE (default: report only)")
    args = ap.parse_args()

    sb = _client()
    manual, assigns, users, docs = load(sb)

    # (document, role) -> holder, and (document, user) -> roles they hold
    holder = {(a["document_id"], a["reviewer_role"]): a.get("reviewer_user_id")
              for a in assigns}
    holds = defaultdict(set)
    for a in assigns:
        if a.get("reviewer_user_id"):
            holds[(a["document_id"], str(a["reviewer_user_id"]))].add(a["reviewer_role"])

    # rows already occupying each (document, form, role)
    occupied = {(r["document_id"], r["form_id"], r.get("reviewer_role")) for r in manual}

    orphans, same_author, untagged = [], [], []

    by_doc_form = defaultdict(list)
    for r in manual:
        by_doc_form[(r["document_id"], r["form_id"])].append(r)

    for (doc_id, form_id), rows in sorted(by_doc_form.items()):
        ok, why = reviewer_slots.independent_pair(rows)
        if why == "same_author":
            same_author.append((doc_id, form_id, rows))
        for r in rows:
            role = r.get("reviewer_role")
            if role is None:
                untagged.append(r)
            elif reviewer_slots.is_orphan(r, {
                    rr: holder.get((doc_id, rr)) for rr in READERS}):
                orphans.append(r)

    def name(uid):
        return users.get(str(uid or ""), "(unknown)")

    def label(doc_id):
        return docs.get(doc_id) or doc_id

    print(f"\n{'='*78}\n  reviewer-slot audit — {len(manual)} manual rows\n{'='*78}")

    # ── 1. orphans ──────────────────────────────────────────────────────────
    print(f"\n[1] ORPHANED ROWS — role reassigned away from the author: {len(orphans)}")
    planned = []
    for r in orphans:
        doc_id, role = r["document_id"], r["reviewer_role"]
        author = str(r.get("extracted_by") or "")
        author_seats = holds[(doc_id, author)] & set(READERS)
        target = None
        for seat in sorted(author_seats):
            if (doc_id, r["form_id"], seat) not in occupied:
                target = seat
                break
        verdict = (f"SAFE  -> re-tag to {target}" if target
                   else "NEEDS A HUMAN (author holds no free seat here)")
        print(f"    {label(doc_id):<28} {role} by {name(author):<24} "
              f"slot now {name(holder.get((doc_id, role)))!r}")
        print(f"      row {r['id']}  {verdict}")
        if target:
            planned.append((r["id"], target))

    # ── 2. same author in both seats ────────────────────────────────────────
    print(f"\n[2] SAME AUTHOR IN BOTH READER SEATS — not independent: {len(same_author)}")
    if same_author:
        print("    Never repaired automatically. Decide which row is the real")
        print("    extraction, delete the other via DELETE /api/v1/results/{id},")
        print("    and have a second reviewer redo that paper.")
    for doc_id, form_id, rows in same_author:
        who = {r["reviewer_role"]: r for r in rows if r.get("reviewer_role") in READERS}
        print(f"    {label(doc_id):<28} form {form_id}")
        for role in READERS:
            r = who.get(role)
            if r:
                print(f"      {role:<11} {r['id']}  by {name(r.get('extracted_by'))}"
                      f"  saved {r.get('created_at')}")

    # ── 3. untagged ─────────────────────────────────────────────────────────
    print(f"\n[3] UNTAGGED ROWS — no reviewer_role, invisible to comparison: {len(untagged)}")
    for r in untagged:
        doc_id = r["document_id"]
        author = str(r.get("extracted_by") or "")
        seats = holds[(doc_id, author)] & set(READERS)
        target = None
        if len(seats) == 1:
            seat = next(iter(seats))
            if (doc_id, r["form_id"], seat) not in occupied:
                target = seat
        verdict = (f"SAFE  -> tag as {target}" if target
                   else "NEEDS A HUMAN (author holds no single free seat)")
        print(f"    {label(doc_id):<28} by {name(author):<24} row {r['id']}  {verdict}")
        if target:
            planned.append((r["id"], target))

    # ── apply ───────────────────────────────────────────────────────────────
    print(f"\n{'-'*78}")
    if not planned:
        print("No SAFE repairs available. Everything above needs a human decision.")
        return
    if not args.apply:
        print(f"{len(planned)} SAFE repair(s) available. Re-run with --apply to perform them.")
        return
    for row_id, role in planned:
        sb.table("extraction_results").update({"reviewer_role": role})\
          .eq("id", row_id).execute()
        print(f"  re-tagged {row_id} -> {role}")
    print(f"\nDone: {len(planned)} row(s) re-tagged.")


if __name__ == "__main__":
    main()
