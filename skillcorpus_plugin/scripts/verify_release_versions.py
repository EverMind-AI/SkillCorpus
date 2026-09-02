from __future__ import annotations

import json
import re
import tarfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = ROOT.parent
EXPECTED = "0.3.0"
MARKETPLACE = json.loads(
    (REPO_ROOT / ".codebuddy-plugin/marketplace.json").read_text(encoding="utf-8")
)


def text_version(path: str, pattern: str) -> str:
    match = re.search(pattern, (ROOT / path).read_text(encoding="utf-8"), re.MULTILINE)
    if not match:
        raise SystemExit(f"version not found in {path}")
    return match.group(1)


versions = {
    "engine-python": text_version("engine-python/pyproject.toml", r'^version = "([^"]+)"'),
    "raven": text_version("plugin-raven/pyproject.toml", r'^version = "([^"]+)"'),
    "hermes": text_version("plugin-hermes/plugin.yaml", r'^version: "([^"]+)"'),
    "openclaw": json.loads((ROOT / "plugin-openclaw/package.json").read_text())["version"],
    "openclaw-manifest": json.loads(
        (ROOT / "plugin-openclaw/openclaw.plugin.json").read_text()
    )["version"],
    # OpenClaw ships as two packages — 2.0 dropped the hook the first one
    # injects through — so a release that validates only one can publish a
    # mismatched pair.
    "openclaw2": json.loads((ROOT / "plugin-openclaw2/package.json").read_text())["version"],
    "openclaw2-manifest": json.loads(
        (ROOT / "plugin-openclaw2/openclaw.plugin.json").read_text()
    )["version"],
    "workbuddy": json.loads((ROOT / "plugin-workbuddy/package.json").read_text())["version"],
}
if MARKETPLACE.get("name") != "skillcorpus":
    raise SystemExit("WorkBuddy marketplace name must be skillcorpus")
entries = [entry for entry in MARKETPLACE.get("plugins", []) if entry.get("name") == "skillsearch"]
if len(entries) != 1:
    raise SystemExit("WorkBuddy marketplace must contain exactly one skillsearch plugin")
entry = entries[0]
source = (REPO_ROOT / entry["source"]).resolve()
expected_source = (ROOT / "plugin-workbuddy").resolve()
if source != expected_source:
    raise SystemExit(f"WorkBuddy marketplace source must resolve to {expected_source}, got {source}")
# Both runtimes and both manifests. On-demand mode reaches the model through
# the MCP server rather than the hook, so a release carrying only `hook.mjs`
# ships a plugin whose default mode has no runtime at all.
for required in (
    source / "dist/hook.mjs",
    source / "hooks/hooks.json",
    source / "dist/mcp.mjs",
    source / "mcp/servers.json",
):
    if not required.is_file():
        raise SystemExit(f"WorkBuddy marketplace is missing required runtime file: {required}")
plugin_manifest = json.loads((source / ".codebuddy-plugin/plugin.json").read_text(encoding="utf-8"))
if plugin_manifest.get("name") != entry["name"]:
    raise SystemExit("WorkBuddy marketplace and plugin manifest names disagree")
versions["workbuddy-marketplace"] = entry.get("version")
versions["workbuddy-manifest"] = plugin_manifest.get("version")

wrong = {name: version for name, version in versions.items() if version != EXPECTED}
if wrong:
    raise SystemExit(f"release versions must all be {EXPECTED}: {wrong}")

for dist in (ROOT / "engine-python/dist", ROOT / "plugin-raven/dist"):
    for archive in dist.glob("*"):
        if archive.suffix == ".whl":
            with zipfile.ZipFile(archive) as package:
                names = package.namelist()
        elif archive.name.endswith(".tar.gz"):
            with tarfile.open(archive) as package:
                names = package.getnames()
        else:
            continue
        if not any(name.endswith(("LICENSE", "LICENSE.txt")) for name in names):
            raise SystemExit(f"{archive.name} does not contain LICENSE")
