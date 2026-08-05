"""Run one near-duplicate scan over the whole library — Round A backfill.

Flow:
  1. GROUP BY canonical name_hash to find same-name candidates across sources
  2. iterate over all skills querying embedding top-k candidates (cos >= min_cosine)
  3. merge into a unique candidate pair set (a.skill_id < b.skill_id)
  4. call LLMDupJudge on each pair (use the cached verdict directly on a cache hit)
  5. for confirmed-duplicate pairs: compare quality_score + source_weight, supersede the loser

Usage:
    python -m skill_library.scripts.rescan_dedup [--lib PATH] [--dry-run] [--limit N]
"""

from __future__ import annotations

import argparse
import json
import struct
import sys
import time
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))


from skill_library import SkillLibrary  # noqa: E402
from skill_library.core.hashing import cosine_sim  # noqa: E402
from skill_library.core.store import VEC_TABLE_NAME  # noqa: E402
from skill_library.core.store import remove_skill_from_library  # noqa: E402


def _load_embedding(conn, skill_id: str) -> list[float] | None:
    row = conn.execute(
        f"SELECT embedding FROM {VEC_TABLE_NAME} WHERE skill_id = ?",
        (skill_id,),
    ).fetchone()
    if not row:
        return None
    emb_bytes = row["embedding"]
    if not emb_bytes:
        return None
    n = len(emb_bytes) // 4
    return list(struct.unpack(f"{n}f", emb_bytes))


def _collect_candidates(lib: SkillLibrary, min_cos: float, top_k: int, limit: int | None):
    """Collect candidate pairs (a, b, cos, trigger) where a.skill_id < b.skill_id."""
    store = lib.store
    conn = store._connect()

    # 1) name_hash collisions (across sources, including multiple copies within a source)
    name_groups: dict[str, list[str]] = defaultdict(list)
    rows = conn.execute(
        "SELECT skill_id, name_hash, quality_score FROM skills WHERE deleted = 0"
    ).fetchall()
    quality: dict[str, float] = {}
    for r in rows:
        name_groups[r["name_hash"]].append(r["skill_id"])
        quality[r["skill_id"]] = r["quality_score"] or 0.0

    pairs: dict[tuple[str, str], tuple[float, str]] = {}
    name_pair_count = 0
    _emb_cache: dict[str, list[float] | None] = {}

    def _emb(sid: str) -> list[float] | None:
        if sid not in _emb_cache:
            _emb_cache[sid] = _load_embedding(conn, sid)
        return _emb_cache[sid]

    _MAX_GROUP = 100
    for nh, ids in name_groups.items():
        if len(ids) < 2:
            continue
        if len(ids) > _MAX_GROUP:
            # Bound the O(k^2) pairing on pathological same-name groups (e.g.
            # "code-review" recurring across thousands of repos would otherwise
            # queue millions of LLM dup judgments). Keep the top-N by quality;
            # log the rest as skipped rather than silently dropping them.
            dropped = len(ids) - _MAX_GROUP
            ids = sorted(ids, key=lambda s: quality.get(s, 0.0),
                         reverse=True)[:_MAX_GROUP]
            print(f"  [dedup] name_hash group {nh[:12]} capped to top "
                  f"{_MAX_GROUP} by quality ({dropped} skipped)")
        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                a, b = sorted([ids[i], ids[j]])
                key = (a, b)
                if key not in pairs:
                    # use the **real cosine**, no longer hardcoded to 1.0 (lesson: 1.0 + auto_cos
                    # short-circuit → 7148/68% cross-source false-positive merges). missing embedding
                    # → use min_cos so it goes to the LLM second judgment, not an auto-merge.
                    ea, eb = _emb(a), _emb(b)
                    cos = cosine_sim(ea, eb) if (ea is not None and eb is not None) else min_cos
                    pairs[key] = (cos, "name_hash")
                    name_pair_count += 1
    print(f"[1/4] name_hash collisions: {name_pair_count} pairs across "
          f"{sum(1 for v in name_groups.values() if len(v) > 1)} groups")

    # 2) embedding nearest-neighbors (iterate over each skill)
    skill_ids = [r["skill_id"] for r in rows]
    if limit:
        skill_ids = skill_ids[:limit]

    emb_pair_count = 0
    skipped = 0
    for idx, sid in enumerate(skill_ids):
        emb = _load_embedding(conn, sid)
        if emb is None:
            skipped += 1
            continue
        near = store.find_near_duplicates(
            emb, exclude_skill_id=sid, top_k=top_k, min_cosine=min_cos,
        )
        for rec, cos in near:
            if rec.skill_id == sid:
                continue
            a, b = sorted([sid, rec.skill_id])
            key = (a, b)
            if key in pairs:
                # upgrade cos
                prev_cos, prev_trigger = pairs[key]
                pairs[key] = (max(prev_cos, cos),
                              "both" if prev_trigger == "name_hash" else prev_trigger)
            else:
                pairs[key] = (cos, "embedding")
                emb_pair_count += 1
        if (idx + 1) % 200 == 0:
            print(f"  scanned {idx + 1}/{len(skill_ids)} — {len(pairs)} pairs so far")
    print(f"[2/4] embedding near pairs: {emb_pair_count} new (skipped {skipped} "
          "without embedding)")

    return pairs


