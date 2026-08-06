"""Security regression: copy_skill_to_library must not dereference symlinks, or a
scraped repo could pull build-machine files into the published attachments."""
from __future__ import annotations

from skillcorpus.core.fsstore import copy_skill_to_library


def test_copy_skips_symlinks(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "SKILL.md").write_text("# demo\n", encoding="utf-8")
    secret = tmp_path / "secret.txt"
    secret.write_text("BUILD MACHINE SECRET", encoding="utf-8")
    (src / "leak").symlink_to(secret)                 # symlink to a build-machine file
    subdir = src / "scripts"
    subdir.mkdir()
    (subdir / "innerleak").symlink_to(secret)         # symlink nested in a copied dir

    dst = copy_skill_to_library(src, tmp_path / "lib", "somesrc", "demo")

    assert (dst / "SKILL.md").exists()
    assert not (dst / "leak").exists(), "top-level symlink must be skipped"
    inner = dst / "scripts" / "innerleak"
    # nested symlink is preserved AS a symlink (not dereferenced) — no file content copied
    assert not (inner.is_file() and not inner.is_symlink()), "nested symlink must not be dereferenced"


if __name__ == "__main__":
    import tempfile, pathlib
    with tempfile.TemporaryDirectory() as d:
        test_copy_skips_symlinks(pathlib.Path(d))
    print("FSSTORE SAFETY TEST PASSED")
