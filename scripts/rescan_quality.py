"""Round B backfill — 对所有 active skill 跑 LLM 质量打分.

流程:
  1. 遍历所有 deleted=0 skill
  2. 并发 (ThreadPoolExecutor) 调 quality_judge.score(rec)
  3. 结果自动写入 quality_judgments 表, 已 cache 的 skipped
  4. 输出 score 直方图 + avg/min/max

本次不修改 skills.quality_score (那是 Round B-3 做的 —
        quality.compute_quality 重构后统一重算).

用法:
    python -m skill_library.scripts.rescan_quality [--lib PATH] [--limit N] [--workers 8]
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from skill_library import SkillLibrary  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lib", default=None)
    ap.add_argument("--limit", type=int, default=None,
                    help="只跑前 N 个 active skill (调试用)")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--skip-cached", action="store_true", default=True,
                    help="跳过已判过的 content_hash (默认 True, cache 命中)")
    ap.add_argument("--report", default=None)
    args = ap.parse_args()

    lib = SkillLibrary(args.lib).open()
    if lib.quality_judge is None:
        print("LLM quality judge unavailable — 退出", file=sys.stderr)
        lib.close()
        sys.exit(1)

    conn = lib.store._connect()
    # 主线程批量加载 (skill_id, content_hash, name, description, body) 避免
    # worker 线程碰 SQLite (Python sqlite3 默认不允许跨线程)
    rows = conn.execute(
        "SELECT skill_id, content_hash, name, description, body "
        "FROM skills WHERE deleted = 0 ORDER BY skill_id"
    ).fetchall()
    if args.limit:
        rows = rows[:args.limit]
    total = len(rows)
    print(f"lib: {lib.lib_root}")
    print(f"target: {total} active skills, workers={args.workers}")

    cached_hashes: set[str] = set()
    if args.skip_cached:
        cached_hashes = {r[0] for r in conn.execute(
            "SELECT content_hash FROM quality_judgments"
        ).fetchall()}
    if cached_hashes:
        print(f"existing cached judgments: {len(cached_hashes)} (will be skipped)")

    # 过滤出需要跑 LLM 的 work list (没 cache 的)
    work_list = [r for r in rows if r["content_hash"] not in cached_hashes]
    skipped_cached = total - len(work_list)
    print(f"to score: {len(work_list)}  skipped_cached: {skipped_cached}")

    from skill_library.store import SkillRecord

    def _worker_score(row):
        """Worker: 构造 SkillRecord, 调 compute_no_cache (纯 LLM, 无 SQLite)."""
        rec = SkillRecord(
            skill_id=row["skill_id"], content_hash=row["content_hash"],
            name=row["name"], description=row["description"], body=row["body"],
        )
        j = lib.quality_judge.compute_no_cache(rec)
        return (rec.skill_id, rec.content_hash, j)

    t0 = time.time()
    done = 0
    failed = 0
    new_scores: list[float] = []

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(_worker_score, r) for r in work_list]
        for fut in as_completed(futures):
            sid, chash, j = fut.result()
            done += 1
            if j is None:
                failed += 1
            else:
                new_scores.append(j.score)
                lib.quality_judge.cache_put(chash, j)

            if done % max(len(work_list) // 40 or 1, 1) == 0:
                pct = 100 * done / max(len(work_list), 1)
                elapsed = time.time() - t0
                rate = done / max(elapsed, 0.1)
                eta = (len(work_list) - done) / max(rate, 0.1)
                print(f"  {done}/{len(work_list)} ({pct:.0f}%)  new={len(new_scores)}  "
                      f"failed={failed}  rate={rate:.1f}/s  eta={eta:.0f}s")

    skipped = skipped_cached

    elapsed = time.time() - t0

    # 汇总 (含之前 cache 的 + 本次新打)
    stats = lib.quality_judge.stats()
    hist = lib.quality_judge.histogram()

    print()
    print(f"=== Round B — quality backfill done in {elapsed:.1f}s ===")
    print(f"  processed: {done}")
    print(f"  new llm_calls: {len(new_scores)}")
    print(f"  cached hits:   {skipped}")
    print(f"  failed:        {failed}")
    print()
    print(f"Aggregate (from quality_judgments table — includes prior cache):")
    print(f"  total judgments: {stats['total']}")
    print(f"  avg / min / max: {stats['avg_score']} / {stats['min_score']} / {stats['max_score']}")
    print(f"  histogram: {hist}")

    if args.report:
        Path(args.report).write_text(json.dumps({
            "lib": str(lib.lib_root),
            "processed": done,
            "new_scores": len(new_scores),
            "cached_hits": skipped,
            "failed": failed,
            "elapsed_sec": round(elapsed, 1),
            "aggregate": stats,
            "histogram": hist,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nReport: {args.report}")

    lib.close()


if __name__ == "__main__":
    main()
