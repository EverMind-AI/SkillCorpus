"""Source registry + discovery + full fetch — read sources.yaml, then discover and concurrently clone every repo.

Single source of truth = ``sources.yaml``. Both ``fetch.py`` (full crawl) and
``scripts/refresh_loop.py`` (scheduled refresh) read from here, rather than each hard-coding its own list.

Each source entry carries a ``type`` that routes to the matching discovery handler:

    git_clone       — the repo itself
    readme_scrape   — clone the awesome-list repo + scrape the README's outbound links
    index_api       — REST API pagination (params: api_url/items_key/url_field/has_next_key)
    json_catalog    — clone repo + parse a JSON catalog (params: repo/json_path/url_field)
    sitemap_scrape  — fetch sitemap index + child sitemaps → owner/repo (params: sitemap_url)
    lobehub_json    — the repo itself (converted by lobehub_to_skills.py after clone, see refresh_loop)

``discover_repos(entry)`` uniformly returns ``list[(owner, repo)]`` — the caller is responsible for
clone + ingest. ``lobehub_json`` also returns [(owner,repo)], but needs special conversion at ingest time.

This module also holds the low-level clone primitives (``clone_repo`` / ``extract_repos_from_readme``
/ ``GH_RE`` / ``FETCHED``); fetch.py imports back from here (to avoid a circular dependency).
"""

from __future__ import annotations

import json as _json
import re
import shutil
import subprocess
import time as _time
import urllib.request
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
REF = REPO_ROOT / "experiment-results" / "_reference_skills"
FETCHED = REF / "_fetched"
DEFAULT_YAML = Path(__file__).resolve().parent / "sources.yaml"

# github URL regex: extract (owner, repo), tolerating a /tree/<branch>/<path> suffix
GH_RE = re.compile(
    r"https://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:/tree/[^)\s]+)?(?=[\s)])"
)


# ---------------------------------------------------------------------------
# Low-level clone primitives (pushed down from fetch.py, reused by fetch.py + this module's discovery)
# ---------------------------------------------------------------------------
def extract_repos_from_readme(readme_path: Path) -> list[tuple[str, str]]:
    """Scan a README for all github.com/<owner>/<repo> links, deduplicated."""
    if not readme_path.exists():
        return []
    text = readme_path.read_text(encoding="utf-8", errors="replace")
    seen: set[tuple[str, str]] = set()
    out: list[tuple[str, str]] = []
    for m in GH_RE.finditer(text):
        owner, repo = m.group(1), m.group(2).rstrip(".")
        if owner.lower() in {"voltagent", "awesome"}:
            continue
        if repo.endswith((".svg", ".png", ".jpg", ".gif")):
            continue
        if repo in {"workflows"}:
            continue
        key = (owner, repo)
        if key in seen:
            continue
        seen.add(key)
        out.append(key)
    return out


