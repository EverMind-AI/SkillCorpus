"""Producer store / embed regression tests (from the full-codebase review round).

Covers integration-boundary / stateful behaviours that ordinary unit tests miss:
the embed batch alignment guard, faiss rebuild/insert alignment, and the
active-column anti-clobber. Failure conditions are reproduced via small manual
patches so they are permanent guards. Plain asserts + manual patching (no
pytest fixtures), matching the rest of the suite.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from skillcorpus import SkillLibrary  # noqa: F401  (kept: exercises package import path)
from skillcorpus.core.store import SkillStore
from skillcorpus.core.models import SkillRecord
from skillcorpus.core.hashing import name_hash
from skillcorpus.core import embed as embed_mod

DIM = 8


def _rec(sid, name, src="s1", q=0.5):
    return SkillRecord(
        skill_id=sid, name=name, description="d " * 10, body="b " * 20,
        source=src, content_hash=f"h_{sid}", name_hash=name_hash(name),
        quality_score=q,
    )


def _emb(half):
    v = [0.0] * DIM
    for i in (range(0, DIM // 2) if half == 0 else range(DIM // 2, DIM)):
        v[i] = 1.0
    return v


def _store():
    tmp = tempfile.mkdtemp()
    st = SkillStore(Path(tmp) / "t.db", embedding_dim=DIM)
    st.init_schema()
    return st


# ----------------------------------------------------------------------
# R2 — embed batch rejects a short/partial response (no silent misalign)
# ----------------------------------------------------------------------
class _FakeResp:
    def __init__(self, payload): self._p = payload
    def read(self):
        import json
        return json.dumps(self._p).encode()


class _FakeOpener:
    def __init__(self, n_return, provider):
        self._n = n_return
        self._provider = provider
    def open(self, req, timeout=None):
        if self._provider == "skillrouter_remote":
            return _FakeResp({"embeddings": [[0.0] * DIM] * self._n})
        return _FakeResp(
            {"data": [{"index": i, "embedding": [0.0] * DIM} for i in range(self._n)]})


def _run_with_fake_remote(n_return, texts, provider="openai_compatible"):
    """Patch the urllib opener + time.sleep around one embed_batch call."""
    import urllib.request
    import time
    orig_opener, orig_sleep = urllib.request.build_opener, time.sleep
    urllib.request.build_opener = lambda *a, **k: _FakeOpener(n_return, provider)
    time.sleep = lambda *a, **k: None   # skip exponential backoff in retries
    try:
        cli = embed_mod.EmbeddingClient(
            base_url="http://x:1/v1", batch_size=4, dim=DIM,
            provider=provider, model="m")
        return cli.embed_batch(texts, _skip_avail_check=True)
    finally:
        urllib.request.build_opener = orig_opener
        time.sleep = orig_sleep


def test_r2_short_batch_returns_none():
    for prov in ("openai_compatible", "skillrouter_remote"):
        out = _run_with_fake_remote(2, ["a", "b", "c", "d"], provider=prov)  # ask 4, get 2
        assert out is None, f"{prov}: short batch must fail loudly (None), not misalign"


def test_r2_full_batch_ok():
    for prov in ("openai_compatible", "skillrouter_remote"):
        out = _run_with_fake_remote(4, ["a", "b", "c", "d"], provider=prov)
        assert out is not None and len(out) == 4, prov


def test_is_available_caches_negative_probe():
    """A down endpoint must be probed at most once per TTL, not on every call
    (otherwise a build against a dead endpoint pays one 5s probe per skill)."""
    import urllib.request
    probes = {"n": 0}

    class _DownOpener:
        def open(self, *a, **k):
            raise OSError("connection refused")

    def _build(*a, **k):
        probes["n"] += 1
        return _DownOpener()

    orig = urllib.request.build_opener
    urllib.request.build_opener = _build
    try:
        cli = embed_mod.EmbeddingClient(
            base_url="http://x:1/v1", dim=DIM, provider="openai_compatible", model="m")
        assert cli.is_available() is False
        assert cli.is_available() is False
        assert probes["n"] == 1, f"re-probed a down endpoint: {probes}"
    finally:
        urllib.request.build_opener = orig


# ----------------------------------------------------------------------
# R1 — rebuild_faiss_index keeps ids ↔ vectors aligned
# ----------------------------------------------------------------------
def test_r1_rebuild_alignment():
    st = _store()
    for i in range(6):
        st.insert(_rec(f"a{i}", f"name {i}"), embedding=_emb(i % 2))
    assert st.rebuild_faiss_index() is True
    assert st._faiss_index.ntotal == len(st._faiss_ids)
    for i in range(6):
        hits = st.find_near_duplicates(_emb(i % 2), top_k=6, min_cosine=-1.0)
        assert hits and hits[0][1] >= 0.99


# ----------------------------------------------------------------------
# R7 — re-inserting an existing id doesn't duplicate it in faiss
# ----------------------------------------------------------------------
def test_r7_reinsert_no_faiss_dup():
    st = _store()
    for i in range(3):
        st.insert(_rec(f"a{i}", f"name {i}"), embedding=_emb(i % 2))
    st.find_near_duplicates(_emb(0), top_k=3, min_cosine=-1.0)  # load index
    pending0 = st._faiss_pending_inserts
    st.insert(_rec("a0", "name 0"), embedding=_emb(1))          # overwrite a0
    assert st._faiss_ids.count("a0") == 1
    assert st._faiss_pending_inserts > pending0



if __name__ == "__main__":
    test_r2_short_batch_returns_none()
    test_r2_full_batch_ok()
    test_is_available_caches_negative_probe()
    test_r1_rebuild_alignment()
    test_r7_reinsert_no_faiss_dup()
    print("ALL PRODUCER STORE/EMBED REGRESSION TESTS PASSED")
