"""Block oversized files introduced or enlarged by the current change.

Pull requests and local checks compare against the merge base. Pushes audit
the whole tracked tree: origin/main already points at HEAD after a main push,
so comparing those two would silently check nothing.
"""

from __future__ import annotations

import argparse
import os
import subprocess
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

MAX_KB = 640
DEFAULT_BASE = "origin/main"
EXEMPT_PREFIXES: tuple[str, ...] = ()
EXEMPT_PATHS = frozenset()


class BaseRefError(RuntimeError):
    """The comparison base could not be resolved."""


@dataclass(frozen=True)
class Violation:
    path: str
    size_bytes: int


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True)
    if result.returncode:
        detail = result.stderr.strip() or "unknown error"
        raise BaseRefError(f"`git {' '.join(args)}` failed: {detail}")
    return result.stdout


def default_base_ref() -> str:
    base = os.environ.get("GITHUB_BASE_REF", "").strip()
    return f"origin/{base}" if base else DEFAULT_BASE


def changed_paths(root: Path, base_ref: str) -> list[str]:
    merge_base = _git(root, "merge-base", base_ref, "HEAD").strip()
    if not merge_base:
        raise BaseRefError(f"no merge base between {base_ref} and HEAD")

    changed = _git(
        root,
        "diff",
        "--name-only",
        "-z",
        "--diff-filter=ACMR",
        merge_base,
    )
    untracked = _git(root, "ls-files", "--others", "--exclude-standard", "-z")
    paths = {path for path in changed.split("\0") if path}
    paths.update(path for path in untracked.split("\0") if path)
    return sorted(paths)


def all_paths(root: Path) -> list[str]:
    tracked = _git(root, "ls-files", "-z")
    untracked = _git(root, "ls-files", "--others", "--exclude-standard", "-z")
    return sorted({path for path in (tracked + untracked).split("\0") if path})


def is_exempt(path: str, prefixes: Sequence[str] = EXEMPT_PREFIXES) -> bool:
    normalised = path.replace("\\", "/")
    return normalised in EXEMPT_PATHS or any(
        normalised.startswith(prefix) for prefix in prefixes
    )


def find_violations(
    paths: Iterable[str],
    *,
    root: Path,
    max_kb: int = MAX_KB,
) -> list[Violation]:
    violations: list[Violation] = []
    for path in paths:
        candidate = root / path
        if is_exempt(path) or candidate.is_symlink() or not candidate.is_file():
            continue
        if candidate.stat().st_size > max_kb * 1024:
            violations.append(Violation(path=path, size_bytes=candidate.stat().st_size))
    return violations


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", help="ref to diff against")
    parser.add_argument(
        "--all", action="store_true", help="audit the complete tracked tree"
    )
    args = parser.parse_args(argv)

    root = _repo_root()
    base_ref = args.base or default_base_ref()
    full_scan = args.all or (
        not args.base and os.environ.get("GITHUB_EVENT_NAME") == "push"
    )
    try:
        paths = all_paths(root) if full_scan else changed_paths(root, base_ref)
    except BaseRefError as exc:
        print(
            f"Repository file-size check could not run: {exc}\n"
            "Fetch the base branch and full history first; CI must use fetch-depth: 0."
        )
        return 1

    violations = find_violations(paths, root=root)
    if not violations:
        print(
            f"Repository file-size check passed ({len(paths)} file(s), "
            f"{'full tree' if full_scan else 'vs ' + base_ref}, ceiling {MAX_KB} KB)."
        )
        return 0

    print(
        f"Repository file-size check failed: this change adds or grows files above "
        f"{MAX_KB} KB. Store large payloads externally or as a release artifact."
    )
    for violation in sorted(violations, key=lambda item: -item.size_bytes):
        print(f"- {violation.path}: {violation.size_bytes / 1024:.1f} KB")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