def clone_repo(owner: str, repo: str, timeout: int = 180,
               max_attempts: int = 2) -> tuple[Path | None, str]:
    """Clone github.com/<owner>/<repo> into FETCHED/. Returns (path, status),
    status ∈ {cloned, exists, fail}.

    GIT_TERMINAL_PROMPT=0 + GIT_ASKPASS=true: a private / auth-required repo fails
    immediately instead of hanging until timeout.
    Permanent failures (404/auth) are not retried; transient failures are retried once.
    """
    import os as _os
    dst = FETCHED / owner / repo
    if dst.exists() and (dst / ".git").exists():
        return dst, "exists"
    dst.parent.mkdir(parents=True, exist_ok=True)
    url = f"https://github.com/{owner}/{repo}.git"
    env = {**_os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": "true"}
    last_err = ""
    for _attempt in range(1, max_attempts + 1):
        if dst.exists():
            shutil.rmtree(dst, ignore_errors=True)
        try:
            subprocess.run(
                ["git", "clone", "--depth", "1", "--quiet", url, str(dst)],
                timeout=timeout, check=True, capture_output=True, env=env,
            )
            return dst, "cloned"
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
            err = getattr(e, "stderr", b"") or b""
            err_s = err.decode("utf-8", errors="replace") if isinstance(err, bytes) else str(err)
            last_err = err_s[:200]
            if any(s in err_s for s in (
                "not found", "Repository not found", "could not read Username",
                "Authentication failed", "remote: error: This repository",
            )):
                break
    if dst.exists():
        shutil.rmtree(dst, ignore_errors=True)
    return None, f"fail: {last_err}"


# ---------------------------------------------------------------------------
# Shared extractor (merges the duplicated GH_RE extraction from the old _skillsdirectory/_skillsmp/_skillmanager)
# ---------------------------------------------------------------------------
def _extract_github_repos(
    items: list[dict], url_field: str, fullname_field: str | None = None,
) -> list[tuple[str, str]]:
    """Extract distinct (owner, repo) from a batch of dicts. Prefer ``fullname_field``
    (a direct ``owner/repo`` field); otherwise extract via the github URL regex on ``url_field``."""
    seen: set[tuple[str, str]] = set()
    out: list[tuple[str, str]] = []
    for x in items:
        owner = repo = None
        if fullname_field:
            full = (x.get(fullname_field) or "").strip()
            if "/" in full:
                owner, repo = full.split("/", 1)
                repo = repo.split("/")[0].rstrip(".")
        if owner is None:
            m = GH_RE.search((x.get(url_field) or "") + " ")
            if not m:
                continue
            owner, repo = m.group(1), m.group(2).rstrip(".")
        if repo.endswith((".svg", ".png", ".jpg", ".gif")):
            continue
        key = (owner, repo)
        if key in seen:
            continue
        seen.add(key)
        out.append(key)
    return out


# ---------------------------------------------------------------------------
# Discovery handler for each type
# ---------------------------------------------------------------------------
def _index_api_repos(
    api_url: str, items_key: str, url_field: str,
    fullname_field: str | None = None, has_next_key: str = "hasNext",
    page_param: str = "page", limit_param: str = "limit", page_size: int = 20,
    limit: int = 0,
) -> list[tuple[str, str]]:
    """Generic REST API paginator (absorbs skillsdirectory + skillsmp).

    Per page, GET ``{api_url}?{page_param}=N&{limit_param}={page_size}``, extract repos
    from ``d[items_key]``, and use ``d['pagination'][has_next_key]`` to decide when to stop.
    """
    seen_out: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    page = 1
    while True:
        url = f"{api_url}?{page_param}={page}&{limit_param}={page_size}"
        try:
            with urllib.request.urlopen(url, timeout=30) as r:
                d = _json.loads(r.read())
        except Exception as e:
            print(f"  !! index_api page {page} failed: {e}")
            break
        items = d.get(items_key, []) or []
        if not items:
            break
        for owner, repo in _extract_github_repos(items, url_field, fullname_field):
            if (owner, repo) not in seen:
                seen.add((owner, repo))
                seen_out.append((owner, repo))
        pag = d.get("pagination", {}) or {}
        if not pag.get(has_next_key):
            break
        if limit and len(seen_out) >= limit:
            break
        page += 1
        _time.sleep(0.25)  # be polite
    return seen_out[:limit] if limit > 0 else seen_out


def _json_catalog_repos(
    repo: str, json_path: str, url_field: str, limit: int = 0,
) -> list[tuple[str, str]]:
    """Clone ``repo``, parse its ``json_path`` JSON catalog, and extract distinct repos
    (absorbs skillmanager). Supports a .gz fallback."""
    owner_repo = repo.split("/", 1)
    if len(owner_repo) != 2:
        return []
    o, r = owner_repo
    repo_dir = FETCHED / o / r
    if not repo_dir.exists():
        clone_repo(o, r)
    jp = repo_dir / json_path
    gz = Path(str(jp) + ".gz")  # same-name .gz fallback (some commits ship only the gzip version)
    if jp.exists():
        text = jp.read_text(encoding="utf-8", errors="replace")
    elif gz.exists():
        import gzip as _gz
        with _gz.open(gz, "rb") as f:
            text = f.read().decode("utf-8", errors="replace")
    else:
        print(f"  !! json_catalog file not found: {jp}")
        return []
    try:
        items = _json.loads(text)
    except Exception as e:
        print(f"  !! json_catalog parse failed: {e}")
        return []
    repos = _extract_github_repos(items if isinstance(items, list) else [], url_field)
    return repos[:limit] if limit > 0 else repos


def _fetch_text(url: str, timeout: int = 30) -> str:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.read().decode("utf-8", errors="replace")
    except Exception as e:
        print(f"  !! fetch failed {url}: {e}")
        return ""


_LOC_RE = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>", re.IGNORECASE)


def _sitemap_repos(sitemap_url: str, limit: int = 0) -> list[tuple[str, str]]:
    """Fetch a sitemap (index or leaf) and extract /<owner>/<repo>/<skill> from skill-page
    URLs → distinct (owner, repo) (newly written, used for skills.sh).

    A sitemap index's <loc> points to child sitemaps (.xml); a leaf sitemap's <loc> points
    to skill pages. The index is handled with one level of recursion.
    """
    seen: set[tuple[str, str]] = set()
    out: list[tuple[str, str]] = []

    def _harvest(xml: str) -> list[str]:
        return _LOC_RE.findall(xml)

    root = _fetch_text(sitemap_url)
    if not root:
        return []
    locs = _harvest(root)
    # Split: .xml child sitemaps vs skill pages
    child_sitemaps = [u for u in locs if u.rstrip("/").endswith(".xml")]
    page_urls = [u for u in locs if not u.rstrip("/").endswith(".xml")]
    if child_sitemaps:
        for cs in child_sitemaps:
            page_urls.extend(_harvest(_fetch_text(cs)))

    for u in page_urls:
        # https://host/<owner>/<repo>/<skill>  → take the first two path segments
        m = re.match(r"https?://[^/]+/([^/]+)/([^/]+)/[^/]+", u)
        if not m:
            continue
        owner, repo = m.group(1), m.group(2).rstrip(".")
        if owner in ("skills", "owners", "about", "search"):  # non-owner in-site paths
            continue
        key = (owner, repo)
        if key in seen:
            continue
        seen.add(key)
        out.append(key)
        if limit and len(out) >= limit:
            break
    return out


# ---------------------------------------------------------------------------
# Registry loading + dispatch
# ---------------------------------------------------------------------------
VALID_TYPES = {
    "git_clone", "readme_scrape", "index_api",
    "json_catalog", "sitemap_scrape", "lobehub_json",
}


def load_registry(yaml_path: Path | str = DEFAULT_YAML) -> list[dict]:
    """Read sources.yaml and return the list of entries (each containing name/repo/type/...)."""
    cfg = yaml.safe_load(Path(yaml_path).read_text(encoding="utf-8"))
    sources = cfg.get("sources", []) or []
    for s in sources:
        t = s.get("type")
        if t not in VALID_TYPES:
            raise ValueError(f"unknown source type {t!r} in entry {s.get('name')}")
    return sources


def discover_repos(entry: dict, timeout: int = 180, limit: int = 0) -> list[tuple[str, str]]:
    """Return the list of distinct (owner, repo) to clone for this source, keyed by entry['type'].

    git_clone / lobehub_json → itself; everything else → its corresponding discovery.
    """
    t = entry["type"]
    if t in ("git_clone", "lobehub_json"):
        repo = entry["repo"]
        if "/" not in repo:
            return []
        o, r = repo.split("/", 1)
        return [(o, r.split("/")[0])]
    if t == "readme_scrape":
        repo = entry["repo"]
        o, r = repo.split("/", 1)
        dst, _status = clone_repo(o, r, timeout=timeout)
        if dst is None:
            return []
        for name in ("README.md", "readme.md", "Readme.md"):
            rp = dst / name
            if rp.exists():
                return extract_repos_from_readme(rp)
        return []
    if t == "index_api":
        return _index_api_repos(
            api_url=entry["api_url"], items_key=entry.get("items_key", "skills"),
            url_field=entry["url_field"], fullname_field=entry.get("fullname_field"),
            has_next_key=entry.get("has_next_key", "hasNext"), limit=limit,
        )
    if t == "json_catalog":
        return _json_catalog_repos(
            repo=entry["repo"], json_path=entry["json_path"],
            url_field=entry["url_field"], limit=limit,
        )
    if t == "sitemap_scrape":
        return _sitemap_repos(entry["sitemap_url"], limit=limit)
    raise ValueError(f"unhandled type {t!r}")


def count_skills(repo_dir: Path) -> int:
    return sum(1 for _ in repo_dir.rglob("SKILL.md"))


def main():
    ap = argparse.ArgumentParser(prog="python -m skill_library.fetch")
    ap.add_argument("--dry-run", action="store_true",
                    help="only discover and list the repos to clone, without actually cloning "
                         "(still scrapes awesome lists / hits APIs / fetches sitemaps to build the full list)")
    ap.add_argument("--max-from-readme", type=int, default=0,
                    help="max number of outbound links to take per readme_scrape source (0 = unlimited, for debugging)")
    ap.add_argument("--timeout", type=int, default=180,
                    help="per-repo clone timeout in seconds")
    ap.add_argument("--workers", type=int, default=16,
                    help="number of concurrent clone threads (network I/O bound)")
    ap.add_argument("--config", type=Path, default=None,
                    help="path to sources.yaml (default: skill_library/sources.yaml)")
    args = ap.parse_args()

    FETCHED.mkdir(parents=True, exist_ok=True)
    registry = load_registry(args.config) if args.config else load_registry()

    # ---- Step 1: walk the registry, discover source by source ----
    all_repos: dict[tuple[str, str], str] = {}  # (owner, repo) → source tag
    print("=" * 70)
    print(f"[Step 1] discovery phase — {len(registry)} sources in the registry")
    print("=" * 70)
    for entry in registry:
        name = entry.get("name") or entry.get("repo") or "?"
        typ = entry["type"]
        try:
            repos = discover_repos(entry, timeout=args.timeout)
        except Exception as e:
            print(f"  ✗ [{typ}] {name}: discover failed {e!r}")
            continue
        if typ == "readme_scrape" and args.max_from_readme > 0:
            repos = repos[: args.max_from_readme]
        new = 0
        for o, r in repos:
            if (o, r) not in all_repos:
                all_repos[(o, r)] = f"{typ}:{name}"
                new += 1
        print(f"  [{typ}] {name}: discovered {len(repos)} repos (+{new} new)")

    # ---- Step 2: clone everything ----
    print()
    print("=" * 70)
    print(f"[Step 2] {len(all_repos)} repos total after merge + dedup")
    print("=" * 70)

    if args.dry_run:
        for (owner, repo), tag in sorted(all_repos.items()):
            print(f"  [{tag}] {owner}/{repo}")
        print(f"\n(dry-run, nothing actually cloned) {len(all_repos)} repos total")
        return

    stats = {"cloned": 0, "exists": 0, "fail": 0, "skill_total": 0}
    failed: list[str] = []
    items = sorted(all_repos.items())

    def _work(item):
        (owner, repo), _tag = item
        dst, status = clone_repo(owner, repo, timeout=args.timeout)
        n = count_skills(dst) if dst is not None else 0
        return (owner, repo, dst, status, n)

    processed = 0
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futures = [pool.submit(_work, it) for it in items]
        for fut in as_completed(futures):
            owner, repo, dst, status, n = fut.result()
            processed += 1
            if dst is None:
                stats["fail"] += 1
                failed.append(f"{owner}/{repo}")
                if processed <= 30 or processed % 50 == 0:
                    print(f"  [{processed}/{len(items)}] ✗ {owner}/{repo}  [{status[:50]}]")
                continue
            stats["cloned" if status == "cloned" else "exists"] += 1
            stats["skill_total"] += n
            if processed % 50 == 0 or processed == len(items):
                marker = f"{n} md" if n else "no md"
                print(f"  [{processed}/{len(items)}] ✓ "
                      f"cloned={stats['cloned']} exists={stats['exists']} fail={stats['fail']} "
                      f"skill_total={stats['skill_total']} — latest {owner}/{repo} ({marker})")

    # ---- Summary ----
    print()
    print("=" * 70)
    print("Summary")
    print("=" * 70)
    print(f"  total requested: {len(all_repos)}")
    print(f"  cloned (new):    {stats['cloned']}")
    print(f"  already existed: {stats['exists']}")
    print(f"  failed:          {stats['fail']}")
    print(f"  SKILL.md total:  {stats['skill_total']}")
    print(f"  output dir:      {FETCHED}")
    if failed:
        print(f"\nFailed repos ({len(failed)}):")
        for f in failed[:30]:
            print(f"  {f}")
        if len(failed) > 30:
            print(f"  ... and {len(failed) - 30} more")


if __name__ == "__main__":
    main()