def _apply_merge(lib: SkillLibrary, a_id: str, b_id: str, cos: float, trigger: str,
                 dry_run: bool) -> dict | None:
    """Apply the merge for a confirmed-duplicate pair. Returns an operation record or None."""
    a = lib.store.get(a_id)
    b = lib.store.get(b_id)
    if a is None or b is None:
        return None
    winner = lib.ingester._pick_winner(a, b)

    # _pick_winner's "new" is the first argument. Here we treat a as "new" and b as "old",
    # so winner == "new" → keep a, drop b
    # winner == "old" → keep b, drop a
    if winner == "new":
        keep, drop = a, b
    else:
        keep, drop = b, a

    if dry_run:
        return {
            "winner": keep.skill_id, "loser": drop.skill_id,
            "winner_q": keep.quality_score, "loser_q": drop.quality_score,
            "cos": round(cos, 3), "trigger": trigger,
            "winner_src": keep.source, "loser_src": drop.source,
            "dry_run": True,
        }

    # real merge: delete physical files + supersede
    if drop.stored_path:
        remove_skill_from_library(lib.lib_root, drop.stored_path)
    lib.store.supersede(drop.skill_id, keep.skill_id)
    return {
        "winner": keep.skill_id, "loser": drop.skill_id,
        "winner_q": keep.quality_score, "loser_q": drop.quality_score,
        "winner_src": keep.source, "loser_src": drop.source,
        "cos": round(cos, 3), "trigger": trigger,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lib", default=None, help="library root (default: skill_library/data)")
    ap.add_argument("--dry-run", action="store_true",
                    help="scan + LLM judge only, do not actually supersede / delete files")
    ap.add_argument("--limit", type=int, default=None,
                    help="only process the first N skills as anchors (for debugging)")
    ap.add_argument("--min-cos", type=float, default=None,
                    help="override near_dup_min_cosine from config")
    ap.add_argument("--top-k", type=int, default=None,
                    help="override near_dup_top_k from config")
    ap.add_argument("--max-pairs", type=int, default=None,
                    help="max number of candidate pairs to process (debug/cost control)")
    ap.add_argument("--report", default=None,
                    help="JSON path to dump the merge report")
    args = ap.parse_args()

    lib = SkillLibrary(args.lib).open()
    dedup_cfg = lib.config.get("dedup", {}) or {}
    min_cos = args.min_cos if args.min_cos is not None else float(dedup_cfg.get("near_dup_min_cosine", 0.90))
    top_k = args.top_k if args.top_k is not None else int(dedup_cfg.get("near_dup_top_k", 5))
    auto_cos = float(dedup_cfg.get("near_dup_auto_cosine", 0.995))

    print(f"lib: {lib.lib_root}")
    print(f"config: min_cos={min_cos}, top_k={top_k}, auto_cos={auto_cos}")
    if lib.dup_judge is None:
        print("!! LLM dup judge unavailable — can only judge auto-duplicates with cos >= auto_cos")

    t0 = time.time()
    pairs = _collect_candidates(lib, min_cos, top_k, args.limit)
    print(f"[3/4] total candidate pairs: {len(pairs)}, "
          f"elapsed={time.time()-t0:.1f}s")

    # LLM judge + merge
    # Phase 4 splits into 2 sub-phases:
    #   4a. concurrent LLM judge (the remote endpoint has plenty of capacity, single-threaded
    #       serial only used 1/8); 8 workers call the LLM at once, throughput ~5-8x
    #   4b. serial apply_merge (DB writes are serial, also avoids races)
    pairs_sorted = sorted(pairs.items(), key=lambda kv: -kv[1][0])  # cos descending
    if args.max_pairs:
        pairs_sorted = pairs_sorted[:args.max_pairs]

    from concurrent.futures import ThreadPoolExecutor

    # pre-fetch all SkillRecords (single-threaded, to avoid workers hitting sqlite multithreading issues)
    needed_ids: set[str] = set()
    for (a_id, b_id), _ in pairs_sorted:
        needed_ids.add(a_id)
        needed_ids.add(b_id)
    print(f"[4a/4] pre-fetching {len(needed_ids)} skill records...")
    rec_cache: dict[str, "SkillRecord | None"] = {
        sid: lib.store.get(sid) for sid in needed_ids
    }

    def _judge_pair_cached(item):
        """Worker (uses the pre-fetched cache, does not touch lib.store)."""
        (a_id, b_id), (cos, trigger) = item
        a, b = rec_cache.get(a_id), rec_cache.get(b_id)
        if a is None or b is None:
            return None
        if cos >= auto_cos:
            return (a_id, b_id, cos, trigger, True, False)
        if lib.dup_judge is None:
            return None
        j = lib.dup_judge.is_duplicate(a, b)
        return (a_id, b_id, cos, trigger, j.is_duplicate, True)

    print(f"[4a/4] parallel judging {len(pairs_sorted)} pairs (workers=8)...")
    verdicts: list[tuple] = []
    llm_calls = 0
    auto_calls = 0
    t_judge = time.time()
    with ThreadPoolExecutor(max_workers=8) as pool:
        for i, result in enumerate(pool.map(_judge_pair_cached, pairs_sorted)):
            if result is None:
                continue
            _, _, _, _, is_dup, used_llm = result
            if used_llm:
                llm_calls += 1
            else:
                auto_calls += 1
            if is_dup:
                verdicts.append(result)
            if (i + 1) % 200 == 0:
                rate = (i + 1) / (time.time() - t_judge)
                print(f"  judged {i + 1}/{len(pairs_sorted)} "
                      f"@ {rate:.1f}/s — confirmed dups: {len(verdicts)}")

    print(f"[4b/4] applying {len(verdicts)} merges (serial)...")
    merges: list[dict] = []
    for a_id, b_id, cos, trigger, _, _ in verdicts:
        m = _apply_merge(lib, a_id, b_id, cos, trigger, args.dry_run)
        if m:
            merges.append(m)

    print(f"[4/4] done in {time.time()-t0:.1f}s — "
          f"llm_calls={llm_calls}, auto_calls={auto_calls}, merges={len(merges)}")

    # aggregate by source pair
    by_src = defaultdict(int)
    for m in merges:
        k = f"{m['winner_src']}  <-  {m['loser_src']}"
        by_src[k] += 1
    print("\nMerges by (winner_src <- loser_src):")
    for k, v in sorted(by_src.items(), key=lambda x: -x[1]):
        print(f"  {v:4d}  {k}")

    if args.report:
        Path(args.report).write_text(
            json.dumps({
                "lib": str(lib.lib_root),
                "dry_run": args.dry_run,
                "min_cos": min_cos, "top_k": top_k, "auto_cos": auto_cos,
                "pairs_total": len(pairs),
                "llm_calls": llm_calls, "auto_calls": auto_calls,
                "merges": merges,
            }, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"\nReport saved to {args.report}")

    lib.close()


if __name__ == "__main__":
    main()
