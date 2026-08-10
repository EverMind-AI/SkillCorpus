"""aggregate.clone — clone-or-pull a GitHub repo into the SKILLCORPUS_HOME cache.

Merges the two former clone paths (fetch.clone_repo + refresh_loop._git_pull_or_clone)
into one: pull if already cached, else clone with retry.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

from ..core.paths import CACHE_DIR as FETCHED

# Permanent failures (404 / private / auth) are not worth retrying.
_PERMANENT = (
    "not found", "Repository not found", "could not read Username",
    "Authentication failed", "remote: error: This repository",
)

# owner / repo reach here straight from remote-scraped discovery (sitemap /
# README / JSON catalog), so they are untrusted. A single-segment name matches
# this and cannot be "." / ".." — which is what stops `FETCHED / ".." / ".."`
# from resolving to a parent dir and getting rmtree'd on the clone retry path.
_SAFE_SEGMENT = re.compile(r"[A-Za-z0-9_.-]+")


def _safe_segment(seg: str) -> bool:
    return bool(_SAFE_SEGMENT.fullmatch(seg)) and seg not in (".", "..")


def clone_or_pull(owner: str, repo: str, timeout: int = 180,
                  max_attempts: int = 2) -> tuple[Path | None, str]:
    """Pull if already cached, else clone github.com/<owner>/<repo> into FETCHED/.

    Returns (path, status), status in {pulled, cloned, fail}. GIT_TERMINAL_PROMPT=0
    + GIT_ASKPASS=true make a private / auth-required repo fail fast instead of
    hanging; permanent failures are not retried, transient ones retried once.
    """
    if not _safe_segment(owner) or not _safe_segment(repo):
        return None, f"fail: unsafe repo name {owner!r}/{repo!r}"
    dst = FETCHED / owner / repo
    # Defence in depth: never touch anything outside the cache root, even if a
    # bad segment ever slips past the check above.
    if not dst.resolve().is_relative_to(FETCHED.resolve()):
        return None, f"fail: path escapes cache: {dst}"
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": "true"}

    if (dst / ".git").is_dir():
        try:
            subprocess.run(
                ["git", "-C", str(dst), "pull", "--quiet", "--rebase=false"],
                timeout=timeout, capture_output=True, env=env, check=True,
            )
            return dst, "pulled"
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            # Pull failed (often a transient network blip). Fall through to a
            # fresh clone, but keep the existing cache until the new clone
            # succeeds — a blip must not destroy a good cache.
            pass

    dst.parent.mkdir(parents=True, exist_ok=True)
    url = f"https://github.com/{owner}/{repo}.git"
    # Clone into a temp sibling and swap in only on success, so a failed clone
    # never leaves a partial dir or removes the previous good cache.
    tmp = dst.parent / f".{repo}.tmp-clone"
    last_err = ""
    for _attempt in range(1, max_attempts + 1):
        shutil.rmtree(tmp, ignore_errors=True)
        try:
            subprocess.run(
                ["git", "clone", "--depth", "1", "--quiet", url, str(tmp)],
                timeout=timeout, check=True, capture_output=True, env=env,
            )
            if dst.exists():
                shutil.rmtree(dst, ignore_errors=True)
            os.replace(tmp, dst)
            return dst, "cloned"
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
            err_s = (getattr(e, "stderr", b"") or b"").decode("utf-8", "replace")
            last_err = err_s[:200]
            if any(s in err_s for s in _PERMANENT):
                break
    shutil.rmtree(tmp, ignore_errors=True)
    # Clone failed: if a prior good cache survived, use it (stale) rather than
    # losing the repo to a transient failure.
    if (dst / ".git").is_dir():
        return dst, "stale"
    return None, f"fail: {last_err}"
