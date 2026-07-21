"""入库 pipeline 编排 — 串接 parse → safety → quality → dedup → classify → embed → store."""

from __future__ import annotations

import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from .metadata import Classifier
from .metadata import extract_tags
from .dedup import content_hash, name_hash, short_hash
from .dedup import LLMDupJudge
from .embed import EmbeddingClient, format_embedding_text
from .store import SkillRecord
from .rules import ParseError, ValidationError, find_skill_md, parse_skill_file, validate_skill
from .metadata import compute_quality
from .metadata import LLMQualityJudge
from .rules import check_safety, is_blocked
from .store import SkillStore, copy_skill_to_library, Category, remove_skill_from_library
from .llm import LLMClient
import yaml

logger = logging.getLogger("skill_library.pipeline")


class IngestStatus(str, Enum):
    ADDED = "added"
    DUPLICATE = "duplicate"              # content_hash 精确重复, 新 skill 丢弃
    MERGED_KEPT_NEW = "merged_kept_new"  # LLM 判近似重复, 新 skill 替换旧 (旧 supersede)
    MERGED_KEPT_OLD = "merged_kept_old"  # LLM 判近似重复, 旧 skill 更好, 新丢弃
    REJECTED_SAFETY = "rejected_safety"
    REJECTED_QUALITY = "rejected_quality"
    REJECTED_PARSE = "rejected_parse"


@dataclass
class IngestResult:
    status: IngestStatus
    record: SkillRecord | None = None
    reason: str = ""
    skill_dir: str = ""


def _drop_subskill_paths(skill_md_paths: list[Path]) -> list[Path]:
    """Filter out SKILL.md whose parent dir is nested inside another SKILL.md
    parent dir in the same batch.

    The shallowest SKILL.md wins; deeper SKILL.md become auxiliary files
    inside the parent's bundle.

    Algorithm: sort by parent depth ascending, then for each path walk its
    parent's ancestors and check membership against a set of already-kept
    parent path strings. O(N × depth) total — flat compared to the naive
    O(N²) startswith scan, important when batches grow past ~10K paths.
    """
    items = sorted(
        skill_md_paths, key=lambda p: (len(p.parts), str(p)),
    )
    kept: list[Path] = []
    kept_parents: set[str] = set()
    for p in items:
        parent = p.parent
        is_sub = False
        for ancestor in parent.parents:
            if str(ancestor) in kept_parents:
                is_sub = True
                break
        if is_sub:
            continue
        kept.append(p)
        kept_parents.add(str(parent))
    return kept


