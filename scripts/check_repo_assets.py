"""Reject committed images, videos, and asset-style directories.

README media must be externally hosted (for example, on the project paper,
GitHub user content, or a release asset) and linked into the documentation.
Keeping it out of the source tree prevents repository bloat and makes the
large-file policy enforceable.
"""

from __future__ import annotations

import subprocess
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import PurePosixPath

BLOCKED_DIR_NAMES = frozenset(
    {"asset", "assets", "image", "images", "img", "media", "video", "videos"}
)
IMAGE_EXTENSIONS = frozenset(
    {
        ".avif",
        ".bmp",
        ".gif",
        ".heic",
        ".heif",
        ".icns",
        ".ico",
        ".jpeg",
        ".jpg",
        ".png",
        ".svg",
        ".tif",
        ".tiff",
        ".webp",
    }
)
VIDEO_EXTENSIONS = frozenset(
    {".avi", ".flv", ".m4v", ".mkv", ".mov", ".mp4", ".mpeg", ".mpg", ".webm", ".wmv"}
)


@dataclass(frozen=True)
class Violation:
    path: str
    reason: str


def _violation_reason(path: str) -> str | None:
    posix_path = PurePosixPath(path.replace("\\", "/"))
    if any(part.lower() in BLOCKED_DIR_NAMES for part in posix_path.parts):
        return "asset/media directory"
    if posix_path.suffix.lower() in IMAGE_EXTENSIONS:
        return "image file"
    if posix_path.suffix.lower() in VIDEO_EXTENSIONS:
        return "video file"
    return None


def find_violations(paths: Iterable[str]) -> list[Violation]:
    return [
        Violation(path=path, reason=reason)
        for path in paths
        if (reason := _violation_reason(path)) is not None
    ]


def _tracked_paths() -> list[str]:
    result = subprocess.run(
        ["git", "ls-files", "-z"], check=True, capture_output=True, text=False
    )
    return [raw.decode("utf-8") for raw in result.stdout.split(b"\0") if raw]


def main() -> int:
    violations = find_violations(_tracked_paths())
    if not violations:
        print("Repository asset/media check passed.")
        return 0

    print(
        "Repository asset/media check failed.\n"
        "Do not commit images, videos, or asset/media directories. Host media "
        "externally or in a release artifact, then link to it from the docs.\n"
    )
    for violation in violations:
        print(f"- {violation.path}: {violation.reason}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
