import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "release_support", Path(__file__).parents[1] / "release_support.py"
)
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


@pytest.fixture
def prepared(tmp_path):
    nested = tmp_path / "python-root"
    nested.mkdir()
    for name in release.expected_assets("0.3.0"):
        (nested / name).write_bytes(name.encode())
    release.prepare_assets(tmp_path, "0.3.0")
    return tmp_path


def test_prepared_checksums_match_flat_downloads(prepared):
    release.verify_assets(prepared, "0.3.0")
    assert "./python-root/" not in (prepared / "SHA256SUMS").read_text()
    assert len((prepared / "SHA256SUMS").read_text().splitlines()) == 10


def test_tampered_download_fails(prepared):
    (prepared / "skillcorpus-0.3.0.tar.gz").write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="checksum mismatch"):
        release.verify_assets(prepared, "0.3.0")


def test_missing_download_fails(prepared):
    (prepared / "skillcorpus-0.3.0.tar.gz").unlink()
    with pytest.raises(ValueError, match="missing or extra"):
        release.verify_assets(prepared, "0.3.0")


def test_duplicate_basenames_fail_before_moving_files(tmp_path):
    for folder in ("one", "two"):
        (tmp_path / folder).mkdir()
        (tmp_path / folder / "same.whl").write_bytes(b"test")
    with pytest.raises(ValueError, match="duplicate"):
        release.prepare_assets(tmp_path, "0.3.0")
    assert (tmp_path / "one/same.whl").exists()


def test_checksum_paths_cannot_escape_download_directory(prepared):
    manifest = prepared / "SHA256SUMS"
    manifest.write_text("0" * 64 + "  ../outside\n")
    with pytest.raises(ValueError, match="flat filenames"):
        release.verify_assets(prepared, "0.3.0")


def test_release_notes_must_target_this_version(tmp_path):
    folder = tmp_path / "docs/releases"
    folder.mkdir(parents=True)
    notes = folder / "v0.3.0.md"
    text = (
        "## What’s Changed\n\nAn actual change.\n\n**Full Changelog**: "
        + release.REPO
        + "/compare/v0.1.0...v0.3.0\n"
    )
    notes.write_text(text)
    assert release.check_notes("v0.3.0", tmp_path) == "v0.1.0"
    notes.write_text(text.replace("...v0.3.0", "...v0.2.0"))
    with pytest.raises(ValueError, match="Full Changelog"):
        release.check_notes("v0.3.0", tmp_path)


def test_release_notes_reject_path_as_tag(tmp_path):
    with pytest.raises(ValueError, match="stable release tag"):
        release.check_notes("../../README", tmp_path)
