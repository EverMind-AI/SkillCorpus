"""aggregate.discover — expand a source entry into distinct (owner, repo) pairs.

git_clone / lobehub_json resolve to themselves; readme_scrape / index_api /
json_catalog / sitemap_scrape scrape or hit an API to enumerate repos.
"""
from __future__ import annotations

import json as _json
import re
import time as _time
import urllib.request
from pathlib import Path

from .clone import clone_or_pull, FETCHED


GH_RE = re.compile(
    r"https://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:/tree/[^)\s]+)?(?=[\s)])"
)



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
        if page >= 5000:  # runaway guard for a misbehaving has_next
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
        clone_or_pull(o, r)
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
        dst, _status = clone_or_pull(o, r, timeout=timeout)
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

