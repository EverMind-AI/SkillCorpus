"""Ingest pipeline orchestration — chains parse → safety → quality → dedup → classify → embed → store."""

from __future__ import annotations

import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from .classify import Classifier
from .classify import extract_tags
from ..core.hashing import content_hash, name_hash, short_hash
from .dedup import LLMDupJudge
from ..core.embed import EmbeddingClient, format_embedding_text
from ..core.models import SkillRecord
from .parse import ParseError, ValidationError, find_skill_md, parse_skill_file, validate_skill
from .quality import compute_quality
from .quality import LLMQualityJudge
from .safety import check_safety, is_blocked
from ..core.store import SkillStore
from ..core.fsstore import copy_skill_to_library, remove_skill_from_library

logger = logging.getLogger("skillcorpus.pipeline")


class IngestStatus(str, Enum):
    ADDED = "added"
    DUPLICATE = "duplicate"              # exact content_hash match, new skill discarded
    MERGED_KEPT_NEW = "merged_kept_new"  # LLM judged near-dup, new skill replaces old (old superseded)
    MERGED_KEPT_OLD = "merged_kept_old"  # LLM judged near-dup, old skill is better, new discarded
    REJECTED_SAFETY = "rejected_safety"
    REJECTED_QUALITY = "rejected_quality"
    REJECTED_PARSE = "rejected_parse"


@dataclass
class IngestResult:
    status: IngestStatus
    record: SkillRecord | None = None
    reason: str = ""
    skill_dir: str = ""


