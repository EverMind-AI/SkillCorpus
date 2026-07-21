"""iter3 升级端到端 smoke test — 一次覆盖 review 的多个集成边界 regression.

这些坑 (migration 路径 / recompute 脚本 / producer↔consumer 标签握手) 都在
**集成边界**, 普通单测覆盖不到。本文件用一个全自给 (无网络 / 无真 LLM / 无真
embedder) 的迷你库, 走一遍升级链路, 断言每个修复点都没回归:

  阶段 1  active 列升级回填   (🔴#2)  — 老库升级后 active 全 0, cmd_activate
                                        按 GREEN 白名单只激活不停用
  阶段 2  export 默认 label    (🔴#5)  — 不传 --embedding-model 时 label 取自
                                        config.yaml (= consumer 端配置), 非硬编码
  阶段 2  export 非空           (🔴#2)  — 回填后 mass_library.db 有行 (修复前为空)
  阶段 3  quality cache 命中     (🔴#3)  — 按 content_hash 命中 (judge 冻结, 无版本键)
  阶段 4  rescan_dedup name_hash(🔴#1)  — name_hash 对用真 cosine, 不再硬置 1.0
                                        自动合并 (历史 7148/68% 假阳)
  阶段 5  license 白名单一致    (🔴#4)  — JSON green_categories == 代码 GREEN set
"""

from __future__ import annotations

import json
import sqlite3
import tempfile
from argparse import Namespace
from pathlib import Path

from skill_library import SkillLibrary
from skill_library.store import SkillRecord
from skill_library.dedup import name_hash, cosine_sim
from skill_library import license_audit
from skill_library import export as export_mod
from skill_library.export import export, _config_embedding
from skill_library.scripts.rescan_dedup import _collect_candidates
from skill_library.metadata import (
    LLMQualityJudge, QualityJudgment,
)
from skill_library.rules import GREEN_LICENSES


def _rec(sid, name, source, body, ch) -> SkillRecord:
    return SkillRecord(
        skill_id=sid, name=name, description="desc " * 10, body=body,
        source=source, content_hash=ch, name_hash=name_hash(name),
        quality_score=0.6,
    )


