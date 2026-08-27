"""Verify that the root wheel contains only the root distribution's code."""

from __future__ import annotations

import argparse
import zipfile
from collections.abc import Sequence
from pathlib import Path

FORBIDDEN_PREFIXES = ("skillcorpus_plugin/", "skillcorpus/tests/")


def _check_wheel(path: Path) -> list[str]:
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
    failures: list[str] = []
    if "skillcorpus/__init__.py" not in names:
        failures.append(f"{path.name}: missing skillcorpus/__init__.py")
    if not any(name.endswith(".dist-info/licenses/LICENSE") for name in names):
        failures.append(f"{path.name}: missing packaged LICENSE")
    for prefix in FORBIDDEN_PREFIXES:
        leaked = sorted(name for name in names if name.startswith(prefix))
        if leaked:
            preview = ", ".join(leaked[:3])
            failures.append(f"{path.name}: must not include {prefix} ({preview})")
    return failures


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wheel", nargs="+", type=Path)
    args = parser.parse_args(argv)
    failures = [failure for path in args.wheel for failure in _check_wheel(path)]
    if failures:
        print("Package contents check failed:")
        print("\n".join(f"- {failure}" for failure in failures))
        return 1
    print(f"Package contents check passed ({len(args.wheel)} wheel(s)).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
