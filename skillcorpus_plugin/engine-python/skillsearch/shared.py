"""The one directory all five hosts agree on, and the registry inside it.

Every host this plugin family supports scans its own skills directory and only
its own, so a skill a user has in one agent is invisible to the other four.
This module is the fix: a single shared root, and a registry each host writes
its own directory into at startup so the others can read it.

## Why the root is a hardcoded expression

``homedir() / ".evermind-skillsearch"``, the same on all three platforms, with
no per-platform branch. On Windows that expands to
``C:\\Users\\x\\.evermind-skillsearch``, which is not the Windows convention —
``%LOCALAPPDATA%`` is. Consistency is chosen over convention on purpose:

- this is the *one* path all five hosts must compute identically, and the whole
  feature is premised on them landing in the same place;
- a platform branch is somewhere for them to diverge. An agent started as a
  service or a scheduled task has a different environment from a desktop
  session, so ``%LOCALAPPDATA%`` can resolve elsewhere and the five split apart
  with nothing logged;
- it is the plugin's own directory, not a system integration point, and the
  user has to open it to drop skills in.

``SKILLSEARCH_HOME`` overrides it, but only as an advanced escape hatch: a
GUI-launched agent never reads a shell profile, so the default can never depend
on it.

## Why hosts self-register instead of us hardcoding their directories

Hardcoding the five defaults is wrong in three ways at once — host versions
change the default, users move it, and the Windows location is not knowable
from here. So each host writes the absolute path it actually resolved at
runtime, and reads the whole table back. What a host then sees is exactly "the
directories of the other hosts that also have this plugin installed", which is
the correct set: a host without the plugin has nothing to share anyway.

## Everything here fails open

A registry that cannot be read or written costs this machine the sharing
feature, and nothing else. It must never cost a turn — on WorkBuddy the hook
that calls this runs per turn, inside an 8-second budget, and a raising hook
blocks the user's message. Every public function here swallows and degrades.
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

#: Overrides the root. Advanced use only — see the module docstring.
HOME_ENV = "SKILLSEARCH_HOME"

_NOTE = (
    "To exclude a directory, set its `enabled` to false. Deleting the line does "
    "not work — that agent re-registers it on its next start."
)


def shared_root() -> Path:
    """The shared root, honouring ``SKILLSEARCH_HOME``.

    Never raises and never creates anything: callers that only read should not
    have to mkdir, and the ones that write say so.
    """
    override = os.environ.get(HOME_ENV, "").strip()
    if override:
        return Path(os.path.expanduser(override))
    return Path(os.path.expanduser("~")) / ".evermind-skillsearch"


def shared_skills_dir() -> Path:
    """Where skills installed through the plugin land."""
    return shared_root() / "skills"


def registry_path() -> Path:
    """The registry of host skills directories."""
    return shared_root() / "registry.json"


def shared_config_path() -> Path:
    """Settings all five hosts read, so one edit applies everywhere."""
    return shared_root() / "config.json"


@dataclass(frozen=True)
class HostEntry:
    """One host's registration.

    ``enabled`` belongs to the user, not to the host that wrote the line.
    """

    id: str
    dir: str
    enabled: bool = True

    def as_json(self) -> dict[str, object]:
        return {"id": self.id, "dir": self.dir, "enabled": self.enabled}


def read_registry(path: Path | None = None) -> list[HostEntry]:
    """Every registered host, or an empty list if the file is unusable.

    A missing, truncated, hand-corrupted or half-written registry all answer
    the same way. The alternative — raising — turns a broken JSON file into a
    broken agent on five hosts at once.
    """
    target = path or registry_path()
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(raw, dict):
        return []
    entries: list[HostEntry] = []
    seen: set[str] = set()
    for item in raw.get("hosts") or []:
        if not isinstance(item, dict):
            continue
        host_id = str(item.get("id") or "").strip()
        directory = str(item.get("dir") or "").strip()
        if not host_id or not directory or host_id in seen:
            continue
        seen.add(host_id)
        enabled = item.get("enabled")
        entries.append(HostEntry(host_id, directory, True if enabled is None else bool(enabled)))
    return entries


def _write_registry(entries: list[HostEntry], path: Path) -> bool:
    """Replace the registry atomically. ``False`` if anything went wrong.

    Temp file in the same directory, then ``os.replace`` — the same shape
    `hub_client` uses for bundles, and for the same reason: five processes can
    reach this concurrently, and a reader must never see a half-written file.
    A loser of the race is fine; its next start writes again.
    """
    payload = {"_note": _NOTE, "hosts": [entry.as_json() for entry in entries]}
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handle, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".registry-", suffix=".json")
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, indent=2, ensure_ascii=False)
                fh.write("\n")
            os.replace(tmp, path)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(tmp)
            raise
    except (OSError, ValueError):
        return False
    return True


def register_host(host_id: str, skills_dir: str | os.PathLike[str] | None, path: Path | None = None) -> list[HostEntry]:
    """Record this host's skills directory and return the whole table.

    Cheap and idempotent by design: the common case is one small read and no
    write at all. WorkBuddy's hook calls this once per turn, on the turn's hot
    path, so a write every turn would be a real cost — and five agents
    rewriting the same file all day is a race nobody needs.

    ``enabled`` is never written back for an entry that already exists. That is
    what makes the file editable: a user who sets ``enabled: false`` must not
    have it undone by the next start of the host it belongs to.

    @param host_id - stable per host, e.g. ``"raven"``. One entry per id.
    @param skills_dir - resolved to an absolute path; relative paths and ``~``
      would each have to be re-resolved by a reader on a different platform.
      ``None`` or empty registers nothing but still returns the table.
    @returns every entry, this host's included. Empty on any failure.
    """
    target = path or registry_path()
    entries = read_registry(target)
    host_id = str(host_id or "").strip()
    if not host_id or not skills_dir:
        return entries

    try:
        resolved = Path(os.path.expanduser(str(skills_dir))).resolve()
    except (OSError, ValueError, RuntimeError):
        return entries
    if not resolved.is_absolute():
        return entries
    wanted = str(resolved)

    for index, entry in enumerate(entries):
        if entry.id != host_id:
            continue
        if entry.dir == wanted:
            # The short circuit the per-turn caller depends on: one read.
            return entries
        # The path moved. Update it and keep the user's `enabled` as it is.
        entries[index] = HostEntry(host_id, wanted, entry.enabled)
        break
    else:
        entries.append(HostEntry(host_id, wanted, True))

    _write_registry(entries, target)
    return entries


def registered_dirs(
    host_id: str = "", path: Path | None = None, *, include_self: bool = False
) -> list[tuple[str, str]]:
    """Directories to scan, as ``(path, name)`` pairs.

    Filtered three ways, all of them deliberate: entries the user disabled are
    dropped; entries whose directory no longer exists are dropped, because an
    uninstalled agent leaves its line behind; and this host's own directory is
    dropped unless asked for, since the caller already scans it and a second
    copy would compete with itself in one ranking.

    @param host_id - whose entry to treat as "self".
    @param include_self - keep this host's own entry in the result.
    """
    out: list[tuple[str, str]] = []
    for entry in read_registry(path):
        if not entry.enabled:
            continue
        if entry.id == host_id and not include_self:
            continue
        try:
            if not Path(entry.dir).is_dir():
                continue
        except OSError:
            continue
        out.append((entry.dir, entry.id))
    return out


def shared_dirs(
    host_id: str, skills_dir: str | os.PathLike[str] | None = None, path: Path | None = None
) -> list[tuple[str, str]]:
    """Register, then answer with everything worth scanning beyond our own.

    The one call a host adapter needs. The shared skills directory comes first
    and is always present — it is where this plugin installs things, so it
    exists whether or not any other host has registered.

    Never raises.
    """
    try:
        register_host(host_id, skills_dir, path)
        dirs: list[tuple[str, str]] = []
        shared = shared_skills_dir()
        if shared.is_dir():
            dirs.append((str(shared), "shared"))
        own = None
        if skills_dir:
            try:
                own = str(Path(os.path.expanduser(str(skills_dir))).resolve())
            except (OSError, ValueError, RuntimeError):
                own = None
        for directory, name in registered_dirs(host_id, path):
            # Two hosts pointed at one directory is a real configuration —
            # OpenClaw 1 and 2 share `~/.openclaw/skills` — and scanning it
            # twice would double every skill in it.
            if directory == own or any(directory == d for d, _ in dirs):
                continue
            dirs.append((directory, name))
    except Exception:  # sharing is never worth a failed turn
        return []
    return dirs


#: The list of extra skills directories, comma-separated. Named and split to
#: match the three TypeScript packages, which have had it since 0.2.0 — a
#: deployment that sets it should not have to care which host reads it.
DIRS_ENV = "SKILLSEARCH_SKILLS_DIRS"


def configured_dirs(value: object, env: dict[str, str] | None = None) -> list[str]:
    """Extra directories from a config value or the environment.

    Accepts a list or a comma-separated string, the same two shapes and the
    same splitting as `asList` in the TypeScript packages. The environment wins
    over the config value, again matching them.
    """
    source = env if env is not None else os.environ
    raw: object = source.get(DIRS_ENV, "").strip() or value
    if isinstance(raw, str):
        return [part.strip() for part in raw.split(",") if part.strip()]
    if isinstance(raw, (list, tuple)):
        return [str(part).strip() for part in raw if str(part).strip()]
    return []


def extra_dirs_for(
    host_id: str,
    skills_dir: str | os.PathLike[str] | None,
    configured: object = None,
    path: Path | None = None,
    env: dict[str, str] | None = None,
) -> list[dict[str, object]]:
    """Everything to hand `SearchConfig.extra_dirs`, in one call.

    Three sources in order, deduplicated by resolved path: the directories the
    deployment configured, the shared skills directory, and the other hosts'
    directories from the registry. Registers this host on the way through.

    Returns plain dicts because that is what `SearchConfig.from_mapping`
    coerces; the caller does not need to import `LocalDir`.

    Never raises — see the module docstring.
    """
    out: list[dict[str, object]] = []
    seen: set[str] = set()

    def add(directory: str, name: str) -> None:
        try:
            resolved = str(Path(os.path.expanduser(directory)).resolve())
        except (OSError, ValueError, RuntimeError):
            return
        if resolved in seen:
            return
        seen.add(resolved)
        out.append({"path": resolved, "name": name, "enabled": True})

    try:
        # The host's own main directory is scanned separately by the engine,
        # so it only goes in `seen` — enough to keep the registry from adding
        # it back a second time.
        if skills_dir:
            with contextlib.suppress(OSError, ValueError, RuntimeError):
                seen.add(str(Path(os.path.expanduser(str(skills_dir))).resolve()))
        for directory in configured_dirs(configured, env):
            add(directory, "configured")
        for directory, name in shared_dirs(host_id, skills_dir, path):
            add(directory, name)
    except Exception:  # sharing is never worth a failed turn
        return out
    return out


def opted_in(value: object, default: bool = True) -> bool:
    """Whether a host's config asked to join the shared library.

    Its own switch, separate from the registry's ``enabled``, because the two
    answer different questions and users conflate them: this one is "do I read
    the others", the registry's is "do the others read me".

    Strings are accepted because a host config file, or an environment
    variable, delivers one — ``"false"``, ``"0"``, ``"no"`` and ``"off"`` all
    mean off.
    """
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in ("false", "0", "no", "off"):
        return False
    if text in ("true", "1", "yes", "on"):
        return True
    return default