def _glob_skill_md(root: Path, pattern: str = "**/SKILL.md") -> list[Path]:
    """Glob skill files case-insensitively for SKILL.md / skill.md.

    find_skill_md() accepts a lowercase ``skill.md``, but pathlib glob is
    case-sensitive, so a batch scan on ``**/SKILL.md`` alone silently skipped
    lowercase-named skills. Glob the given pattern plus its lowercase-filename
    variant and dedup by path (distinct files on a case-sensitive FS; the same
    file never matches both patterns since glob's fnmatch is case-sensitive).
    """
    lower = pattern.replace("SKILL.md", "skill.md")
    patterns = (pattern,) if lower == pattern else (pattern, lower)
    seen: dict[str, Path] = {}
    for pat in patterns:
        for p in root.glob(pat):
            seen[str(p)] = p
    return list(seen.values())


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
    """Ingest pipeline."""

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
        self.classifier = classifier  # LLM classifier (None → everything OTHER)
        self.source_weights = source_weights
        self.thresholds = thresholds
        self.embedder = embedding_client
        self.concurrency = max(1, concurrency)
        self.dup_judge = dup_judge
        # dedup config (embedding near-dup trigger thresholds, etc.)
        d = dedup_cfg or {}
        self._dedup_enabled = bool(d.get("enable_near_dup", True))
        self._dedup_min_cos = float(d.get("near_dup_min_cosine", 0.90))
        self._dedup_auto_cos = float(d.get("near_dup_auto_cosine", 0.995))
        self._dedup_top_k = int(d.get("near_dup_top_k", 5))
        # quality judge: whether to compute the LLM score inline during ingest
        self.quality_judge = quality_judge
        q = quality_cfg or {}
        self._llm_quality_at_ingest = bool(q.get("llm_quality_at_ingest", True))
    # ------------------------------------------------------------------
    # dedup decision (shared helper, called by both serial + concurrent paths)
    # ------------------------------------------------------------------

    def _pick_winner(self, new_rec: SkillRecord, old_rec: SkillRecord) -> str:
        """Return 'new' or 'old' — which skill to keep. In order: quality → source → newer."""
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
        # tie → the one uploaded later wins (a latecomer may be a newer version)
        return "new" if (new_rec.added_at or "") > (old_rec.added_at or "") else "old"

    def _collect_near_dup_candidates(
        self, new_rec: SkillRecord, embedding: list[float] | None,
    ) -> list[tuple[SkillRecord, float, str]]:
        """Collect near-dup candidates. Return a list of [(old_rec, real_cosine, trigger)].

        trigger ∈ {'name_hash', 'name_hash_no_emb', 'embedding', 'both'}.

        The name_hash path computes the real cosine (numpy dot of the two
        embeddings), never a 1.0 placeholder: a 1.0 combined with auto_cos=0.995
        would short-circuit ``_judge_duplicate`` and auto-merge across sources
        (same name, different content). When an embedding is missing it falls
        back to ``_dedup_min_cos`` to force the LLM second-pass judgment instead
        of bypassing it.
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
        # 1) canonical name_hash collision (same name across sources) — compute real cos
        for r in self.store.get_by_name_hash(new_rec.name_hash):
            if r.source == new_rec.source:
                continue  # same source + same name is handled separately by the caller (overwrite)
            other_emb = self.store.get_embedding(r.skill_id)
            real_cos = _cos(embedding, other_emb)
            if real_cos is None:
                # embedding missing (new skill not embedded / old skill not stored) →
                # use min_cos as placeholder, _judge_duplicate will go through the LLM (no short-circuit)
                cands[r.skill_id] = (r, self._dedup_min_cos, "name_hash_no_emb")
            else:
                cands[r.skill_id] = (r, real_cos, "name_hash")

        # 2) embedding neighbors
        if (self._dedup_enabled and embedding is not None
                and self.embedder is not None):
            near = self.store.find_near_duplicates(
                # Exclude the row being (re)written itself: a same-source
                # same-name overwrite reuses the old skill_id, so without this
                # the old row self-matches at cos~=1.0 and triggers a
                # supersede(x, x) self-reference.
                embedding, exclude_skill_id=new_rec.skill_id,
                top_k=self._dedup_top_k,
                min_cosine=self._dedup_min_cos,
            )
            for rec, cos in near:
                if rec.skill_id in cands:
                    # already matched via name_hash; overwrite with the cos from
                    # the embedding path (the two should agree, take the latter =
                    # already normalized/computed by find_near_duplicates)
                    cands[rec.skill_id] = (rec, cos, "both")
                else:
                    cands[rec.skill_id] = (rec, cos, "embedding")
        return list(cands.values())

    def _judge_duplicate(
        self, new_rec: SkillRecord, old_rec: SkillRecord, cos: float,
    ) -> bool:
        """Decide whether new_rec and old_rec count as duplicates. cos >= auto_cos
        auto-marks as duplicate; otherwise call the LLM. If the LLM is unavailable,
        conservatively judge them as non-duplicate."""
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
        """Shared finalize: near-dup decision + file copy + store.insert.

        Assumes rec is already ready (except stored_path) when called, and that
        the upstream dedup for content_hash / same-source same-name has already
        been handled by the caller. This only handles:
          - cross-source same-name canonical
          - embedding near-dup
        """
        # persist source_url onto the DB row (not only the FS meta)
        if source_url and not rec.source_url:
            rec.source_url = source_url
        # near-dup decision — gated solely on `dedup.enable_near_dup`. (dup_judge is
        # built unconditionally when an LLM is available, so OR-ing it in here made
        # enable_near_dup=false silently still run the inline LLM judge — a broken
        # flag + a wedge risk on large skills. The judge is still used *within* this
        # path via _judge_duplicate, and standalone by dedup_pass.)
        did_supersede = False
        to_supersede: list = []
        if not force and self._dedup_enabled:
            cands = self._collect_near_dup_candidates(rec, embedding)
            cands.sort(key=lambda x: -x[1])  # cos descending
            # first filter out all candidates confirmed as duplicates (auto / LLM)
            confirmed = [
                (old, cos, trigger) for old, cos, trigger in cands
                if self._judge_duplicate(rec, old, cos)
            ]
            if confirmed:
                # pick the single winner among {rec} ∪ all confirmed dups (pairwise greedy max).
                # the winner MUST be decided before acting — otherwise, if rec loses
                # to some old skill but has already superseded other old skills, those
                # loser.superseded_by would point at a rec.skill_id that never gets
                # inserted (dangling reference).
                best, best_cos, best_trigger = rec, 0.0, ""
                for old, cos, trigger in confirmed:
                    if self._pick_winner(best, old) == "old":
                        best, best_cos, best_trigger = old, cos, trigger
                if best is not rec:
                    # rec is not the best → discard rec, merge into the existing best
                    return IngestResult(
                        status=IngestStatus.MERGED_KEPT_OLD,
                        record=best,
                        reason=f"near-dup ({best_trigger}, cos={best_cos:.3f}): "
                               f"kept {best.skill_id}",
                        skill_dir=str(skill_dir),
                    )
                # rec beats all confirmed dups → retire them, but only AFTER the
                # winner is durably inserted below, so a failed copy/insert cannot
                # destroy the losers and leave a dangling superseded_by.
                to_supersede = confirmed

        # not merged (or rec=winner has already superseded all old ones) → normal ingest
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

        # winner is durable → now retire the losers (deferred from above)
        for old, cos, trigger in to_supersede:
            if old.stored_path:
                remove_skill_from_library(self.lib_root, old.stored_path)
            self.store.supersede(old.skill_id, rec.skill_id)
            did_supersede = True
            logger.info(
                f"near-dup merge: {rec.skill_id} supersedes {old.skill_id} "
                f"(trigger={trigger}, cos={cos:.3f})"
            )

        # status is decided directly by whether this run superseded anything, not by a
        # superseded_by COUNT (which would misreport a merge when a skill_id that already
        # won a merge is ingested again).
        status = IngestStatus.MERGED_KEPT_NEW if did_supersede else IngestStatus.ADDED
        return IngestResult(status=status, record=rec, skill_dir=str(skill_dir))

    # ------------------------------------------------------------------

    def ingest(
        self, skill_dir: Path, source: str,
        source_url: str | None = None,
        source_path: str | None = None,
        force: bool = False,
    ) -> IngestResult:
        """Run the full pipeline on a single skill directory. Returns the record on success."""
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

        # Note: the GREEN-license gate is not at the ingest layer — store.insert()
        # sets active=0/1 by source (non-GREEN is kept but active=0), and export
        # then filters by active=1.
        # active is set by curate.license_audit; export filters WHERE active=1.

        # ------------------------------------------------------------
        # 3. Quality filter (structural rules)
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
        # 4a. exact content_hash duplicate — mark as DUPLICATE directly
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
        # 4b. same source + same canonical name → overwrite the old record (version update)
        # ------------------------------------------------------------
        same_name = self.store.get_by_name_hash(n_hash)
        same_name_same_src = [r for r in same_name if r.source == source]
        if same_name_same_src and not force:
            # Overwrite in place by reusing the id. Do NOT delete the old files
            # here: copy_skill_to_library rmtrees + rewrites the same
            # (source, slug) dir on a successful write, and deleting up front
            # left a dangling row if _finalize_and_insert then discarded rec
            # (near-dup loss) or raised.
            stable_id = same_name_same_src[0].skill_id
        else:
            stable_id = self._make_id(source, name, c_hash)

        # ------------------------------------------------------------
        # 5. Classify (LLM classifier) + tags
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
        # 6b. LLM quality (single-thread path: use score() directly, includes cache)
        # ------------------------------------------------------------
        llm_quality_norm: float | None = None
        if self.quality_judge is not None and self._llm_quality_at_ingest:
            probe_rec = SkillRecord(
                skill_id="", name=name, description=description,
                body=body, content_hash=c_hash,
            )
            try:
                j = self.quality_judge.score(probe_rec)
                if j is not None:
                    llm_quality_norm = j.normalized
            except Exception as e:
                logger.debug("quality judge failed at ingest: %s", e)

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
        # 7. Embed (needed before near-dup detection)
        # ------------------------------------------------------------
        embedding = None
        if self.embedder is not None and self.embedder.is_available():
            emb_text = format_embedding_text(name, description, body)
            embedding = self.embedder.embed(emb_text)

        # ------------------------------------------------------------
        # 8. Finalize (near-dup decision + file copy + store.insert)
        # ------------------------------------------------------------
        return self._finalize_and_insert(
            rec, skill_dir, embedding, source, source_url,
            source_path or str(skill_dir), force=force,
        )

    # ------------------------------------------------------------------
    # Batch
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Concurrent ingest (LLM + embed concurrent, store serial)
    # ------------------------------------------------------------------

    def _prepare(
        self, skill_dir: Path, source: str, source_path: str | None,
    ) -> tuple[IngestStatus, SkillRecord | None, str,
               list[float] | None, "object | None"]:
        """Do LLM classification + embedding + LLM quality in a concurrency-safe way,
        returning (status, prepared_record, reason, embedding, quality_judgment).

        quality_judgment is cache_put by the main thread in the write phase (to avoid
        cross-thread SQLite).
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

        # LLM classify (LLM call, the concurrency bottleneck)
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

        # LLM quality — concurrency-safe (compute_no_cache doesn't touch SQLite); main thread cache_put in the write phase.
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

        # Embed (concurrent)
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
        """Concurrent batch ingest — LLM/embed concurrent, store writes serial.

        ~N times faster than ingest_batch (N=concurrency); no benefit at small scale.

        Sub-skill suppression: when ``rglob`` finds two SKILL.md where one's
        parent directory is an ancestor of another's, only the **shallowest**
        one is ingested. The deeper SKILL.md becomes an auxiliary file inside
        the parent's bundle (carried along by ``copytree`` at copy time, but
        not registered as its own skill). This prevents promoting a nested
        SKILL.md to a top-level sibling skill.
        """
        root = Path(root)
        skill_md_paths = _glob_skill_md(root, pattern)
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

                # writes must be serial (dedup + SQLite + filesystem)
                if status == IngestStatus.ADDED and rec is not None:
                    # main thread cache_put for LLM quality (avoid cross-thread SQLite)
                    if qj is not None and self.quality_judge is not None:
                        try:
                            self.quality_judge.cache_put(rec.content_hash, qj)
                        except Exception as e:
                            logger.debug(f"quality cache_put failed: {e}")

                    existing = self.store.get_by_content_hash(rec.content_hash)
                    if existing is not None:
                        counts[IngestStatus.DUPLICATE.value] += 1
                        continue

                    # same-name collision (within the same source): overwrite the old record, keep stable_id
                    same_src = [
                        r for r in self.store.get_by_name_hash(rec.name_hash)
                        if r.source == source
                    ]
                    if same_src:
                        # Overwrite in place by reusing the id. Do NOT delete the
                        # old files here: copy_skill_to_library rmtrees + rewrites
                        # the same (source, slug) dir on a successful write, and
                        # deleting up front left a dangling row if
                        # _finalize_and_insert then discarded rec (near-dup loss)
                        # or raised.
                        rec.skill_id = same_src[0].skill_id

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
    # Legacy sequential (kept)
    # ------------------------------------------------------------------

    def ingest_batch(
        self, root: Path, source: str,
        pattern: str = "**/SKILL.md",
        stop_on_error: bool = False,
        limit: int | None = None,
        progress: bool = True,
        source_url: str | None = None,
    ) -> dict[str, Any]:
        """Scan all SKILL.md under root, running ingest on each containing directory.

        Note: kept serial (SQLite WAL + vLLM batching are efficient enough). For
        concurrency use ingest_batch_async.
        """
        root = Path(root)
        skill_md_paths = _glob_skill_md(root, pattern)
        # exclude workspaces / temporary skills (common in crawlers / intermediate artifacts)
        skill_md_paths = [p for p in skill_md_paths if "/workspaces/" not in str(p)]
        # sub-skill suppression — same rule as
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
        """Rough token count (1 token ≈ 4 chars)."""
        return max(1, len(body) // 4)


# ════════════ SkillLibrary top-level API ════════════
