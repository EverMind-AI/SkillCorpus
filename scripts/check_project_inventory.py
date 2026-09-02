"""Keep the supported SkillCorpus projects and public host list in sync.

The repository contains one core Python package and several independently
installable host integrations. Adding a new integration means updating this
inventory, its manifest, its guide, and both top-level README host tables in
the same review.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

import tomllib

ROOT = Path(__file__).resolve().parent.parent
PLUGIN_ROOT = ROOT / "skillcorpus_plugin"
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+(?:[a-zA-Z0-9.+-]*)$")


@dataclass(frozen=True)
class Project:
    label: str
    manifest: Path
    guide: Path
    expected_name: str
    format: str


PROJECTS = (
    Project(
        "core pipeline",
        Path("pyproject.toml"),
        Path("README.md"),
        "skillcorpus",
        "toml",
    ),
    Project(
        "Python retrieval engine",
        Path("skillcorpus_plugin/engine-python/pyproject.toml"),
        Path("skillcorpus_plugin/engine-python/README.md"),
        "skillsearch",
        "toml",
    ),
    Project(
        "DeepSeek Harness",
        Path("skillcorpus_plugin/engine-typescript/package.json"),
        Path("skillcorpus_plugin/engine-typescript/README.md"),
        "@evermind-ai/dsh-skill-search",
        "json",
    ),
    Project(
        "Hermes",
        Path("skillcorpus_plugin/plugin-hermes/plugin.yaml"),
        Path("skillcorpus_plugin/plugin-hermes/README.md"),
        "skillsearch",
        "yaml",
    ),
    Project(
        "OpenClaw",
        Path("skillcorpus_plugin/plugin-openclaw/package.json"),
        Path("skillcorpus_plugin/plugin-openclaw/README.md"),
        "@evermind-ai/openclaw-skillsearch",
        "json",
    ),
    Project(
        "OpenClaw 2.0",
        Path("skillcorpus_plugin/plugin-openclaw2/package.json"),
        Path("skillcorpus_plugin/plugin-openclaw2/README.md"),
        "@evermind-ai/openclaw2-skillsearch",
        "json",
    ),
    Project(
        "Raven",
        Path("skillcorpus_plugin/plugin-raven/pyproject.toml"),
        Path("skillcorpus_plugin/plugin-raven/README.md"),
        "skillsearch-raven",
        "toml",
    ),
    Project(
        "WorkBuddy",
        Path("skillcorpus_plugin/plugin-workbuddy/package.json"),
        Path("skillcorpus_plugin/plugin-workbuddy/README.md"),
        "@evermind-ai/workbuddy-skillsearch",
        "json",
    ),
)
HOST_GUIDES = tuple(project.guide.as_posix() for project in PROJECTS[2:])
EXPECTED_PLUGIN_DIRS = {
    ".github",
    "engine-python",
    "engine-typescript",
    "plugin-hermes",
    "plugin-openclaw",
    "plugin-openclaw2",
    "plugin-raven",
    "plugin-workbuddy",
    "scripts",
}


def _read_manifest(project: Project) -> tuple[str | None, str | None, bool | None]:
    content = (ROOT / project.manifest).read_text(encoding="utf-8")
    if project.format == "toml":
        data = tomllib.loads(content)["project"]
        return data.get("name"), data.get("version"), None
    if project.format == "json":
        data = json.loads(content)
        return data.get("name"), data.get("version"), data.get("private")

    name = re.search(r"^name:\s*[\"']?([^\"'\s]+)", content, re.M)
    version = re.search(r"^version:\s*[\"']?([^\"'\s]+)", content, re.M)
    return (
        name.group(1) if name else None,
        version.group(1) if version else None,
        None,
    )


def _check_projects() -> list[str]:
    failures: list[str] = []
    for project in PROJECTS:
        manifest = ROOT / project.manifest
        guide = ROOT / project.guide
        if not manifest.is_file():
            failures.append(f"{project.label}: missing manifest {project.manifest}")
            continue
        if not guide.is_file():
            failures.append(f"{project.label}: missing guide {project.guide}")
        name, version, private = _read_manifest(project)
        if name != project.expected_name:
            failures.append(
                f"{project.label}: manifest name {name!r} != {project.expected_name!r}"
            )
        if not version or not VERSION_RE.fullmatch(version):
            failures.append(f"{project.label}: invalid release version {version!r}")
        if project.label == "DeepSeek Harness" and private is not True:
            failures.append("DeepSeek Harness: package.json must remain private")
    return failures


def _check_plugin_dirs() -> list[str]:
    actual = {path.name for path in PLUGIN_ROOT.iterdir() if path.is_dir()}
    unexpected = sorted(actual - EXPECTED_PLUGIN_DIRS)
    missing = sorted(EXPECTED_PLUGIN_DIRS - actual)
    failures = [
        f"plugin inventory: undocumented directory {name}" for name in unexpected
    ]
    failures.extend(
        f"plugin inventory: expected directory missing: {name}" for name in missing
    )
    return failures


def _check_top_level_readmes() -> list[str]:
    failures: list[str] = []
    for readme in (ROOT / "README.md", ROOT / "README.zh-CN.md"):
        text = readme.read_text(encoding="utf-8")
        for guide in HOST_GUIDES:
            if guide not in text:
                failures.append(f"{readme.name}: host table missing {guide}")
    return failures


def main() -> int:
    failures = _check_projects() + _check_plugin_dirs() + _check_top_level_readmes()
    if failures:
        print("Project inventory check failed:")
        print("\n".join(f"- {failure}" for failure in failures))
        return 1
    print(f"Project inventory check passed ({len(PROJECTS)} maintained projects).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
