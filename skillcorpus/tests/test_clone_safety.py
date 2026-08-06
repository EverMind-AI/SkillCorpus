"""Security regression: clone_or_pull must reject path-traversal repo names.

owner/repo arrive straight from remote-scraped discovery, so a malicious
sitemap / README / JSON catalog could yield ('..', '..') and, via the clone
retry path's shutil.rmtree(dst), delete a parent directory. The clone chokepoint
must reject such names before touching the filesystem.
"""

from __future__ import annotations

import skillcorpus.aggregate.clone as clone


def test_clone_or_pull_rejects_traversal(tmp_path, monkeypatch):
    cache = tmp_path / "cache"
    cache.mkdir()
    # a sibling of the cache that MUST survive a traversal attempt
    keep = tmp_path / "keepme"
    keep.mkdir()
    (keep / "important.txt").write_text("do not delete", encoding="utf-8")
    monkeypatch.setattr(clone, "FETCHED", cache)

    for owner, repo in [("..", ".."), ("..", ""), (".", "x"),
                        ("a/../..", "b"), ("", "repo"), ("o", "../../etc")]:
        dst, status = clone.clone_or_pull(owner, repo)
        assert dst is None, f"{owner}/{repo} should be rejected, got {dst}"
        assert status.startswith("fail"), status

    # nothing outside the cache was touched
    assert keep.exists() and (keep / "important.txt").exists()
    assert (keep / "important.txt").read_text(encoding="utf-8") == "do not delete"


def test_safe_segment():
    assert clone._safe_segment("anthropics")
    assert clone._safe_segment("some.repo-name_v2")
    assert not clone._safe_segment("..")
    assert not clone._safe_segment(".")
    assert not clone._safe_segment("")
    assert not clone._safe_segment("a/b")
    assert not clone._safe_segment("../x")


if __name__ == "__main__":
    import tempfile
    import pathlib

    class _MP:
        def setattr(self, o, n, v): setattr(o, n, v)

    with tempfile.TemporaryDirectory() as d:
        test_clone_or_pull_rejects_traversal(pathlib.Path(d), _MP())
    test_safe_segment()
    print("CLONE SAFETY TESTS PASSED")
