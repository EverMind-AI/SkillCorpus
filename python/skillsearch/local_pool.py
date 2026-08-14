"""LocalPool — BM25 keyword retrieval over file-based skills.

The "local" pool covers everything that lives as a SKILL.md file on disk:

  - workspace/skills/ (user-authored)
  - packaged builtin/ (the 9 shipped skills)
  - workspace/skills/everos/ (EverOS-extracted, optional)

These pools are small (tens to a few hundred skills) and frequently edited,
so BM25 over the in-memory corpus is the right shape:
  - no embedding model loaded → starts in milliseconds
  - relevance is keyword-driven → user intent on a specific tool
    ("pdf" / "weather") matches better than dense semantic
  - re-tokenize per ``select`` is cheap at this scale

Index is built eagerly in ``__init__`` and refreshed via the public
``rebuild_index()``, which a host calls whenever its skills change on
disk — :meth:`SkillSearch.invalidate` is the supported way in. Steady-state ``search`` therefore
costs one query-side tokenize + one BM25 dot-product over precomputed
``doc_freqs``; the per-doc tokenize and IDF accumulation only run when
files actually changed.

BM25 + tokenization come from :mod:`skillsearch.bm25` (a self-contained
Okapi BM25, no ``rank_bm25`` / ``jieba`` / ``nltk`` dependency, with CJK-aware
tokenization) — shared with the agent tool catalog.
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING

from skillsearch.bm25 import BM25Okapi as _BM25Okapi
from skillsearch.bm25 import tokenize as _tokenize
from skillsearch.types import ScoredSkill, SkillMeta

if TYPE_CHECKING:
    from skillsearch.ports import SkillStore


def _format_skill_text(meta: SkillMeta, body_max: int = 4000) -> str:
    """One-line representation fed into the BM25 index. Heavier on signal
    fields (name, description) than body — ``"weather"`` should fire on
    the weather skill even when the body talks about HTTP and caching."""
    body = (meta.content or "")[:body_max]
    # Repeat name + description so they outweigh a long body in BM25 TF.
    return f"{meta.name} {meta.name} {meta.description or ''} {body}"


class LocalPool:
    """BM25 retrieval wrapper around a file-based ``SkillStore``.

    Holds a prebuilt ``_BM25Okapi`` over the current registry contents.
    A host calls :meth:`rebuild_index` when its skills change on disk,
    leaving ``search`` to a single query-side tokenize + BM25 dot
    product.

    Thread-safety: an internal :class:`threading.Lock` guards the
    ``(metas, _BM25Okapi)`` pair. ``rebuild_index`` does the expensive
    tokenize + BM25 construction *outside* the lock and only takes it
    for the atomic swap; ``search`` holds the lock only long enough
    to capture the two references, then scores + sorts outside.
    """

    def __init__(self, registry: SkillStore) -> None:
        self._registry = registry
        self._metas: list[SkillMeta] = []
        self._bm25: _BM25Okapi | None = None
        # Plain Lock (not RLock): no method re-enters another.
        self._lock = threading.Lock()
        # Eager initial build — matches the rest of the service which
        # pays disk-walk cost up front rather than at first user query.
        self.rebuild_index()

    def rebuild_index(self) -> None:
        """Re-read the registry and rebuild the BM25 index in place.

        Called once from ``__init__`` for the initial build, and again
        whenever the host reports its skills changed. Idempotent and
        safe to call concurrently — the
        last writer's index wins; in-flight searches retain their
        previously captured references and finish against a consistent
        snapshot.
        """
        metas = self._registry.list_all()
        if not metas:
            with self._lock:
                self._metas = []
                self._bm25 = None
            return
        tokenized_corpus = [_tokenize(_format_skill_text(m)) for m in metas]
        bm25 = _BM25Okapi(tokenized_corpus)
        # Defensive copy: registry's ``list_all`` hands out its cached
        # list by reference, and a future rebuild replaces (not mutates)
        # it — copying decouples us so the snapshot we serve to readers
        # cannot diverge from the BM25 we paired it with.
        metas_snapshot = list(metas)
        with self._lock:
            self._metas = metas_snapshot
            self._bm25 = bm25

    def search(self, query: str, top_k: int = 50) -> list[ScoredSkill]:
        """Return top-K matches by BM25 over the prebuilt index."""
        query_tokens = _tokenize(query)
        if not query_tokens:
            return []
        with self._lock:
            bm25 = self._bm25
            metas = self._metas
        if bm25 is None or not metas:
            return []
        scores = bm25.get_scores(query_tokens)
        # Drop zero-score docs and order by descending score, then take top_k.
        # Exclude ``m.always`` skills: a host that marks a skill
        # always-on already injects its body every turn, so ranking it
        # here would put the same text in the prompt twice.
        ranked = sorted(
            ((s, m) for s, m in zip(scores, metas, strict=True) if s > 0.0 and not m.always),
            key=lambda x: x[0],
            reverse=True,
        )[:top_k]
        return [ScoredSkill(name=m.name, score=float(s), source=m.source) for s, m in ranked]
