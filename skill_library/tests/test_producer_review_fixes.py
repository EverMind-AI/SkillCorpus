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

from skill_library import SkillLibrary  # noqa: F401  (kept: exercises package import path)
from skill_library.core.store import SkillStore
from skill_library.core.models import SkillRecord
from skill_library.core.hashing import name_hash
from skill_library.core import embed as embed_mod

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
    def __init__(self, n_return): self._n = n_return
    def open(self, req, timeout=None):
        return _FakeResp({"embeddings": [[0.0] * DIM] * self._n})


def _run_with_fake_remote(n_return, texts):
    """Patch the urllib opener + time.sleep around one embed_batch call."""
    import urllib.request
    import time
    orig_opener, orig_sleep = urllib.request.build_opener, time.sleep
    urllib.request.build_opener = lambda *a, **k: _FakeOpener(n_return)
    time.sleep = lambda *a, **k: None   # skip exponential backoff in retries
    try:
        cli = embed_mod.EmbeddingClient(
            base_url="http://x:1", batch_size=4, dim=DIM)
        return cli.embed_batch(texts, _skip_avail_check=True)
    finally:
        urllib.request.build_opener = orig_opener
        time.sleep = orig_sleep


def test_r2_short_batch_returns_none():
    out = _run_with_fake_remote(2, ["a", "b", "c", "d"])  # ask 4, get 2
    assert out is None, "short batch must fail loudly (None), not misalign"


def test_r2_full_batch_ok():
    out = _run_with_fake_remote(4, ["a", "b", "c", "d"])
    assert out is not None and len(out) == 4


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


# ----------------------------------------------------------------------
# R8 — insert never downgrades an operator-set active=1
# ----------------------------------------------------------------------
def test_r8_active_anticlobber():
    st = _store()
    st.insert(_rec("ng", "x", src="redsrc/repo"), embedding=_emb(0))  # non-green
    c = st._connect()
    assert c.execute(
        "SELECT active FROM skills WHERE skill_id='ng'").fetchone()["active"] == 0
    c.execute("UPDATE skills SET active=1 WHERE skill_id='ng'")        # hand-activate
    c.commit()
    st.insert(_rec("ng", "x", src="redsrc/repo"), embedding=_emb(0))  # re-ingest
    after = st._connect().execute(
        "SELECT active FROM skills WHERE skill_id='ng'").fetchone()["active"]
    assert after == 1, "re-insert must not clobber operator-set active=1"


if __name__ == "__main__":
    test_r2_short_batch_returns_none()
    test_r2_full_batch_ok()
    test_r1_rebuild_alignment()
    test_r7_reinsert_no_faiss_dup()
    test_r8_active_anticlobber()
    print("ALL PRODUCER STORE/EMBED REGRESSION TESTS PASSED")
