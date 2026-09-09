"""Where an installed skill came from, recorded beside the skill itself.

Installing into the shared directory — rather than into a cache excluded from
scanning — creates a problem the cache exclusion existed to avoid: the skill is
now both a local hit (it is on disk, and the scanner sees it) and a remote hit
(the catalogue still returns it), so one skill takes two slots in the ranking.

Neither existing defence catches that:

- fusion collapses on ``qualified_id``, and ``local/pdf-tables`` and
  ``hub/pdf-tables`` are different ids;
- ``_dedup_exact_bodies`` compares a SHA-256 of the body, so a trailing newline
  or a bumped version number is enough to miss.

The fix is identity rather than coincidence. An install writes a marker inside
the skill's own directory saying what it is and where it came from; the scanner
reads that marker and carries the identity on the hit; fusion collapses on it.
A skill installed from ``hub`` and the same skill offered by ``hub`` are then
one thing by construction, whatever their bytes happen to be.

The marker doubles as the ledger. Installing puts files on a user's disk, so
they must be able to see what is there and remove it — and a single file per
skill, inside the skill, cannot drift out of sync with the directory the way a
central index can. ``list_installed`` is a directory walk, not a database read.

Everything here fails open. A marker that cannot be read leaves the skill
looking hand-written, which costs deduplication for that one skill; it must
never cost a turn.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import shutil
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

#: Inside the skill's own directory. Dotted so a host's own scanner ignores it,
#: and named for this plugin so its owner is obvious to someone browsing.
MARKER = ".skillsearch-origin.json"

_NOTE = "Written by the skillsearch plugin. Delete the directory to uninstall."


@dataclass(frozen=True)
class Origin:
    """What an installed skill is, and where it came from."""

    origin: str
    """``<source>/<slug>``. The identity fusion collapses on."""

    source: str
    slug: str
    version: str = ""
    sha256: str = ""
    installed_at: str = ""

    def as_json(self) -> dict[str, object]:
        return {
            "_note": _NOTE,
            "version": 1,
            "origin": self.origin,
            "source": self.source,
            "slug": self.slug,
            "skill_version": self.version,
            "sha256": self.sha256,
            "installed_at": self.installed_at,
        }


def identity(source: str, slug: str) -> str:
    """The cross-source identity of one skill.

    Deliberately not the ``qualified_id``: that is ``<source>/<native id>``
    where source is the *retrieval* source, so the same skill reached two ways
    has two of them. This is what the skill *is*.
    """
    return f"{str(source).strip()}/{str(slug).strip()}"


def body_digest(body: str) -> str:
    """SHA-256 of a skill body, for the ledger.

    Recorded so an update can say what changed and a user can tell a modified
    skill from an untouched one. Not used for deduplication — that is what the
    identity above is for, precisely because a digest misses a bumped version.
    """
    return hashlib.sha256((body or "").encode("utf-8")).hexdigest()


def write_marker(skill_dir: str | os.PathLike[str], origin: Origin) -> bool:
    """Record provenance inside an installed skill. ``False`` on failure.

    Written atomically for the same reason the bundle itself is: a reader
    walking the shared directory must never see half a marker.
    """
    target = Path(skill_dir) / MARKER
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        handle, tmp = tempfile.mkstemp(dir=str(target.parent), prefix=".origin-", suffix=".json")
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as fh:
                json.dump(origin.as_json(), fh, indent=2, ensure_ascii=False)
                fh.write("\n")
            os.replace(tmp, target)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(tmp)
            raise
    except (OSError, ValueError):
        return False
    return True


def read_marker(skill_dir: str | os.PathLike[str]) -> Origin | None:
    """Provenance for one skill, or ``None`` when it was not installed here.

    ``None`` is the ordinary answer, not an error: a hand-written skill has no
    marker and must keep working exactly as it did.
    """
    try:
        raw = json.loads((Path(skill_dir) / MARKER).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict):
        return None
    source = str(raw.get("source") or "").strip()
    slug = str(raw.get("slug") or "").strip()
    if not source or not slug:
        return None
    return Origin(
        origin=str(raw.get("origin") or identity(source, slug)),
        source=source,
        slug=slug,
        version=str(raw.get("skill_version") or ""),
        sha256=str(raw.get("sha256") or ""),
        installed_at=str(raw.get("installed_at") or ""),
    )


def now() -> str:
    """An install timestamp, UTC and second-resolution."""
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def slug_dir(root: str | os.PathLike[str], source: str, slug: str) -> Path:
    """Where a skill from ``source`` lands under ``root``.

    One directory per identity, not per version: an update replaces what is
    there rather than accumulating copies, which is what keeps the shared
    directory from growing a second ranked copy of everything.

    The name is sanitised because a slug comes from a catalogue and reaches
    the filesystem — anything outside the allow-list becomes ``_``, so a slug
    of ``../../etc`` cannot escape ``root``.
    """
    safe_source = "".join(c if c.isalnum() or c in "-_" else "_" for c in str(source))[:40]
    safe_slug = "".join(c if c.isalnum() or c in "-_.@" else "_" for c in str(slug))[:120]
    return Path(root) / f"{safe_source}__{safe_slug or 'skill'}"


def _entries(root: str | os.PathLike[str]) -> list[tuple[Path, Origin]]:
    """Every install under ``root``, as ``(top-level directory, marker)``.

    The two paths are not always the same directory, which is what makes this
    more than an ``iterdir``. Catalogue bundles usually wrap the whole skill in
    one directory, so the ``SKILL.md`` — and therefore the marker, which lives
    beside it because that is what the scanner reads — sits one level below the
    directory the install created. Uninstalling has to remove the outer one, or
    an empty husk stays behind.

    Only one level down. A marker deeper than that is not something this code
    wrote, and treating an arbitrary depth as an install would let a skill
    bundled inside another skill be uninstalled out from under it.
    """
    out: list[tuple[Path, Origin]] = []
    try:
        entries = sorted(Path(root).iterdir(), key=lambda p: p.name)
    except OSError:
        return out
    for entry in entries:
        if not entry.is_dir():
            continue
        marker = read_marker(entry)
        if marker is not None:
            out.append((entry, marker))
            continue
        try:
            nested = sorted(entry.iterdir(), key=lambda p: p.name)
        except OSError:
            continue
        for child in nested:
            if not child.is_dir():
                continue
            marker = read_marker(child)
            if marker is not None:
                out.append((entry, marker))
                break
    return out


def list_installed(root: str | os.PathLike[str]) -> list[Origin]:
    """Every skill this plugin installed under ``root``, sorted by identity.

    A walk rather than an index read: the directory is the truth, so a skill a
    user deleted by hand is simply gone rather than a stale row nobody can
    explain.
    """
    return sorted((marker for _, marker in _entries(root)), key=lambda o: o.origin)


def find_installed(root: str | os.PathLike[str], origin: str) -> Path | None:
    """The directory to remove for an installed skill, by identity.

    The directory the install *created*, not the one holding the ``SKILL.md``
    — see `_entries`.
    """
    for directory, marker in _entries(root):
        if marker.origin == origin:
            return directory
    return None


def swap_into_place(staging: str | os.PathLike[str], dest: str | os.PathLike[str]) -> None:
    """Move a finished install over whatever is at ``dest``.

    An install must never leave the user worse off than before it started, so
    an update is "build the new one, switch, then delete the old" rather than
    "delete the old, then build". A failure at any point leaves the previous
    version in place and working.

    ``rename`` alone will not do it: on POSIX renaming onto a non-empty
    directory fails, and on Windows it fails onto any existing one. So the old
    copy is moved aside first, and moved back if the switch does not complete.

    @raises OSError - the install did not happen; ``dest`` is untouched.
    """
    staging_path, dest_path = Path(staging), Path(dest)
    if not dest_path.exists():
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        os.replace(staging_path, dest_path)
        return

    retired = dest_path.with_name(f"{dest_path.name}.retiring-{os.getpid()}-{os.urandom(4).hex()}")
    os.replace(dest_path, retired)
    try:
        os.replace(staging_path, dest_path)
    except BaseException:
        # Put the working copy back before letting the failure out.
        with contextlib.suppress(OSError):
            os.replace(retired, dest_path)
        raise
    else:
        shutil.rmtree(retired, ignore_errors=True)


def uninstall(root: str | os.PathLike[str], origin: str) -> bool:
    """Remove an installed skill by identity. ``False`` if it was not there.

    Moved aside and then deleted, so a half-finished delete cannot leave a
    directory the scanner still reads as a skill.
    """
    found = find_installed(root, origin)
    if found is None:
        return False
    retired = found.with_name(f"{found.name}.removing-{os.getpid()}-{os.urandom(4).hex()}")
    try:
        os.replace(found, retired)
    except OSError:
        return False
    shutil.rmtree(retired, ignore_errors=True)
    return True