def _emb(dim, half):
    """单位方向向量: half=0 → 前半=1, half=1 → 后半=1. 两者 cosine ≈ 0."""
    v = [0.0] * dim
    lo, hi = (0, dim // 2) if half == 0 else (dim // 2, dim)
    for i in range(lo, hi):
        v[i] = 1.0
    return v


def _build_lib(root: Path):
    """迷你库: 2 个 GREEN 源行 + 1 个非 GREEN 行, 都带 embedding.

    源名都不在真实 license_safe_sources.json 里 → store.insert 全部 active=0,
    正好复现 '老库升级后 active 列默认全 0' 的状态, 无需手工改。
    """
    lib = SkillLibrary(root).open()
    dim = lib.store.embedding_dim
    lib.store.insert(_rec("g1", "alpha-skill", "greensrc/repo",
                          "body alpha " * 20, "h_g1"), embedding=_emb(dim, 0))
    lib.store.insert(_rec("g2", "beta-skill", "greensrc/repo",
                          "body beta " * 20, "h_g2"), embedding=_emb(dim, 1))
    lib.store.insert(_rec("r1", "gamma-skill", "redsrc/repo",
                          "body gamma " * 20, "h_r1"), embedding=_emb(dim, 0))
    return lib, dim


def _active_count(db_path: Path) -> int:
    con = sqlite3.connect(db_path)
    try:
        return con.execute(
            "SELECT COUNT(*) FROM skills WHERE active=1 AND deleted=0"
        ).fetchone()[0]
    finally:
        con.close()


def test_iter3_upgrade_smoke():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        lib, dim = _build_lib(tmp / "lib")
        db_path = lib.lib_root / "index.db"

        # ── 阶段 1: active 升级回填 (🔴#2) ──────────────────────────────
        # 升级后症状: 所有行 active=0 → export 会产出空库。
        assert _active_count(db_path) == 0, "升级态应为 active 全 0 (复现 bug 前提)"

        green_json = tmp / "green.json"
        green_json.write_text(json.dumps({
            "green_categories": sorted(GREEN_LICENSES),
            "sources": ["greensrc/repo"],   # 只有 greensrc 是 GREEN
        }), encoding="utf-8")

        rc = license_audit.cmd_activate(Namespace(
            json=str(green_json), db=str(db_path), dry_run=False))
        assert rc == 0
        # GREEN 源 2 行被激活; 非 GREEN 的 r1 保持 0 (只激活不停用)
        con = sqlite3.connect(db_path)
        act = {r[0]: r[1] for r in con.execute(
            "SELECT skill_id, active FROM skills").fetchall()}
        con.close()
        assert act == {"g1": 1, "g2": 1, "r1": 0}, f"activate 结果错: {act}"

        # 幂等: 再跑一次结果不变
        license_audit.cmd_activate(Namespace(
            json=str(green_json), db=str(db_path), dry_run=False))
        assert _active_count(db_path) == 2, "activate 不幂等"

        # ── 阶段 2: export 默认 label 取自 config + 非空 (🔴#5 / 🔴#2) ──
        mass = tmp / "mass_library.db"
        cfg_model, _cfg_dim = _config_embedding()
        export(lib.lib_root, mass)   # 不传 embedding_model → 应读 config.yaml

        mcon = sqlite3.connect(mass)
        try:
            n = mcon.execute("SELECT COUNT(*) FROM skills").fetchone()[0]
            labels = {r[0] for r in mcon.execute(
                "SELECT DISTINCT embedding_model FROM skills "
                "WHERE embedding IS NOT NULL").fetchall()}
        finally:
            mcon.close()
        assert n == 2, f"回填后 export 应有 2 行, 实得 {n} (🔴#2: 升级后空库)"
        assert labels == {cfg_model}, \
            f"embedding_model 应 = config '{cfg_model}', 实得 {labels} (🔴#5)"

        # ── 阶段 3: quality cache 按 content_hash 命中 (judge 冻结, 无版本键) ──
        qconn = sqlite3.connect(":memory:")
        qconn.row_factory = sqlite3.Row

        class _FakeLLM:
            def chat(self, *a, **k):
                return json.dumps({"utility": 8, "robustness": 7,
                                   "safety": 9, "flags": [], "reason": "x"})

        judge = LLMQualityJudge(_FakeLLM(), qconn)
        judge.cache_put("h_new", QualityJudgment(
            score=7.8, normalized=0.78, reason="new",
            utility=8, robustness=7, safety=9, flags=[]))
        assert judge._cache_get("h_new") is not None, "已缓存 content_hash 应命中"
        assert judge._cache_get("h_absent") is None, "未缓存 content_hash 应 miss"

        # ── 阶段 4: rescan_dedup name_hash 用真 cosine (🔴#1) ──────────
        # 两个同名、内容/embedding 都不同 (cos≈0) 的跨 source skill。
        ea, eb = _emb(dim, 0), _emb(dim, 1)
        lib.store.insert(_rec("d1", "dup-name", "srcA/repo",
                              "alpha content " * 20, "h_d1"), embedding=ea)
        lib.store.insert(_rec("d2", "dup-name", "srcB/repo",
                              "totally different " * 20, "h_d2"), embedding=eb)
        lib.dup_judge = None          # 无 LLM → 只有 cos>=auto_cos 才会自动合并
        lib.ingester.dup_judge = None
        pairs = _collect_candidates(lib, min_cos=0.0, top_k=5, limit=None)
        key = tuple(sorted(["d1", "d2"]))
        assert key in pairs, f"name_hash 对未被收集: {list(pairs)}"
        cos, trigger = pairs[key]
        real = cosine_sim(ea, eb)
        assert cos < 0.99, \
            f"name_hash 对 cos={cos} 应是真低余弦 (~{real:.3f}), 不是硬置 1.0 (🔴#1)"
        assert abs(cos - real) < 0.05, f"cos {cos} 应≈真 cosine {real}"

        # ── 阶段 5: license 白名单 ↔ 代码 GREEN set 一致 (🔴#4) ────────
        real_json = json.loads(
            (Path(export_mod.__file__).resolve().parent
             / "license_safe_sources.json").read_text(encoding="utf-8"))
        assert set(real_json["green_categories"]) == set(GREEN_LICENSES), \
            "license_safe_sources.json green_categories 与代码 GREEN set 不一致 (🔴#4)"
        assert "0BSD" in real_json["green_categories"], "0BSD 缺失 (🔴#4)"

        lib.close()


if __name__ == "__main__":
    test_iter3_upgrade_smoke()
    print("ITER3 UPGRADE SMOKE TEST PASSED")
