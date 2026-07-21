"""Round B backfill — run LLM quality scoring over all active skills.

Flow:
  1. iterate over all deleted=0 skills
  2. concurrently (ThreadPoolExecutor) call quality_judge.score(rec)
  3. results are written to the quality_judgments table automatically; already-cached ones are skipped
  4. output a score histogram + avg/min/max

This run does not modify skills.quality_score (that is Round B-3's job —
        recomputed uniformly after the quality.compute_quality refactor).

Usage:
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
                    help="only process the first N active skills (for debugging)")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--skip-cached", action="store_true", default=True,
                    help="skip already-judged content_hash (default True, cache hit)")
    ap.add_argument("--report", default=None)
    args = ap.parse_args()

    lib = SkillLibrary(args.lib).open()
    if lib.quality_judge is None:
        print("LLM quality judge unavailable — exiting", file=sys.stderr)
        lib.close()
        sys.exit(1)

    conn = lib.store._connect()
    # main thread bulk-loads (skill_id, content_hash, name, description, body) to avoid
    # worker threads touching SQLite (Python sqlite3 disallows cross-thread use by default)
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

    # filter out the work list that needs the LLM (those without cache)
    work_list = [r for r in rows if r["content_hash"] not in cached_hashes]
    skipped_cached = total - len(work_list)
    print(f"to score: {len(work_list)}  skipped_cached: {skipped_cached}")

    from skill_library.store import SkillRecord

    def _worker_score(row):
        """Worker: build a SkillRecord, call compute_no_cache (pure LLM, no SQLite)."""
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

    # aggregate (includes previously cached + newly scored this run)
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
