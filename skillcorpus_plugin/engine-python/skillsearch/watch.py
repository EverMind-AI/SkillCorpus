"""Notice when the shared skills directory changed, without a restart.

The local scan is built once and cached for the life of the engine, and
:meth:`SkillSearch.invalidate` — the supported way to drop it — is called by no
adapter at all. So on four of the five hosts a skill added today is invisible
until the agent restarts. That is fatal for a *shared* library, whose whole
premise is that a skill installed in one agent shows up in the others.

This is the fix, and it is deliberately narrow.

## Only the shared directory

A host now scans five or more directories rather than one, and walking all of
them every turn would put the cost on every deployment, including the ones not
using this feature. The shared directory is ours, its size is something we
control, and it is the only one whose contents change behind the host's back.

Everyone else's directories keep their host's existing behaviour: a user
editing a skill inside their own agent's directory still gets whatever that
agent already did, which for four of them means a restart. That is a real
limitation and it is the spec's call, not an oversight.

## Why a fingerprint rather than a revision file

A counter the plugin bumps when *it* installs something is cheaper — one
``stat`` — but it cannot see a user dragging a directory into the shared
folder by hand, which is one of the acceptance cases. So the fingerprint is
the mechanism and a revision file could only ever be a fast path in front of
it.

The walk is not pure overhead. Measured on WorkBuddy, whose per-turn hook has
done this since 0.2.0: 34ms to fingerprint 46 skills against 53ms to read and
parse them. The walk's cost is flat in corpus size and the parse it avoids is
not, so it pays off harder the bigger the library gets.
"""

from __future__ import annotations

import os
from collections.abc import Iterable
from pathlib import Path

#: Directory names never worth descending into. Mirrors the scanner's own skip
#: list — a fingerprint that tracks different files from the scan would either
#: miss changes or invent them.
SKIP_DIRS = frozenset({".git", "node_modules", "__pycache__", ".venv", "venv", ".tox"})

SKILL_FILE = "SKILL.md"


def fingerprint(dirs: Iterable[str | os.PathLike[str]], max_depth: int = 5) -> str:
    """Path and mtime of every ``SKILL.md`` under ``dirs``, in scan order.

    Sorted per directory level so two walks of an unchanged tree agree — a
    filesystem is free to hand back ``readdir`` entries in whatever order it
    likes, and an unsorted walk would invalidate the cache at random.

    Never raises: a directory that vanishes mid-walk contributes nothing, which
    is also the correct answer.
    """
    parts: list[str] = []
    for root in dirs:
        _collect(Path(root), max_depth, parts)
    return "\n".join(parts)


def _collect(directory: Path, depth: int, out: list[str]) -> None:
    if depth < 0:
        return
    try:
        entries = sorted(os.scandir(directory), key=lambda e: e.name)
    except OSError:
        return
    for entry in entries:
        if entry.name in SKIP_DIRS:
            continue
        try:
            if entry.is_dir(follow_symlinks=False):
                _collect(Path(entry.path), depth - 1, out)
            elif entry.name == SKILL_FILE:
                out.append(f"{entry.path}:{entry.stat().st_mtime_ns}")
        except OSError:
            # Deleted between scandir and stat. The next turn's walk settles it.
            continue


class DirectoryWatch:
    """Answers "did these directories change since I last asked?".

    Stateful on purpose: the first call establishes the baseline and reports
    no change, so constructing a watch does not throw away a scan that was
    just built.
    """

    def __init__(self, dirs: Iterable[str | os.PathLike[str]], max_depth: int = 5) -> None:
        self._dirs = [str(d) for d in dirs]
        self._max_depth = max_depth
        self._seen: str | None = None

    @property
    def active(self) -> bool:
        """Whether there is anything to watch. Cheap enough to call per turn."""
        return bool(self._dirs)

    def changed(self) -> bool:
        """Whether the tree differs from the last call. Never raises."""
        if not self._dirs:
            return False
        try:
            current = fingerprint(self._dirs, self._max_depth)
        except Exception:  # a watch is an optimisation, never a failure mode
            return False
        if self._seen is None:
            self._seen = current
            return False
        if current == self._seen:
            return False
        self._seen = current
        return True
