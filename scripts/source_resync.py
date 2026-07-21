"""Round C-3 — 增量 source re-sync, 避免 unchanged skill 重复调 LLM.

与 add-batch 的区别:
  add-batch:        每个 SKILL.md 走完整 pipeline (parse + LLM classify + embed +
                    LLM quality); unchanged 靠 content_hash DUPLICATE 在 ingest
                    最后才 skip, 前面的 LLM 调用已经浪费了.
  source_resync:    先读 content_hash, 库里已存在 + 同 source → 直接 SKIP_UNCHANGED,
                    不跑 LLM / 不计 duplicate 到 LLM quota. 能变的或新的才走完整 pipeline.

对 1,933 awesome skill 的重跑, unchanged 率 ~ 99%, 可节省 99% 的 LLM 调用.

用法:
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
                    help="只报告变化, 不调 lib.add")
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

    unchanged: list[str] = []        # content_hash 命中, 同 source
    dup_other_source: list[str] = [] # content_hash 命中但 source 不同 (冲突)
    updates: list[str] = []          # 新 content_hash, 同 source 已有同 canonical name
    new_adds: list[str] = []
    parse_errors: list[tuple[str, str]] = []

    # 先一次性扫 hash 归类 (不调 ingest)
    for md in md_paths:
        skill_dir = md.parent
        try:
            fm, body = parse_skill_file(md)
            validate_skill(fm, body)
        except (ParseError, ValidationError) as e:
            parse_errors.append((str(skill_dir), f"{type(e).__name__}: {e}"))
            continue

        c_hash = content_hash(body)
        # 含 deleted records — 之前被 supersede 的同一 skill 视为 unchanged,
        # 避免循环 re-ingest + 被再次合并
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

        # content 新. 查同 source + 同 canonical name 是否存在 (→ update 路径)
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
        print("\ndry-run — 不 ingest")
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
