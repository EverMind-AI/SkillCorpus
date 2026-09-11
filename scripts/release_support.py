"""Validate release notes and the flat set of downloadable release assets."""

from __future__ import annotations

import argparse
import hashlib
import re
from pathlib import Path

REPO = "https://github.com/EverMind-AI/SkillCorpus"
TAG_RE = re.compile(r"v[0-9]+\.[0-9]+\.[0-9]+")


def check_notes(tag: str, root: Path = Path(".")) -> str:
    if not TAG_RE.fullmatch(tag):
        raise ValueError("expected a stable release tag such as v0.3.0")
    notes = (root / "docs" / "releases" / f"{tag}.md").read_text(encoding="utf-8")
    if "## What’s Changed" not in notes and "## What's Changed" not in notes:
        raise ValueError("release notes must include What’s Changed")
    pattern = (
        re.escape(f"**Full Changelog**: {REPO}/compare/")
        + r"([\w./-]+)\.\.\."
        + re.escape(tag)
    )
    match = re.search(pattern + r"\s*$", notes)
    if not match or match[1] == tag:
        raise ValueError("Full Changelog must end with the previous tag...this tag")
    return match[1]


def expected_assets(version: str) -> set[str]:
    if not TAG_RE.fullmatch("v" + version):
        raise ValueError("invalid release version")
    names = {f"skillcorpus-plugins-v{version}.tar.gz"}
    for package in ("skillcorpus", "skillsearch", "skillsearch_raven"):
        names.add(f"{package}-{version}-py3-none-any.whl")
        names.add(f"{package}-{version}.tar.gz")
    for package in ("openclaw", "openclaw2", "workbuddy"):
        names.add(f"evermind-ai-{package}-skillsearch-{version}.tgz")
    return names


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def prepare_assets(directory: Path, version: str) -> None:
    paths = [
        path
        for path in directory.rglob("*")
        if path.is_file() and path.name != "SHA256SUMS"
    ]
    names = [path.name for path in paths]
    if len(names) != len(set(names)):
        raise ValueError("duplicate release asset basenames")
    if set(names) != expected_assets(version):
        raise ValueError(
            f"unexpected release assets: {set(names) ^ expected_assets(version)}"
        )
    for path in paths:
        target = directory / path.name
        if path != target:
            path.rename(target)
    lines = [f"{digest(directory / name)}  {name}\n" for name in sorted(names)]
    (directory / "SHA256SUMS").write_text("".join(lines), encoding="utf-8")
    verify_assets(directory, version)


def verify_assets(directory: Path, version: str) -> None:
    expected = expected_assets(version)
    actual = {path.name for path in directory.iterdir() if path.is_file()}
    if actual != expected | {"SHA256SUMS"}:
        raise ValueError(
            f"missing or extra release assets: {actual ^ (expected | {'SHA256SUMS'})}"
        )
    records: dict[str, str] = {}
    for line in (directory / "SHA256SUMS").read_text(encoding="utf-8").splitlines():
        match = re.fullmatch(r"([a-f0-9]{64})  ([A-Za-z0-9_.-]+)", line)
        if not match or match[2] in records:
            raise ValueError("invalid or duplicate checksum entry; use flat filenames")
        records[match[2]] = match[1]
    if set(records) != expected:
        raise ValueError(
            "checksum manifest must cover every release asset exactly once"
        )
    for name, expected_digest in records.items():
        if digest(directory / name) != expected_digest:
            raise ValueError(f"checksum mismatch: {name}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("check-notes").add_argument("tag")
    for command in ("prepare", "verify"):
        child = commands.add_parser(command)
        child.add_argument("directory", type=Path)
        child.add_argument("version")
    args = parser.parse_args()
    if args.command == "check-notes":
        print(check_notes(args.tag))
    elif args.command == "prepare":
        prepare_assets(args.directory, args.version)
    else:
        verify_assets(args.directory, args.version)


if __name__ == "__main__":
    main()