class Ingester:
    """入库流水线."""

    def __init__(
        self,
        store: SkillStore,
        lib_root: Path,
        source_weights: dict[str, float],
        thresholds: dict[str, Any],
        embedding_client: EmbeddingClient | None = None,
        classifier: Classifier | None = None,
        concurrency: int = 8,
        dup_judge: LLMDupJudge | None = None,
        dedup_cfg: dict[str, Any] | None = None,
        quality_judge: LLMQualityJudge | None = None,
        quality_cfg: dict[str, Any] | None = None,
    ):
        self.store = store
        self.lib_root = Path(lib_root)
        self.classifier = classifier  # LLM 分类器(None → 全部 OTHER)
        self.source_weights = source_weights
        self.thresholds = thresholds
        self.embedder = embedding_client
        self.concurrency = max(1, concurrency)
        self.dup_judge = dup_judge
        # 去重配置 (embedding 近似去重触发阈值等)
        d = dedup_cfg or {}
        self._dedup_enabled = bool(d.get("enable_near_dup", True))
        self._dedup_min_cos = float(d.get("near_dup_min_cosine", 0.90))
        self._dedup_auto_cos = float(d.get("near_dup_auto_cosine", 0.995))
        self._dedup_top_k = int(d.get("near_dup_top_k", 5))
        # 质量判 (Round B): ingest 阶段是否即时算 LLM 分
        self.quality_judge = quality_judge
        q = quality_cfg or {}
        self._llm_quality_at_ingest = bool(q.get("llm_quality_at_ingest", True))
    # ------------------------------------------------------------------
    # 去重决策 (共用 helper, serial + concurrent 都调)
    # ------------------------------------------------------------------

    def _pick_winner(self, new_rec: SkillRecord, old_rec: SkillRecord) -> str:
        """返回 'new' 或 'old' — 保留哪个 skill. 依次:quality → source → newer."""
        if new_rec.quality_score > old_rec.quality_score + 1e-6:
            return "new"
        if old_rec.quality_score > new_rec.quality_score + 1e-6:
            return "old"
        nw = self.source_weights.get(new_rec.source,
                                     self.source_weights.get("custom", 0.5))
        ow = self.source_weights.get(old_rec.source,
                                     self.source_weights.get("custom", 0.5))
        if nw > ow:
            return "new"
        if ow > nw:
            return "old"
        # tie → 新的上传时间晚的赢 (后来者可能是更新版)
        return "new" if (new_rec.added_at or "") > (old_rec.added_at or "") else "old"

    def _collect_near_dup_candidates(
        self, new_rec: SkillRecord, embedding: list[float] | None,
    ) -> list[tuple[SkillRecord, float, str]]:
        """收集近似重复候选. 返回 [(old_rec, real_cosine, trigger)] 列表.

        trigger ∈ {'name_hash', 'name_hash_no_emb', 'embedding', 'both'}.

        历史教训 (2026-05): cosine 曾经在 name_hash 路径用 1.0 占位, 配合
        auto_cos=0.995 短路 → ``_judge_duplicate`` 不过 LLM, 直接 auto-merge.
        实测产生 7,148 (68%) 跨 source name_hash supersede 是 false positive
        (真 cos<0.90 的不同内容被误合并). 现在改成显式算真 cos: 拉两个
        embedding numpy dot. embedding 缺失时退化到 ``_dedup_min_cos``
        以强制走 LLM 二判, 不再绕过.
        """
        import numpy as _np

        def _cos(a: list[float] | None, b: list[float] | None) -> float | None:
            if a is None or b is None:
                return None
            av = _np.asarray(a, dtype=_np.float32)
            bv = _np.asarray(b, dtype=_np.float32)
            na = float(_np.linalg.norm(av))
            nb = float(_np.linalg.norm(bv))
            if na < 1e-12 or nb < 1e-12:
                return None
            return float(_np.dot(av, bv) / (na * nb))

        cands: dict[str, tuple[SkillRecord, float, str]] = {}
        # 1) canonical name_hash 冲突 (跨 source 同名) — 算真 cos
        for r in self.store.get_by_name_hash(new_rec.name_hash):
            if r.source == new_rec.source:
                continue  # 同 source 同名由 caller 单独处理 (覆盖)
            other_emb = self.store.get_embedding(r.skill_id)
            real_cos = _cos(embedding, other_emb)
            if real_cos is None:
                # embedding 缺失 (新 skill 没 embed / 老 skill 没存) →
                # 用 min_cos 占位, _judge_duplicate 会走 LLM (不会短路)
                cands[r.skill_id] = (r, self._dedup_min_cos, "name_hash_no_emb")
            else:
                cands[r.skill_id] = (r, real_cos, "name_hash")

        # 2) embedding 近邻
        if (self._dedup_enabled and embedding is not None
                and self.embedder is not None):
            near = self.store.find_near_duplicates(
                embedding, exclude_skill_id=None,
                top_k=self._dedup_top_k,
                min_cosine=self._dedup_min_cos,
            )
            for rec, cos in near:
                if rec.skill_id in cands:
                    # 已由 name_hash 命中, 用 embedding 路径的 cos 覆盖
                    # (两者应该一致, 取后者 = 已经过 find_near_duplicates
                    # 的归一化/计算)
                    cands[rec.skill_id] = (rec, cos, "both")
                else:
                    cands[rec.skill_id] = (rec, cos, "embedding")
        return list(cands.values())

    def _judge_duplicate(
        self, new_rec: SkillRecord, old_rec: SkillRecord, cos: float,
    ) -> bool:
        """决定 new_rec 和 old_rec 是否算重复. cos >= auto_cos 自动判重;
        否则调 LLM. 若 LLM 不可用, 保守判非重复."""
        if cos >= self._dedup_auto_cos:
            return True
        if self.dup_judge is None:
            return False
        j = self.dup_judge.is_duplicate(new_rec, old_rec)
        return j.is_duplicate

    def _finalize_and_insert(
        self, rec: SkillRecord, skill_dir: Path,
        embedding: list[float] | None,
        source: str, source_url: str | None, source_path: str,
        force: bool = False,
    ) -> IngestResult:
        """共用 finalize: 近似去重决策 + 文件拷贝 + store.insert.

        假设调用时 rec 已经 ready (除 stored_path), 且 content_hash /
        同 source 同名的前置 dedup 已在 caller 处理. 这里只处理:
          - 跨 source 同名 canonical
          - embedding 近似
        """
        # source_url 落到 row (此前只进 FS meta, DB 列被漏掉)
        if source_url and not rec.source_url:
            rec.source_url = source_url
        # 近似去重决策 — 只在 dedup 启用 + 有 LLM judge 时触发
        did_supersede = False
        if not force and (self._dedup_enabled or self.dup_judge is not None):
            cands = self._collect_near_dup_candidates(rec, embedding)
            cands.sort(key=lambda x: -x[1])  # cos 降序
            # 先筛出所有被 (auto / LLM) 确认为重复的候选
            confirmed = [
                (old, cos, trigger) for old, cos, trigger in cands
                if self._judge_duplicate(rec, old, cos)
            ]
            if confirmed:
                # 在 {rec} ∪ 所有确认 dup 里选唯一 winner (pairwise greedy max).
                # 必须先定 winner 再动手 — 否则若 rec 输给某个老 skill, 却已经
                # supersede 了别的老 skill, 那些 loser.superseded_by 会指向一个
                # 根本不会入库的 rec.skill_id (悬挂引用).
                best, best_cos, best_trigger = rec, 0.0, ""
                for old, cos, trigger in confirmed:
                    if self._pick_winner(best, old) == "old":
                        best, best_cos, best_trigger = old, cos, trigger
                if best is not rec:
                    # rec 不是最优 → 丢弃 rec, 合并进已有的 best
                    return IngestResult(
                        status=IngestStatus.MERGED_KEPT_OLD,
                        record=best,
                        reason=f"near-dup ({best_trigger}, cos={best_cos:.3f}): "
                               f"kept {best.skill_id}",
                        skill_dir=str(skill_dir),
                    )
                # rec 胜过所有确认 dup → supersede 全部 (不再只处理第一对),
                # 否则同类的其余老 skill 会成为活着的孤儿重复
                for old, cos, trigger in confirmed:
                    if old.stored_path:
                        remove_skill_from_library(self.lib_root, old.stored_path)
                    self.store.supersede(old.skill_id, rec.skill_id)
                    did_supersede = True
                    logger.info(
                        f"near-dup merge: {rec.skill_id} supersedes {old.skill_id} "
                        f"(trigger={trigger}, cos={cos:.3f})"
                    )

        # 没被合并 (或 rec=winner 已 supersede 全部 old) → 正常入库
        stored_dir = copy_skill_to_library(
            skill_dir, self.lib_root, source, self._slug(rec.name),
            meta={
                "skill_id": rec.skill_id,
                "source": source,
                "source_url": source_url,
                "source_path": source_path,
                "content_hash": rec.content_hash,
                "added_at": rec.added_at,
            },
        )
        rec.stored_path = str(stored_dir.relative_to(self.lib_root))
        self.store.insert(rec, embedding=embedding)

        # status 直接由本次是否 supersede 决定 — 不再查 superseded_by COUNT
        # (那会把历史上赢过合并的 skill_id 再次入库时误报成 merge).
        status = IngestStatus.MERGED_KEPT_NEW if did_supersede else IngestStatus.ADDED
        return IngestResult(status=status, record=rec, skill_dir=str(skill_dir))

    # ------------------------------------------------------------------

    def ingest(
        self, skill_dir: Path, source: str,
        source_url: str | None = None,
        source_path: str | None = None,
        force: bool = False,
    ) -> IngestResult:
        """对单个 skill 目录跑完整 pipeline. 成功返回 record."""
        skill_dir = Path(skill_dir)
        md_path = find_skill_md(skill_dir)
        if md_path is None:
            return IngestResult(
                status=IngestStatus.REJECTED_PARSE,
                reason="SKILL.md not found",
                skill_dir=str(skill_dir),
            )

        # ------------------------------------------------------------
        # 1. Parse
        # ------------------------------------------------------------
        try:
            fm, body = parse_skill_file(md_path)
        except ParseError as e:
            return IngestResult(
                status=IngestStatus.REJECTED_PARSE,
                reason=f"parse error: {e}",
                skill_dir=str(skill_dir),
            )

        try:
            validate_skill(fm, body)
        except ValidationError as e:
            return IngestResult(
                status=IngestStatus.REJECTED_PARSE,
                reason=f"validation: {e}",
                skill_dir=str(skill_dir),
            )

        name = str(fm["name"]).strip()
        description = str(fm["description"]).strip()

        # ------------------------------------------------------------
        # 2. Safety
        # ------------------------------------------------------------
        full_text = f"{name}\n{description}\n{body}"
        safety_flags = check_safety(full_text)
        if is_blocked(safety_flags):
            return IngestResult(
                status=IngestStatus.REJECTED_SAFETY,
                reason=f"blocked: {safety_flags}",
                skill_dir=str(skill_dir),
            )

        # 注: GREEN-license 闸不在 ingest 层 —— 由 store.insert() 按 source 设
        # active=0/1 (非 GREEN 留库但 active=0), export 再按 active=1 过滤。
        # 见 store._load_safe_sources + export 的 WHERE active=1。

        # ------------------------------------------------------------
        # 3. Quality filter (Phase 1 规则)
        # ------------------------------------------------------------
        body_len = len(body)
        desc_len = len(description)
        t = self.thresholds
        if body_len < t.get("body_min_chars", 300):
            return IngestResult(
                status=IngestStatus.REJECTED_QUALITY,
                reason=f"body too short ({body_len} < {t['body_min_chars']})",
                skill_dir=str(skill_dir),
            )
        if body_len > t.get("body_max_chars", 50000):
            return IngestResult(
                status=IngestStatus.REJECTED_QUALITY,
                reason=f"body too long ({body_len} > {t['body_max_chars']})",
                skill_dir=str(skill_dir),
            )
        if desc_len < t.get("description_min_chars", 20):
            return IngestResult(
                status=IngestStatus.REJECTED_QUALITY,
                reason=f"description too short ({desc_len})",
                skill_dir=str(skill_dir),
            )
        desc_max = int(t.get("description_max_chars", 1024))
        if desc_len > desc_max:
            logger.warning(
                f"[{source}] description too long ({desc_len} > {desc_max}) "
                f"for skill '{name}' — kept as-is (agentskills.io spec)"
            )

        # ------------------------------------------------------------
        # 4a. 精确 content_hash 重复 — 直接判 DUPLICATE
        # ------------------------------------------------------------
        c_hash = content_hash(body)
        n_hash = name_hash(name)

        existing = self.store.get_by_content_hash(c_hash)
        if existing is not None and not force:
            return IngestResult(
                status=IngestStatus.DUPLICATE,
                record=existing,
                reason=f"content hash match: {existing.skill_id}",
                skill_dir=str(skill_dir),
            )

        # ------------------------------------------------------------
        # 4b. 同 source + 同 canonical name → 覆盖旧 record (版本更新)
        # ------------------------------------------------------------
        same_name = self.store.get_by_name_hash(n_hash)
        same_name_same_src = [r for r in same_name if r.source == source]
        if same_name_same_src and not force:
            old = same_name_same_src[0]
            if old.stored_path:
                remove_skill_from_library(self.lib_root, old.stored_path)
            stable_id = old.skill_id
        else:
            stable_id = self._make_id(source, name, c_hash)

        # ------------------------------------------------------------
        # 5. Classify (LLM 分类器) + tags
        # ------------------------------------------------------------
        if self.classifier is not None:
            category = self.classifier.classify(name, description, body).category
        else:
            category = "OTHER"  # no LLM available
        tags = extract_tags(name, description, fm)

        # ------------------------------------------------------------
        # 6. Structural features + quality
        # ------------------------------------------------------------
        has_scripts = (skill_dir / "scripts").exists() or any(
            p.suffix == ".py" for p in skill_dir.iterdir() if p.is_file()
        )
        has_references = (skill_dir / "references").exists() or any(
            p.suffix == ".md" and p.name.upper() != "SKILL.MD"
            for p in skill_dir.iterdir() if p.is_file()
        )

        # ------------------------------------------------------------
        # 6b. LLM quality (single-thread 路径: 直接用 score() 含 cache)
        # ------------------------------------------------------------
        llm_quality_norm: float | None = None
        if self.quality_judge is not None and self._llm_quality_at_ingest:
            probe_rec = SkillRecord(
                skill_id="", name=name, description=description,
                body=body, content_hash=c_hash,
            )
            j = self.quality_judge.score(probe_rec)
            if j is not None:
                llm_quality_norm = j.normalized

        q_score = compute_quality(
            source=source, source_weights=self.source_weights,
            body_len=body_len, desc_len=desc_len, frontmatter=fm,
            has_scripts=has_scripts, has_references=has_references,
            safety_flags=safety_flags, llm_score=llm_quality_norm,
        )

        rec = SkillRecord(
            skill_id=stable_id,
            name=name, description=description, body=body, frontmatter_raw=fm,
            source=source, source_url=source_url,
            source_path=source_path or str(skill_dir),
            license=self._extract_license(fm, skill_dir),
            content_hash=c_hash, name_hash=n_hash,
            category=category, tags=tags,
            quality_score=q_score, safety_flags=safety_flags,
            body_tokens=self._rough_tokens(body),
            has_scripts=has_scripts, has_references=has_references,
        )

        # ------------------------------------------------------------
        # 7. Embed (近似去重前就需要)
        # ------------------------------------------------------------
        embedding = None
        if self.embedder is not None and self.embedder.is_available():
            emb_text = format_embedding_text(name, description, body)
            embedding = self.embedder.embed(emb_text)

        # ------------------------------------------------------------
        # 8. Finalize (near-dup 判决 + 文件拷贝 + store.insert)
        # ------------------------------------------------------------
        return self._finalize_and_insert(
            rec, skill_dir, embedding, source, source_url,
            source_path or str(skill_dir), force=force,
        )

    # ------------------------------------------------------------------
    # Batch
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Concurrent ingest (LLM + embed 并发, store 串行)
    # ------------------------------------------------------------------

    def _prepare(
        self, skill_dir: Path, source: str, source_path: str | None,
    ) -> tuple[IngestStatus, SkillRecord | None, str,
               list[float] | None, "object | None"]:
        """在并发安全的前提下完成 LLM 分类 + embedding + LLM quality,
        返回 (status, prepared_record, reason, embedding, quality_judgment).

        quality_judgment 由主线程在 write phase cache_put (避免跨线程 SQLite).
        """
        md_path = find_skill_md(skill_dir)
        if md_path is None:
            return IngestStatus.REJECTED_PARSE, None, "SKILL.md not found", None, None

        try:
            fm, body = parse_skill_file(md_path)
        except ParseError as e:
            return IngestStatus.REJECTED_PARSE, None, f"parse: {e}", None, None
        try:
            validate_skill(fm, body)
        except ValidationError as e:
            return IngestStatus.REJECTED_PARSE, None, f"validation: {e}", None, None

        name = str(fm["name"]).strip()
        description = str(fm["description"]).strip()
        full_text = f"{name}\n{description}\n{body}"
        safety_flags = check_safety(full_text)
        if is_blocked(safety_flags):
            return IngestStatus.REJECTED_SAFETY, None, f"blocked: {safety_flags}", None, None

        t = self.thresholds
        body_len, desc_len = len(body), len(description)
        if body_len < t.get("body_min_chars", 300):
            return IngestStatus.REJECTED_QUALITY, None, f"body too short ({body_len})", None, None
        if body_len > t.get("body_max_chars", 50000):
            return IngestStatus.REJECTED_QUALITY, None, f"body too long ({body_len})", None, None
        if desc_len < t.get("description_min_chars", 20):
            return IngestStatus.REJECTED_QUALITY, None, f"desc too short ({desc_len})", None, None
        desc_max = int(t.get("description_max_chars", 1024))
        if desc_len > desc_max:
            logger.warning(
                f"[{source}] description too long ({desc_len} > {desc_max}) "
                f"for skill '{name}' — kept as-is"
            )

        c_hash = content_hash(body)
        n_hash = name_hash(name)

        # LLM classify (LLM 调用, 并发瓶颈)
        if self.classifier is not None:
            category = self.classifier.classify(name, description, body).category
        else:
            category = "OTHER"
        tags = extract_tags(name, description, fm)

        has_scripts = (skill_dir / "scripts").exists() or any(
            p.suffix == ".py" for p in skill_dir.iterdir() if p.is_file()
        )
        has_references = (skill_dir / "references").exists() or any(
            p.suffix == ".md" and p.name.upper() != "SKILL.MD"
            for p in skill_dir.iterdir() if p.is_file()
        )

        # LLM quality — 并发安全 (compute_no_cache 不碰 SQLite); 主线程 write phase cache_put.
        quality_judgment = None
        llm_q_norm: float | None = None
        if self.quality_judge is not None and self._llm_quality_at_ingest:
            probe = SkillRecord(
                skill_id="", name=name, description=description,
                body=body, content_hash=c_hash,
            )
            try:
                quality_judgment = self.quality_judge.compute_no_cache(probe)
                if quality_judgment is not None:
                    llm_q_norm = quality_judgment.normalized
            except Exception as e:
                logger.debug(f"quality judge exception for {name}: {e}")

        q_score = compute_quality(
            source=source, source_weights=self.source_weights,
            body_len=body_len, desc_len=desc_len, frontmatter=fm,
            has_scripts=has_scripts, has_references=has_references,
            safety_flags=safety_flags, llm_score=llm_q_norm,
        )

        # Embed (并发)
        embedding = None
        if self.embedder is not None and self.embedder.is_available():
            emb_text = format_embedding_text(name, description, body)
            embedding = self.embedder.embed(emb_text)

        stable_id = self._make_id(source, name, c_hash)
        rec = SkillRecord(
            skill_id=stable_id,
            name=name, description=description, body=body, frontmatter_raw=fm,
            source=source, source_path=source_path or str(skill_dir),
            license=self._extract_license(fm, skill_dir),
            content_hash=c_hash, name_hash=n_hash,
            category=category, tags=tags,
            quality_score=q_score, safety_flags=safety_flags,
            body_tokens=self._rough_tokens(body),
            has_scripts=has_scripts, has_references=has_references,
        )
        return IngestStatus.ADDED, rec, "", embedding, quality_judgment

    def ingest_batch_concurrent(
        self, root: Path, source: str,
        pattern: str = "**/SKILL.md",
        limit: int | None = None,
        progress: bool = True,
        source_url: str | None = None,
    ) -> dict[str, Any]:
        """并发版 batch ingest — LLM/embed 并发, store 写入串行.

        比 ingest_batch 快 ~N 倍 (N=concurrency), 小规模无收益.

        Sub-skill suppression (V4, 2026-05-08): when ``rglob`` finds two
        SKILL.md where one's parent directory is an ancestor of another's,
        only the **shallowest** one is ingested. The deeper SKILL.md
        becomes an auxiliary file inside the parent's bundle (carried
        along by ``copytree`` at copy time, but not registered as its own
        skill). This prevents the historical "promote nested SKILL.md to
        a top-level sibling skill" artifact.
        """
        root = Path(root)
        skill_md_paths = list(root.glob(pattern))
        skill_md_paths = [p for p in skill_md_paths if "/workspaces/" not in str(p)]
        # Filter sub-skill SKILL.md: keep one path P only if no other
        # path Q has P.parent strictly under Q.parent. Sort by depth
        # ascending so shallower wins.
        skill_md_paths = _drop_subskill_paths(skill_md_paths)
        if limit:
            skill_md_paths = skill_md_paths[:limit]

        counts: dict[str, int] = {s.value: 0 for s in IngestStatus}
        rejected_samples: list[tuple[str, str]] = []
        added_ids: list[str] = []
        total = len(skill_md_paths)
        if total == 0:
            return {"total": 0, **counts, "added_ids": [], "rejected_samples": []}

        logger.info(f"[{source}] concurrent ingest: {total} skills, workers={self.concurrency}")

        def _worker(md_path: Path):
            skill_dir = md_path.parent
            try:
                rel = str(skill_dir.relative_to(root))
            except ValueError:
                rel = str(skill_dir)
            try:
                status, rec, reason, emb, qj = self._prepare(skill_dir, source, rel)
                return skill_dir, rel, status, rec, reason, emb, qj
            except Exception as e:
                return skill_dir, rel, IngestStatus.REJECTED_PARSE, None, f"exception: {e}", None, None

        processed = 0
        with ThreadPoolExecutor(max_workers=self.concurrency) as pool:
            futures = [pool.submit(_worker, p) for p in skill_md_paths]
            for fut in as_completed(futures):
                skill_dir, rel, status, rec, reason, emb, qj = fut.result()
                processed += 1

                # 写入必须串行 (dedup + SQLite + filesystem)
                if status == IngestStatus.ADDED and rec is not None:
                    # 主线程 cache_put LLM quality (避免跨线程 SQLite)
                    if qj is not None and self.quality_judge is not None:
                        try:
                            self.quality_judge.cache_put(rec.content_hash, qj)
                        except Exception as e:
                            logger.debug(f"quality cache_put failed: {e}")

                    existing = self.store.get_by_content_hash(rec.content_hash)
                    if existing is not None:
                        counts[IngestStatus.DUPLICATE.value] += 1
                        continue

                    # 同名冲突 (同 source 内): 覆盖旧 record, keep stable_id
                    same_src = [
                        r for r in self.store.get_by_name_hash(rec.name_hash)
                        if r.source == source
                    ]
                    if same_src:
                        old = same_src[0]
                        if old.stored_path:
                            remove_skill_from_library(self.lib_root, old.stored_path)
                        rec.skill_id = old.skill_id

                    try:
                        result = self._finalize_and_insert(
                            rec, skill_dir, emb, source, source_url, rel, force=False,
                        )
                        counts[result.status.value] += 1
                        if result.status in (IngestStatus.ADDED,
                                             IngestStatus.MERGED_KEPT_NEW):
                            added_ids.append(rec.skill_id)
                    except Exception as e:
                        # Use the dedicated "errors" counter instead of pretending
                        # a write/system failure is a parse rejection.
                        counts["errors"] = counts.get("errors", 0) + 1
                        if len(rejected_samples) < 20:
                            rejected_samples.append((rel, f"store error: {e}"))
                else:
                    counts[status.value] += 1
                    if len(rejected_samples) < 20:
                        rejected_samples.append((rel, reason))

                if progress and total > 20 and processed % max(total // 20, 1) == 0:
                    pct = 100 * processed / total
                    logger.info(
                        f"  [{source}] {processed}/{total} ({pct:.0f}%) — "
                        f"added={counts['added']}, dup={counts['duplicate']}, "
                        f"rejected={counts['rejected_quality']+counts['rejected_parse']+counts['rejected_safety']}"
                    )

        return {
            "total": total,
            **counts,
            "added_ids": added_ids,
            "rejected_samples": rejected_samples,
        }

    # ------------------------------------------------------------------
    # Legacy sequential (保留)
    # ------------------------------------------------------------------

    def ingest_batch(
        self, root: Path, source: str,
        pattern: str = "**/SKILL.md",
        stop_on_error: bool = False,
        limit: int | None = None,
        progress: bool = True,
        source_url: str | None = None,
    ) -> dict[str, Any]:
        """扫描 root 下所有 SKILL.md, 对每个所在目录跑 ingest.

        注意: 保持串行 (SQLite WAL + vLLM 批量效率够). 要并发可用 ingest_batch_async.
        """
        root = Path(root)
        skill_md_paths = list(root.glob(pattern))
        # 排除 workspaces / 临时 skill (常见于爬虫/中间产物)
        skill_md_paths = [p for p in skill_md_paths if "/workspaces/" not in str(p)]
        # V4 (2026-05-08) sub-skill suppression — same rule as
        # ingest_batch_concurrent above; see _drop_subskill_paths.
        skill_md_paths = _drop_subskill_paths(skill_md_paths)
        if limit:
            skill_md_paths = skill_md_paths[:limit]

        counts: dict[str, int] = {s.value: 0 for s in IngestStatus}
        rejected_samples: list[tuple[str, str]] = []
        added_ids: list[str] = []

        total = len(skill_md_paths)
        for i, md_path in enumerate(skill_md_paths):
            skill_dir = md_path.parent
            try:
                rel = str(skill_dir.relative_to(root))
            except ValueError:
                rel = str(skill_dir)
            try:
                result = self.ingest(skill_dir, source=source, source_path=rel,
                                     source_url=source_url)
            except Exception as e:
                logger.exception(f"ingest crashed on {skill_dir}: {e}")
                if stop_on_error:
                    raise
                # Real system error — don't mis-bucket as REJECTED_PARSE.
                counts["errors"] = counts.get("errors", 0) + 1
                rejected_samples.append((rel, f"exception: {e}"))
                continue
            counts[result.status.value] += 1
            if result.status == IngestStatus.ADDED and result.record:
                added_ids.append(result.record.skill_id)
            elif result.status != IngestStatus.ADDED:
                if len(rejected_samples) < 20:
                    rejected_samples.append((rel, result.reason))

            if progress and total > 20 and (i + 1) % max(total // 20, 1) == 0:
                pct = 100 * (i + 1) / total
                logger.info(
                    f"  [{source}] {i+1}/{total} ({pct:.0f}%) — added={counts['added']}, "
                    f"dup={counts['duplicate']}, rejected={counts['rejected_quality']+counts['rejected_parse']+counts['rejected_safety']}"
                )

        return {
            "total": total,
            **counts,
            "added_ids": added_ids,
            "rejected_samples": rejected_samples,
        }

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _slug(name: str) -> str:
        s = re.sub(r"[^a-zA-Z0-9]+", "-", name).strip("-").lower()
        return s or "unnamed"

    def _make_id(self, source: str, name: str, c_hash: str) -> str:
        return f"{source}__{self._slug(name)}__{short_hash(c_hash)}"

    @staticmethod
    def _extract_license(fm: dict[str, Any], skill_dir: Path) -> str | None:
        lic = fm.get("license")
        if isinstance(lic, str) and lic.strip():
            return lic.strip()
        for name in ("LICENSE", "LICENSE.txt", "LICENSE.md"):
            p = skill_dir / name
            if p.exists():
                return (skill_dir / name).name
        return None

    @staticmethod
    def _rough_tokens(body: str) -> int:
        """粗略 token 数 (1 token ≈ 4 chars)."""
        return max(1, len(body) // 4)


# ════════════ SkillLibrary 顶层 API ════════════
_DEFAULT_CONFIG_NAME = "config.yaml"


_DEFAULT_LIB_ROOT = Path(__file__).resolve().parent / "data"


class SkillLibrary:
    """Skill 库顶层 API — CRUD + 入库 pipeline + 检索.

    默认路径: skill_library 包同级 data/ 子目录 (持久化, 跨会话保留).
    显式指定 lib_root 可切换到其他实例.
    """

    def __init__(
        self, lib_root: str | Path | None = None,
        config_path: str | Path | None = None,
    ):
        self.lib_root = Path(lib_root or _DEFAULT_LIB_ROOT).resolve()
        self.lib_root.mkdir(parents=True, exist_ok=True)
        self.config_path = Path(config_path) if config_path else self._default_config_path()
        self.config: dict[str, Any] = {}
        self.store: SkillStore | None = None
        self.classifier: Classifier | None = None
        self.embedder: EmbeddingClient | None = None
        self.ingester: Ingester | None = None

    def _default_config_path(self) -> Path:
        local = self.lib_root / "config.yaml"
        if local.exists():
            return local
        pkg_default = Path(__file__).parent / "config.yaml"
        return pkg_default

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def open(self) -> "SkillLibrary":
        """初始化 DB + 加载 config + 组件."""
        self.config = yaml.safe_load(self.config_path.read_text(encoding="utf-8")) or {}

        # --- Embedding ---
        embed_cfg = self.config.get("embedding", {})
        dim = int(embed_cfg.get("dim", 1536))
        self.embedder = EmbeddingClient(
            dim=dim,
            base_url=embed_cfg.get("base_url"),
            api_key=embed_cfg.get("api_key"),
            batch_size=int(embed_cfg.get("batch_size", 32)),
            timeout=int(embed_cfg.get("timeout", 60)),
        )

        # --- Storage (与 embedding dim 绑定) ---
        self.store = SkillStore(self.lib_root / "index.db", embedding_dim=dim)
        self.store.init_schema()

        # --- LLM client + LLM classifier ---
        llm_cfg_dict = self.config.get("llm", {}) or {}
        self.llm: LLMClient | None = None

        if llm_cfg_dict:
            # 单端点: 取 endpoints[0], 退回 llm 顶层 base_url/model/api_key
            eps = llm_cfg_dict.get("endpoints") or []
            ep0 = eps[0] if eps else {}
            self.llm = LLMClient(
                base_url=ep0.get("base_url") or llm_cfg_dict.get("base_url", "http://localhost:8211/v1"),
                model=ep0.get("model") or llm_cfg_dict.get("model", "qwen3"),
                api_key=ep0.get("api_key") or llm_cfg_dict.get("api_key", "dummy"),
                temperature=float(llm_cfg_dict.get("temperature", 0.1)),
                max_tokens=int(llm_cfg_dict.get("max_tokens", 512)),
                timeout=int(llm_cfg_dict.get("timeout", 60)),
                enable_thinking=bool(llm_cfg_dict.get("enable_thinking", False)),
            )
            if self.llm.is_available():
                self.classifier = Classifier(self.llm)
                logger.info("LLM classifier enabled (model=%s)", llm_cfg_dict.get("model"))
            else:
                logger.warning("LLM unavailable; ingest will set category=OTHER")

        # --- LLM dup judge (Round A — 跨 source 近似去重 LLM 仲裁) ---
        self.dup_judge: LLMDupJudge | None = None
        # --- LLM quality judge (Round B — 质量 0-10 打分) ---
        self.quality_judge: LLMQualityJudge | None = None
        if self.llm is not None and self.llm.is_available():
            try:
                self.dup_judge = LLMDupJudge(self.llm, self.store._connect())
                logger.info("LLM dup judge enabled")
            except Exception as e:
                logger.warning(f"LLM dup judge init failed: {e}")
            try:
                self.quality_judge = LLMQualityJudge(self.llm, self.store._connect())
                logger.info("LLM quality judge enabled")
            except Exception as e:
                logger.warning(f"LLM quality judge init failed: {e}")

        # --- Ingester ---
        concurrency = int(llm_cfg_dict.get("concurrency", 8)) if llm_cfg_dict else 8
        self.ingester = Ingester(
            store=self.store,
            lib_root=self.lib_root,
            source_weights=self.config.get("source_weights", {}),
            thresholds=self.config.get("thresholds", {}),
            embedding_client=self.embedder,
            classifier=self.classifier,
            concurrency=concurrency,
            dup_judge=self.dup_judge,
            dedup_cfg=self.config.get("dedup", {}),
            quality_judge=self.quality_judge,
            quality_cfg=self.config.get("quality", {}),
        )
        return self

    def close(self) -> None:
        if self.store is not None:
            self.store.close()

    def __enter__(self) -> "SkillLibrary":
        return self.open()

    def __exit__(self, *exc):
        self.close()

    # ------------------------------------------------------------------
    # Create
    # ------------------------------------------------------------------

    def add(
        self, skill_dir: str | Path, source: str,
        source_url: str | None = None, force: bool = False,
    ) -> IngestResult:
        assert self.ingester is not None, "call open() first"
        return self.ingester.ingest(
            Path(skill_dir), source=source, source_url=source_url, force=force,
        )

    def add_batch(
        self, root: str | Path, source: str,
        pattern: str = "**/SKILL.md",
        limit: int | None = None,
        concurrent: bool = True,
        source_url: str | None = None,
    ) -> dict[str, Any]:
        assert self.ingester is not None, "call open() first"
        if concurrent and self.ingester.concurrency > 1:
            return self.ingester.ingest_batch_concurrent(
                Path(root), source=source, pattern=pattern, limit=limit,
                source_url=source_url,
            )
        return self.ingester.ingest_batch(
            Path(root), source=source, pattern=pattern, limit=limit,
            source_url=source_url,
        )

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def get(self, skill_id: str) -> SkillRecord | None:
        assert self.store is not None
        return self.store.get(skill_id)

    def list(
        self,
        category: str | None = None,
        source: str | None = None,
        tag: str | None = None,
        min_quality: float = 0.0,
        limit: int = 100,
        offset: int = 0,
    ) -> list[SkillRecord]:
        assert self.store is not None
        return self.store.list(
            category=category, source=source, tag=tag,
            min_quality=min_quality, limit=limit, offset=offset,
        )

    # ------------------------------------------------------------------
    # Update
    # ------------------------------------------------------------------

    def update_metadata(self, skill_id: str, **fields: Any) -> SkillRecord | None:
        assert self.store is not None
        return self.store.update(skill_id, **fields)

    def retag(self, skill_id: str, tags: list[str]) -> SkillRecord | None:
        return self.update_metadata(skill_id, tags=tags)

    def reclassify(self, skill_id: str, category: str) -> SkillRecord | None:
        valid = {c.value for c in Category}
        if category not in valid:
            raise ValueError(f"invalid category '{category}'; must be one of {valid}")
        return self.update_metadata(skill_id, category=category)

    # ------------------------------------------------------------------
    # Delete
    # ------------------------------------------------------------------

    def delete(self, skill_id: str, soft: bool = True) -> bool:
        assert self.store is not None
        rec = self.store.get(skill_id)
        if not soft and rec is not None and rec.stored_path:
            remove_skill_from_library(self.lib_root, rec.stored_path)
        return self.store.delete(skill_id, soft=soft)

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Export
    # ------------------------------------------------------------------

    def export_bundle(
        self,
        out_path: str | Path,
        skill_ids: list[str] | None = None,
        category: str | None = None,
        source: str | None = None,
        tag: str | None = None,
        min_quality: float = 0.0,
        limit: int = 10_000,
    ) -> dict[str, Any]:
        """导出一个 skill 子集为 zip 包, 可跨机迁移.

        选择优先级:
            skill_ids 指定 → 直接按 id 拿;
            否则按 filter (category/source/tag/min_quality) 从 store.list 拿.

        Zip 结构:
            manifest.json         元信息 + skill 列表
            skills/<source>/<name_slug>/
                SKILL.md 等全部文件 (从 lib_root/skills/ 下复制)

        返回统计 dict: {out_path, count, size_bytes, ...}
        """
        import zipfile
        from datetime import datetime, timezone
        import json as _json

        assert self.store is not None
        out_path = Path(out_path).resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)

        # 选 skills
        if skill_ids:
            records = [r for r in (self.store.get(sid) for sid in skill_ids) if r is not None]
        else:
            records = self.store.list(
                category=category, source=source, tag=tag,
                min_quality=min_quality, limit=limit,
            )
        if not records:
            return {"out_path": str(out_path), "count": 0, "size_bytes": 0,
                    "reason": "no matching skills"}

        manifest_skills: list[dict[str, Any]] = []
        missing_files: list[str] = []
        with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
            for rec in records:
                manifest_skills.append({
                    "skill_id": rec.skill_id, "name": rec.name,
                    "source": rec.source,
                    "category": rec.category,
                    "tags": rec.tags,
                    "content_hash": rec.content_hash,
                    "quality_score": rec.quality_score,
                    "stored_path": rec.stored_path,
                    "description": rec.description[:200],
                })
                if not rec.stored_path:
                    missing_files.append(rec.skill_id)
                    continue
                src_dir = self.lib_root / rec.stored_path
                if not src_dir.exists() or not src_dir.is_dir():
                    missing_files.append(rec.skill_id)
                    continue
                # 走目录, 保持相对路径
                for f in src_dir.rglob("*"):
                    if not f.is_file():
                        continue
                    arcname = f"{rec.stored_path}/{f.relative_to(src_dir).as_posix()}"
                    zf.write(f, arcname)

            manifest = {
                "exported_at": datetime.now(timezone.utc).isoformat(),
                "lib_root": str(self.lib_root),
                "selection": {
                    "skill_ids": skill_ids, "category": category,
                    "source": source, "tag": tag, "min_quality": min_quality,
                },
                "count": len(manifest_skills),
                "missing_files": missing_files,
                "skills": manifest_skills,
            }
            zf.writestr("manifest.json",
                        _json.dumps(manifest, ensure_ascii=False, indent=2))

        return {
            "out_path": str(out_path),
            "count": len(manifest_skills),
            "size_bytes": out_path.stat().st_size,
            "missing_files": missing_files,
        }

    def stats(self) -> dict[str, Any]:
        assert self.store is not None
        s = self.store.stats()
        s["lib_root"] = str(self.lib_root)
        s["has_embedding"] = self.embedder.is_available() if self.embedder else False
        s["has_llm_classify"] = self.classifier is not None
        s["has_dup_judge"] = self.dup_judge is not None
        s["has_quality_judge"] = self.quality_judge is not None
        if self.dup_judge is not None:
            try:
                s["dedup_judgments"] = self.dup_judge.stats()
            except Exception as e:
                logger.debug("dup_judge.stats() unavailable: %s", e)
        if self.quality_judge is not None:
            try:
                s["quality_judgments"] = self.quality_judge.stats()
                s["quality_histogram"] = self.quality_judge.histogram()
            except Exception as e:
                logger.debug("quality_judge.stats() unavailable: %s", e)
        # superseded 计数 — Round A 近似去重的战果
        conn = self.store._connect()
        row = conn.execute(
            "SELECT COUNT(*) FROM skills WHERE superseded_by IS NOT NULL"
        ).fetchone()
        s["superseded_count"] = int(row[0]) if row else 0

        # description > 1024 的 skill 数 (Round C 告警指标)
        desc_max = int((self.config.get("thresholds") or {}).get("description_max_chars", 1024))
        row = conn.execute(
            "SELECT COUNT(*) FROM skills WHERE deleted = 0 AND LENGTH(description) > ?",
            (desc_max,),
        ).fetchone()
        s["overlong_description_count"] = int(row[0]) if row else 0
        return s
