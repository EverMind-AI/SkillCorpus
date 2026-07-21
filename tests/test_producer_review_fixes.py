"""Regression tests for the full-codebase review round (R1–R10).

Covers the integration-boundary / stateful / silent-degradation classes that
unit tests historically missed — including failure conditions (short embed
batch, stale-dim blob) reproduced via small manual patches so they're permanent
guards, not inspection-only claims. Plain asserts + manual patching (no pytest),
matching the rest of the suite.
"""

from __future__ import annotations

import sqlite3
import struct
import tempfile
from pathlib import Path

from skill_library import SkillLibrary
from skill_library.store import SkillStore, SkillRecord
from skill_library.dedup import name_hash
from skill_library import embed as embed_mod
from skill_library import export as export_mod
from skill_library.export import _truthy_always, _valid_emb_blob, export

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
# R10 — is_always truthy tolerant of YAML string forms
# ----------------------------------------------------------------------
def test_r10_truthy_always():
    for v in (True, "true", "True", "TRUE", "yes", "on", "1", 1):
        assert _truthy_always(v) == 1, f"{v!r} should be truthy"
    for v in (False, "false", "no", "off", "0", 0, None, "", "maybe"):
        assert _truthy_always(v) == 0, f"{v!r} should be falsy"


# ----------------------------------------------------------------------
# R4 — _valid_emb_blob + export drops a stale-dim blob
# ----------------------------------------------------------------------
def test_r4_valid_emb_blob():
    assert _valid_emb_blob(b"\x00" * (DIM * 4), DIM) is True
    assert _valid_emb_blob(b"\x00" * ((DIM + 1) * 4), DIM) is False
    assert _valid_emb_blob(None, DIM) is False
    assert _valid_emb_blob(b"", DIM) is False


def test_r4_export_drops_dim_mismatch():
    with tempfile.TemporaryDirectory() as tmp:
        lib = SkillLibrary(Path(tmp) / "lib").open()
        lib.store.insert(_rec("good", "good-skill"))
        lib.store.insert(_rec("bad", "bad-skill"))
        c = lib.store._connect()
        c.execute("UPDATE skills SET active=1")
        c.commit()

        good_blob = struct.pack(f"{DIM}f", *_emb(0))                  # correct
        bad_blob = struct.pack(f"{DIM + 1}f", *([0.1] * (DIM + 1)))   # wrong dim
        orig = export_mod._load_embeddings_from_vec_skills
        export_mod._load_embeddings_from_vec_skills = (
            lambda data_dir: {"good": good_blob, "bad": bad_blob})
        try:
            mass = Path(tmp) / "mass.db"
            stats = export(lib.lib_root, mass, embedding_model="m", embedding_dim=DIM)
        finally:
            export_mod._load_embeddings_from_vec_skills = orig
            lib.close()

        assert stats.get("emb_dim_mismatch", 0) == 1, stats
        con = sqlite3.connect(mass)
        rows = {r[0]: (r[1], r[2]) for r in con.execute(
            "SELECT content_hash, embedding, embedding_model FROM skills")}
        con.close()
        assert rows["h_good"][0] is not None and rows["h_good"][1] == "m"
        assert rows["h_bad"][0] is None and rows["h_bad"][1] is None


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


# (store has no runtime vector_search — that's the consumer runtime's job; this
#  store's faiss path is dedup-only and is covered by find_near_duplicates tests.)


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


# ----------------------------------------------------------------------
# Fix B — _row_to_insert_tuple UPDATE pass doesn't double-count stats
# ----------------------------------------------------------------------
def test_fixb_update_pass_no_double_count():
    from collections import defaultdict
    from skill_library.export import _row_to_insert_tuple
    st = _store()
    st.insert(_rec("s", "n"))
    row = st._connect().execute("SELECT * FROM skills WHERE skill_id='s'").fetchone()
    stats = defaultdict(int)
    kw = dict(assets_dir=Path("/nonexistent"), embedding_model="m",
              embedding_dim=DIM, embedding_dtype="float32", stats=stats, now=0)
    _row_to_insert_tuple(row, None, count_stats=True, **kw)    # insert pass counts
    snap = dict(stats)
    _row_to_insert_tuple(row, None, count_stats=False, **kw)   # update pass: no count
    assert dict(stats) == snap, f"update pass double-counted: {dict(stats)} vs {snap}"
    assert snap.get("without_embedding") == 1 and snap.get("db_only") == 1


if __name__ == "__main__":
    test_r10_truthy_always()
    test_r4_valid_emb_blob()
    test_r4_export_drops_dim_mismatch()
    test_r2_short_batch_returns_none()
    test_r2_full_batch_ok()
    test_r1_rebuild_alignment()
    test_r7_reinsert_no_faiss_dup()
    test_r8_active_anticlobber()
    test_fixb_update_pass_no_double_count()
    print("ALL PRODUCER REVIEW-FIX TESTS PASSED")
