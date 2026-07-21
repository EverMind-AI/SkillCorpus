"""对整库跑一次近似去重扫描 — Round A backfill.

流程:
  1. GROUP BY canonical name_hash 找跨 source 同名候选
  2. 遍历所有 skill 查 embedding top-k 候选 (cos >= min_cosine)
  3. 合并成唯一 candidate pair 集合 (a.skill_id < b.skill_id)
  4. 对每对调 LLMDupJudge (缓存命中直接用)
  5. 确认重复的 pair: 比较 quality_score + source_weight, loser supersede

用法:
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
from skill_library.dedup import cosine_sim  # noqa: E402
from skill_library.store import VEC_TABLE_NAME  # noqa: E402
from skill_library.store import remove_skill_from_library  # noqa: E402


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
    """收集候选 pair (a, b, cos, trigger) 其中 a.skill_id < b.skill_id."""
    store = lib.store
    conn = store._connect()

    # 1) name_hash 冲突 (跨 source, 包含同 source 多份)
    name_groups: dict[str, list[str]] = defaultdict(list)
    rows = conn.execute(
        "SELECT skill_id, name_hash FROM skills WHERE deleted = 0"
    ).fetchall()
    for r in rows:
        name_groups[r["name_hash"]].append(r["skill_id"])

    pairs: dict[tuple[str, str], tuple[float, str]] = {}
    name_pair_count = 0
    _emb_cache: dict[str, list[float] | None] = {}

    def _emb(sid: str) -> list[float] | None:
        if sid not in _emb_cache:
            _emb_cache[sid] = _load_embedding(conn, sid)
        return _emb_cache[sid]

    for nh, ids in name_groups.items():
        if len(ids) < 2:
            continue
        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                a, b = sorted([ids[i], ids[j]])
                key = (a, b)
                if key not in pairs:
                    # 用**真 cosine**, 不再硬置 1.0 (历史教训: 1.0 + auto_cos 短路
                    # → 7148/68% 跨 source 假阳合并). embedding 缺失 → 用 min_cos
                    # 让它走 LLM 二判, 不自动合并.
                    ea, eb = _emb(a), _emb(b)
                    cos = cosine_sim(ea, eb) if (ea is not None and eb is not None) else min_cos
                    pairs[key] = (cos, "name_hash")
                    name_pair_count += 1
    print(f"[1/4] name_hash collisions: {name_pair_count} pairs across "
          f"{sum(1 for v in name_groups.values() if len(v) > 1)} groups")

    # 2) embedding 近邻 (遍历每个 skill)
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
                # 升级 cos
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
    """对确认重复的 pair 应用合并. 返回操作记录或 None."""
    a = lib.store.get(a_id)
    b = lib.store.get(b_id)
    if a is None or b is None:
        return None
    winner = lib.ingester._pick_winner(a, b)
    loser_rec = b if winner == "new" else a  # 注意: _pick_winner("new"=a的角色?)

    # _pick_winner 的 "new" 是第一个参数. 这里把 a 当 "new", b 当 "old"
    # 所以 winner == "new" → 保留 a, 淘汰 b
    # winner == "old" → 保留 b, 淘汰 a
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

    # 真实合并: 删物理 + supersede
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
                    help="只扫描 + LLM 判, 不实际 supersede / 删文件")
    ap.add_argument("--limit", type=int, default=None,
                    help="只处理前 N 个 skill 作为 anchor (调试用)")
    ap.add_argument("--min-cos", type=float, default=None,
                    help="覆盖 config 里的 near_dup_min_cosine")
    ap.add_argument("--top-k", type=int, default=None,
                    help="覆盖 config 里的 near_dup_top_k")
    ap.add_argument("--max-pairs", type=int, default=None,
                    help="最多处理多少对候选 (调试/成本控制)")
    ap.add_argument("--report", default=None,
                    help="merge report 落盘 JSON 路径")
    args = ap.parse_args()

    lib = SkillLibrary(args.lib).open()
    dedup_cfg = lib.config.get("dedup", {}) or {}
    min_cos = args.min_cos if args.min_cos is not None else float(dedup_cfg.get("near_dup_min_cosine", 0.90))
    top_k = args.top_k if args.top_k is not None else int(dedup_cfg.get("near_dup_top_k", 5))
    auto_cos = float(dedup_cfg.get("near_dup_auto_cosine", 0.995))

    print(f"lib: {lib.lib_root}")
    print(f"config: min_cos={min_cos}, top_k={top_k}, auto_cos={auto_cos}")
    if lib.dup_judge is None:
        print("!! LLM dup judge unavailable — 只能判 cos >= auto_cos 的自动重复")

    t0 = time.time()
    pairs = _collect_candidates(lib, min_cos, top_k, args.limit)
    print(f"[3/4] total candidate pairs: {len(pairs)}, "
          f"elapsed={time.time()-t0:.1f}s")

    # LLM 判 + 合并
    # Phase 4 拆 2 子阶段:
    #   4a. 并发 LLM judge (远端 endpoint 容量大, 单线程串行只用了 1/8)
    #       8 worker 同时调 LLM, throughput ~5-8x
    #   4b. 串行 apply_merge (DB 写串行, 也避免 race)
    pairs_sorted = sorted(pairs.items(), key=lambda kv: -kv[1][0])  # cos 降序
    if args.max_pairs:
        pairs_sorted = pairs_sorted[:args.max_pairs]

    from concurrent.futures import ThreadPoolExecutor

    # 预取所有 SkillRecord (单线程, 避免 worker 撞 sqlite 多线程问题)
    needed_ids: set[str] = set()
    for (a_id, b_id), _ in pairs_sorted:
        needed_ids.add(a_id)
        needed_ids.add(b_id)
    print(f"[4a/4] pre-fetching {len(needed_ids)} skill records...")
    rec_cache: dict[str, "SkillRecord | None"] = {
        sid: lib.store.get(sid) for sid in needed_ids
    }

    def _judge_pair_cached(item):
        """Worker (改用预取 cache, 不 touch lib.store)."""
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

    # 汇总 by source pair
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
