"""Bundle installation and the zip safety boundary.

The zip arrives from a remote catalog, so every limit here is a security
boundary rather than a robustness nicety: these tests pin what an attacker
cannot do with a crafted archive, and what a failed extraction may not
leave behind.
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest

from skillsearch.hub_client import SkillHubClient, SkillHubError


def zip_bytes(entries: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return buf.getvalue()


class FakeDownload(SkillHubClient):
    """A client whose download returns a fixed archive, with no network."""

    def __init__(self, payload: bytes, cache_dir: Path) -> None:
        super().__init__("https://catalog.invalid", cache_dir=cache_dir)
        self._payload = payload
        self.downloads = 0

    async def download(self, skill_id: str) -> bytes:
        self.downloads += 1
        return self._payload

    async def get(self, skill_id: str) -> dict:
        return {"slug": "demo", "version": "v1"}


async def test_install_extracts_the_bundle(tmp_path: Path) -> None:
    client = FakeDownload(zip_bytes({"SKILL.md": b"# demo\n"}), tmp_path)
    info = await client.install("demo-id")
    assert (Path(info["dir"]) / "SKILL.md").read_text() == "# demo\n"


async def test_a_failed_extraction_leaves_no_cache_entry(tmp_path: Path) -> None:
    """The point of staging: a rejected archive must not become a cache hit.

    The archive below is rejected partway through — one good entry, then a
    traversal attempt. Extracting straight into the destination would leave
    it existing but truncated, and every later install would read that as
    "already cached" and hand the agent half a skill.
    """
    payload = zip_bytes({"SKILL.md": b"# demo\n", "../escape.md": b"pwned"})
    client = FakeDownload(payload, tmp_path)

    with pytest.raises(SkillHubError):
        await client.install("demo-id")

    assert list(tmp_path.iterdir()) == [], "a failed install left residue behind"

    # And the failure repeats rather than being masked by a poisoned entry.
    with pytest.raises(SkillHubError):
        await client.install("demo-id")
    assert client.downloads == 2


async def test_traversal_never_writes_outside_the_destination(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    client = FakeDownload(zip_bytes({"../outside/stolen.md": b"pwned"}), tmp_path / "cache")

    with pytest.raises(SkillHubError):
        await client.install("demo-id")
    assert list(outside.iterdir()) == []


async def test_an_oversized_total_is_rejected(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("skillsearch.hub_client._MAX_ZIP_TOTAL_BYTES", 16)
    client = FakeDownload(zip_bytes({"SKILL.md": b"x" * 64}), tmp_path)

    with pytest.raises(SkillHubError):
        await client.install("demo-id")
    assert list(tmp_path.iterdir()) == []


async def test_a_disallowed_entry_is_skipped_not_fatal(tmp_path: Path) -> None:
    """One stray asset must not make an entire skill uninstallable."""
    payload = zip_bytes({"SKILL.md": b"# demo\n", "run.exe": b"MZ"})
    client = FakeDownload(payload, tmp_path)

    info = await client.install("demo-id")
    root = Path(info["dir"])
    assert (root / "SKILL.md").exists()
    assert not (root / "run.exe").exists()


async def test_a_second_install_reuses_the_cache(tmp_path: Path) -> None:
    client = FakeDownload(zip_bytes({"SKILL.md": b"# demo\n"}), tmp_path)
    await client.install("demo-id")
    await client.install("demo-id")
    assert client.downloads == 1
