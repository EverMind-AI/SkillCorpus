from __future__ import annotations

import json
import re
import tarfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXPECTED = "0.2.0"


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
    "workbuddy": json.loads((ROOT / "plugin-workbuddy/package.json").read_text())["version"],
}
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
