"""A SkillStore for hosts that don't already have one.

Walks a directory tree for ``SKILL.md`` files and parses their YAML
frontmatter. Hosts that already scan a skills directory should pass their
own object instead — this exists so a host without one still gets local
retrieval, not to compete with a scanner that already works.

Results are cached until :meth:`invalidate` is called. A host that watches
the filesystem should call it on change; one that doesn't gets a snapshot
taken at first use, which is the right trade for a directory of a few
hundred short files.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

_SKILL_FILE = "SKILL.md"
_SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv"}


@dataclass
class FileSkill:
    """One ``SKILL.md`` on disk, in the shape ``SkillStore`` promises."""

    name: str
    description: str
    content: str
    source: str
    path: Path
    always: bool = False


def _parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Split ``---`` YAML frontmatter from the body.

    Deliberately not a YAML parser: skill frontmatter in the wild is flat
    ``key: value``, and taking a YAML dependency for that would push the
    cost onto every host embedding this package.
    """
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text
    head, body = text[3:end], text[end + 4 :]
    meta: dict[str, str] = {}
    for line in head.splitlines():
        key, sep, value = line.partition(":")
        if sep and not key.startswith((" ", "\t", "#")):
            meta[key.strip()] = value.strip().strip("\"'")
    return meta, body.lstrip("\n")


class DirectorySkillStore:
    """Scans one or more roots for ``SKILL.md`` files."""

    def __init__(
        self,
        roots: list[tuple[Path, str]],
        *,
        max_depth: int = 5,
    ) -> None:
        self._roots = [(Path(r), name) for r, name in roots]
        self._max_depth = max_depth
        self._cache: list[FileSkill] | None = None

    def invalidate(self) -> None:
        self._cache = None

    def list_all(self) -> list[FileSkill]:
        if self._cache is None:
            self._cache = self._scan()
        return self._cache

    def get(self, name: str, source: str | None = None) -> FileSkill | None:
        for skill in self.list_all():
            if skill.name == name and (source is None or skill.source == source):
                return skill
        return None

    def _scan(self) -> list[FileSkill]:
        """Scan every root in order, the first occurrence of a name winning.

        Roots arrive in precedence order — the user's directory, then any
        bundled one, then extras — so shadowing is what the caller asked
        for: a user's ``pdf-forms`` replaces a bundled ``pdf-forms``
        rather than competing with it for the same rank.

        The collapse key is the name alone. Keying it by ``(source,
        name)`` would make the two copies collide only within one root,
        which is precisely where a collision cannot happen, and let both
        into the index everywhere it can.
        """
        found: list[FileSkill] = []
        seen: set[str] = set()
        for root, source in self._roots:
            if not root.is_dir():
                continue
            for path in self._walk(root):
                try:
                    text = path.read_text(encoding="utf-8")
                except OSError as e:
                    log.warning("skillsearch: cannot read %s: %s", path, e)
                    continue
                meta, body = _parse_frontmatter(text)
                name = meta.get("name") or path.parent.name
                if name in seen:
                    log.debug(
                        "skillsearch: %s in %s is shadowed by an earlier root",
                        name,
                        source,
                    )
                    continue
                seen.add(name)
                found.append(
                    FileSkill(
                        name=name,
                        description=meta.get("description", ""),
                        content=body,
                        source=source,
                        path=path,
                        always=str(meta.get("always", "")).lower() in {"1", "true", "yes"},
                    ),
                )
        return found

    def _walk(self, root: Path):
        stack = [(root, 0)]
        while stack:
            current, depth = stack.pop()
            if depth > self._max_depth:
                continue
            try:
                entries = list(current.iterdir())
            except OSError:
                continue
            for entry in entries:
                if entry.is_dir():
                    if entry.name not in _SKIP_DIRS:
                        stack.append((entry, depth + 1))
                elif entry.name == _SKILL_FILE:
                    yield entry


__all__ = ["DirectorySkillStore", "FileSkill"]
