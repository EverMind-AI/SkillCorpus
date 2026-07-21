"""Round C-3 — incremental source re-sync, avoiding repeated LLM calls for unchanged skills.

Difference from add-batch:
  add-batch:        every SKILL.md runs the full pipeline (parse + LLM classify + embed +
                    LLM quality); unchanged ones are only skipped via content_hash DUPLICATE
                    at the very end of ingest, after the earlier LLM calls have been wasted.
  source_resync:    reads content_hash first; already in the library + same source → SKIP_UNCHANGED
                    directly, without running the LLM / without counting a duplicate against the
                    LLM quota. Only changed or new ones run the full pipeline.

For a re-run over 1,933 awesome skills, the unchanged rate is ~99%, saving 99% of LLM calls.

Usage:
    python -m skill_library.scripts.source_resync \
        experiment-results/_reference_skills/_fetched/anthropics/skills \
        --source anthropics [--lib PATH] [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from skill_library import SkillLibrary  # noqa: E402
from skill_library.dedup import content_hash  # noqa: E402
from skill_library.rules import (  # noqa: E402
    ParseError, ValidationError, parse_skill_file, validate_skill,
)


def _scan(root: Path) -> list[Path]:
    return [p for p in root.glob("**/SKILL.md") if "/workspaces/" not in str(p)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("root", help="source root dir to scan (**/SKILL.md)")
    ap.add_argument("--source", required=True)
    ap.add_argument("--lib", default=None)
    ap.add_argument("--dry-run", action="store_true",
                    help="only report changes, do not call lib.add")
    ap.add_argument("--report", default=None)
    args = ap.parse_args()

    root = Path(args.root).resolve()
    if not root.is_dir():
        print(f"not a directory: {root}", file=sys.stderr)
        sys.exit(1)

    lib = SkillLibrary(args.lib).open()
    conn = lib.store._connect()

    md_paths = _scan(root)
    print(f"lib: {lib.lib_root}")
    print(f"source: {args.source}")
    print(f"scanning: {root} → {len(md_paths)} SKILL.md")

    unchanged: list[str] = []        # content_hash hit, same source
    dup_other_source: list[str] = [] # content_hash hit but different source (conflict)
    updates: list[str] = []          # new content_hash, same source already has the same canonical name
    new_adds: list[str] = []
    parse_errors: list[tuple[str, str]] = []

    # first scan and categorize by hash in one pass (without calling ingest)
    for md in md_paths:
        skill_dir = md.parent
        try:
            fm, body = parse_skill_file(md)
            validate_skill(fm, body)
        except (ParseError, ValidationError) as e:
            parse_errors.append((str(skill_dir), f"{type(e).__name__}: {e}"))
            continue

        c_hash = content_hash(body)
        # include deleted records — the same skill previously superseded is treated as unchanged,
        # to avoid a re-ingest loop + being merged again
        row = conn.execute(
            "SELECT skill_id, source, deleted, superseded_by "
            "FROM skills WHERE content_hash = ?",
            (c_hash,),
        ).fetchone()
        if row is not None:
            if row["source"] == args.source:
                unchanged.append(str(skill_dir))
            else:
                dup_other_source.append(f"{skill_dir}  (already in {row['source']})")
            continue

        # content is new. check whether same source + same canonical name exists (→ update path)
        from skill_library.dedup import name_hash
        n_hash = name_hash(str(fm["name"]))
        same = conn.execute(
            "SELECT skill_id FROM skills WHERE name_hash = ? AND source = ? AND deleted = 0",
            (n_hash, args.source),
        ).fetchone()
        if same:
            updates.append(str(skill_dir))
        else:
            new_adds.append(str(skill_dir))

    print()
    print(f"== scan summary ==")
    print(f"  unchanged (content_hash hit same source): {len(unchanged)}")
    print(f"  updates (same source canonical name):     {len(updates)}")
    print(f"  new adds:                                 {len(new_adds)}")
    print(f"  dup-other-source (skip, conflict):        {len(dup_other_source)}")
    print(f"  parse errors:                             {len(parse_errors)}")

    if args.dry_run:
        print("\ndry-run — no ingest")
    else:
        print(f"\n== ingesting {len(updates) + len(new_adds)} changed/new skills ==")
        t0 = time.time()
        ingest_results = []
        for skill_dir in updates + new_adds:
            try:
                r = lib.add(skill_dir, source=args.source)
                ingest_results.append({"dir": skill_dir, "status": r.status.value,
                                       "reason": r.reason})
            except Exception as e:
                ingest_results.append({"dir": skill_dir, "status": "exception",
                                       "reason": str(e)})
        elapsed = time.time() - t0
        from collections import Counter
        c = Counter(r["status"] for r in ingest_results)
        print(f"  elapsed: {elapsed:.1f}s")
        for k, v in c.most_common():
            print(f"    {k:25s} {v}")

    if args.report:
        Path(args.report).write_text(json.dumps({
            "lib": str(lib.lib_root), "source": args.source, "root": str(root),
            "unchanged": len(unchanged),
            "updates": len(updates),
            "new_adds": len(new_adds),
            "dup_other_source": dup_other_source[:20],
            "parse_errors": parse_errors[:20],
            "dry_run": args.dry_run,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nReport: {args.report}")

    lib.close()


if __name__ == "__main__":
    main()
