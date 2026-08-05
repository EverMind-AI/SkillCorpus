"""License audit — single-entry maintenance of source license metadata.

Consolidates the formerly scattered scripts:
  scripts/_archive/enrich_unmapped_licenses.py   →  ``refresh``
  (manual SQL rebuild of license_safe_sources.json)  →  ``build``
  (none)                                          →  ``validate``
  step 4 of enrich_unmapped_licenses.py           →  ``apply``
  (none)                                          →  ``stats``

Each subcommand runs standalone, or they can be chained as ``refresh && build && apply`` for a weekly/monthly routine.

Data flow:
    GitHub API (spdx_id)
        ↓ refresh (concurrent fetch, incremental append)
    source_license_report.csv  (source → category mapping)
        ↓ build  (filter to GREEN_LICENSES)
    license_safe_sources.json  (runtime whitelist)
        ↓ apply (backfill DB skills.license)
    data/index.db skills.license  (per-skill persistence)

Usage:
    GITHUB_TOKEN=ghp_xxx python3 -m skill_library.license_audit refresh
    python3 -m skill_library.license_audit build
    python3 -m skill_library.license_audit validate
    python3 -m skill_library.license_audit apply --dry-run
    python3 -m skill_library.license_audit stats
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sqlite3
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .license import (
    GREEN_LICENSES,
    RED_LICENSES,
    YELLOW_LICENSES,
    _JUNK_LIC_STRINGS,
    normalize_license,
)

# Category strings _gh_fetch_license can return that are NOT actual licenses
# (repo status, not an SPDX id). They must never be written into
# skills.license by ``apply`` — otherwise the consumer receives "NO_LICENSE"
# etc. as if it were a license.
_NON_LICENSE_CATEGORIES = frozenset(
    {"NO_LICENSE", "SOURCE_NOT_FETCHED", "Custom", "NOASSERTION"}
)

# ---------------------------------------------------------------------------
# Paths — single source of truth
# ---------------------------------------------------------------------------
_PKG_DIR = Path(__file__).resolve().parent.parent              # skill_library/
_REPO_ROOT = _PKG_DIR.parent                            # skill/
DEFAULT_DB = _PKG_DIR / "data" / "index.db"
DEFAULT_CSV = _REPO_ROOT / "source_license_report.csv"
DEFAULT_JSON = _PKG_DIR / "license_safe_sources.json"


def _load_csv_map(csv_path: Path) -> dict[str, str]:
    """source → license_category from the report CSV; {} if file absent.

    Tolerant of a missing file (refresh may run before the CSV exists),
    unlike license_filter.load_source_license_map which opens unconditionally.
    """
    if not csv_path.exists():
        return {}
    out: dict[str, str] = {}
    with open(csv_path) as f:
        for row in csv.DictReader(f):
            out[row["source"]] = row["license_category"]
    return out


# ---------------------------------------------------------------------------
# refresh — GitHub API → CSV (incremental)
# ---------------------------------------------------------------------------
def _gh_fetch_license(owner_repo: str, token: str, max_rate_retries: int = 5) -> str:
    """Return spdx_id for `owner/repo`, or status string on failure.

    ``NO_LICENSE``           — repo exists but no LICENSE file detected
    ``SOURCE_NOT_FETCHED``   — 404 / network / non-200 (retried on 403/429)
    ``Custom``               — repo has LICENSE but no recognized spdx

    Rate-limit (403/429) is retried with 60s backoff up to ``max_rate_retries``
    times, then gives up as SOURCE_NOT_FETCHED — bounded so a persistent
    auth failure can't recurse into a stack overflow.
    """
    url = f"https://api.github.com/repos/{owner_repo}/license"
    for _attempt in range(max_rate_retries + 1):
        try:
            r = subprocess.run(
                ["curl", "-s", "-o", "-", "-w", "\n%{http_code}",
                 "-H", f"Authorization: Bearer {token}",
                 "-H", "Accept: application/vnd.github+json",
                 "-H", "User-Agent: SkillCorpus-license-audit/1.0",
                 "--max-time", "45",
                 url],
                capture_output=True, text=True, timeout=60,
            )
        except subprocess.TimeoutExpired:
            return "SOURCE_NOT_FETCHED"
        body, _, status = r.stdout.rpartition("\n")
        status = status.strip()
        if status == "404":
            return "NO_LICENSE"
        if status in ("403", "429"):
            # rate-limited; backoff and retry (bounded)
            time.sleep(60)
            continue
        if status != "200":
            return "SOURCE_NOT_FETCHED"
        try:
            d = json.loads(body)
            spdx = (d.get("license") or {}).get("spdx_id") or "NOASSERTION"
            return spdx if spdx and spdx != "NOASSERTION" else "Custom"
        except Exception:
            return "SOURCE_NOT_FETCHED"
    # exhausted rate-limit retries
    return "SOURCE_NOT_FETCHED"


def cmd_refresh(args: argparse.Namespace) -> int:
    """Fetch license for DB sources missing from CSV (or with stale entries)."""
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if not token:
        print("ERROR: set GITHUB_TOKEN env var (PAT scope: public_repo)",
              file=sys.stderr)
        return 1

    csv_path = Path(args.csv)
    db_path = Path(args.db)

    # 1. load existing CSV mapping
    existing = _load_csv_map(csv_path)
    print(f"existing CSV rows : {len(existing):,}  ({csv_path.name})")

    # 2. unmapped sources — ALL non-deleted, NOT just active=1. License probing
    #    must run BEFORE activation: a new source enters the DB at active=0
    #    (store-side GREEN-whitelist gate), and only a GREEN license result can
    #    promote it. Filtering to active=1 here deadlocked new sources at
    #    active=0 forever (never probed -> never in CSV -> never whitelisted).
    con = sqlite3.connect(db_path)
    sources = list(con.execute(
        "SELECT source, COUNT(*) c FROM skills WHERE deleted=0 "
        "GROUP BY source ORDER BY c DESC"
    ))
    if args.source:
        sources = [(s, c) for s, c in sources if s == args.source]
    if args.refresh_all:
        unmapped = sources
    else:
        unmapped = [(s, c) for s, c in sources if s not in existing]
    print(f"to fetch          : {len(unmapped):,}  "
          f"({sum(c for _, c in unmapped):,} rows covered)")

    if args.dry_run:
        for src, c in unmapped[:20]:
            print(f"  [dry] {src[:55]:<55} {c:>4}")
        if len(unmapped) > 20:
            print(f"  ... +{len(unmapped) - 20} more")
        return 0

    if not unmapped:
        print("nothing to do.")
        return 0

    # 3. parallel fetch + append CSV under lock
    write_header = not csv_path.exists()
    f_csv = open(csv_path, "a")
    wr = csv.writer(f_csv)
    if write_header:
        wr.writerow(["license_category", "category_total_active", "source",
                     "skill_count_active", "license_file", "repo_dir"])
    csv_lock = threading.Lock()
    new_rows: list[tuple[str, int, str]] = []

    def worker(item: tuple[int, str, int]) -> str:
        i, src, c = item
        cat = _gh_fetch_license(src, token)
        with csv_lock:
            existing[src] = cat
            new_rows.append((src, c, cat))
            wr.writerow([cat, 0, src, c, "", src])
            f_csv.flush()
            print(f"  [{i:>4}/{len(unmapped):>4}] {src[:55]:<55} {c:>4}  → {cat}",
                  flush=True)
        return cat

    items = [(i + 1, src, c) for i, (src, c) in enumerate(unmapped)]
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        list(pool.map(worker, items))
    f_csv.close()

    print(f"\nappended {len(new_rows):,} rows to {csv_path}")
    return 0


# ---------------------------------------------------------------------------
# build — CSV → license_safe_sources.json
# ---------------------------------------------------------------------------
def cmd_build(args: argparse.Namespace) -> int:
    """Regenerate license_safe_sources.json from CSV (GREEN-only filter)."""
    csv_path = Path(args.csv)
    json_path = Path(args.out)
    if not csv_path.exists():
        print(f"ERROR: CSV not found: {csv_path}", file=sys.stderr)
        return 1

    green_sources = sorted({
        src for src, cat in _load_csv_map(csv_path).items()
        if cat in GREEN_LICENSES
    })

    payload = {
        "green_categories": sorted(GREEN_LICENSES),
        "sources": green_sources,
    }
    if args.dry_run:
        print(f"[dry] would write {len(green_sources):,} GREEN sources to {json_path}")
        print(f"      green_categories = {payload['green_categories']}")
        return 0

    json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    print(f"wrote {len(green_sources):,} GREEN sources to {json_path}")
    return 0


# ---------------------------------------------------------------------------
# validate — consistency checks across CSV / JSON / DB
# ---------------------------------------------------------------------------
def cmd_validate(args: argparse.Namespace) -> int:
    """Cross-check CSV ↔ JSON ↔ DB and report mismatches."""
    csv_path = Path(args.csv)
    json_path = Path(args.json)
    db_path = Path(args.db)

    if not csv_path.exists():
        print(f"ERROR: CSV not found: {csv_path}", file=sys.stderr)
        return 1
    if not json_path.exists():
        print(f"ERROR: JSON not found: {json_path}", file=sys.stderr)
        return 1

    csv_map = _load_csv_map(csv_path)

    # Load JSON
    payload = json.loads(json_path.read_text())
    json_sources = set(payload.get("sources", []))
    json_cats = set(payload.get("green_categories", []))

    issues: list[str] = []

    # Check 1: every JSON source must be GREEN per CSV
    for src in sorted(json_sources):
        cat = csv_map.get(src)
        if cat is None:
            issues.append(f"  JSON has source not in CSV: {src}")
        elif cat not in GREEN_LICENSES:
            issues.append(f"  JSON has non-GREEN source: {src} (cat={cat})")

    # Check 2: every GREEN CSV source must be in JSON
    csv_greens = {s for s, c in csv_map.items() if c in GREEN_LICENSES}
    missing = csv_greens - json_sources
    for src in sorted(missing):
        issues.append(f"  CSV GREEN source missing from JSON: {src} (cat={csv_map[src]})")

    # Check 3: green_categories matches GREEN_LICENSES
    if json_cats != GREEN_LICENSES:
        only_json = json_cats - GREEN_LICENSES
        only_code = GREEN_LICENSES - json_cats
        if only_json:
            issues.append(f"  JSON green_categories has extras: {sorted(only_json)}")
        if only_code:
            issues.append(f"  JSON green_categories missing: {sorted(only_code)}")

    # Check 4: every active source in DB has a CSV entry
    con = sqlite3.connect(db_path)
    db_sources = {s for (s,) in con.execute(
        "SELECT DISTINCT source FROM skills WHERE deleted=0 AND active=1"
    )}
    unmapped_db = db_sources - set(csv_map.keys())
    for src in sorted(unmapped_db):
        n = con.execute(
            "SELECT COUNT(*) FROM skills WHERE deleted=0 AND active=1 AND source=?",
            (src,),
        ).fetchone()[0]
        issues.append(f"  DB active source missing from CSV: {src} ({n} rows)")

    # Check 5: every active skill row has license set to a sensible value
    junk_count = con.execute(
        "SELECT COUNT(*) FROM skills WHERE deleted=0 AND active=1 "
        "AND (license IS NULL OR license='' OR license IN ('Unknown','LICENSE'))"
    ).fetchone()[0]
    if junk_count:
        issues.append(f"  DB has {junk_count} active rows with junk license string")

    if not issues:
        print("validate OK — CSV ↔ JSON ↔ DB consistent")
        print(f"  CSV rows                 : {len(csv_map):,}")
        print(f"  JSON GREEN sources       : {len(json_sources):,}")
        print(f"  DB active sources        : {len(db_sources):,}")
        print(f"  green_categories         : {len(json_cats)}")
        return 0

    print(f"validate FAILED — {len(issues)} issue(s):")
    for s in issues[:50]:
        print(s)
    if len(issues) > 50:
        print(f"  ... +{len(issues) - 50} more")
    return 1


# ---------------------------------------------------------------------------
# apply — CSV → DB (backfill skills.license)
# ---------------------------------------------------------------------------
def cmd_apply(args: argparse.Namespace) -> int:
    """Backfill `skills.license` from CSV source-level mapping.

    Only updates rows where the per-skill license is junk — NULL, empty,
    'Unknown', or a bare LICENSE filename (members of ``_JUNK_LIC_STRINGS``).
    Per-skill frontmatter license values (including short ones like 'MIT',
    'ISC', 'BSD', 'WTFPL') are NOT overwritten.
    """
    csv_path = Path(args.csv)
    db_path = Path(args.db)

    if not csv_path.exists():
        print(f"ERROR: CSV not found: {csv_path}", file=sys.stderr)
        return 1

    csv_map = _load_csv_map(csv_path)

    con = sqlite3.connect(db_path)
    updates: list[tuple[str, str]] = []
    for src, lic_str in con.execute(
        "SELECT source, license FROM skills WHERE deleted=0 AND active=1"
    ):
        cur_lic = (lic_str or "").strip()
        # keep any valid declaration the skill provided; only overwrite
        # actual junk (empty / Unknown / LICENSE filename)
        if cur_lic and cur_lic not in _JUNK_LIC_STRINGS:
            continue
        cat = csv_map.get(src)
        # Only backfill an actual license id; skip status categories
        # (NO_LICENSE / SOURCE_NOT_FETCHED / Custom / NOASSERTION) so they
        # never masquerade as a license on the skill / consumer side.
        if cat and cat not in _NON_LICENSE_CATEGORIES:
            updates.append((cat, src))

    print(f"would update {len(updates):,} rows (junk → CSV category)")

    if args.dry_run:
        # show sample
        sample = updates[:20]
        for cat, src in sample:
            print(f"  [dry] {src[:55]:<55} → {cat}")
        return 0

    if not updates:
        return 0

    # Build placeholders for IN-clause matching the canonical junk set
    junk_list = sorted(_JUNK_LIC_STRINGS - {""})  # SQL handles '' via license=''
    junk_q = ",".join("?" for _ in junk_list)
    cur = con.cursor()
    cur.executemany(
        "UPDATE skills SET license=? WHERE source=? AND active=1 "
        f"AND (license IS NULL OR license='' OR license IN ({junk_q}))",
        [(cat, src, *junk_list) for cat, src in updates],
    )
    con.commit()
    print(f"updated {cur.rowcount:,} rows")
    return 0


# ---------------------------------------------------------------------------
# activate — align skills.active with the GREEN whitelist (upgrade backfill)
# ---------------------------------------------------------------------------
def cmd_activate(args: argparse.Namespace) -> int:
    """Align the skills.active column with the GREEN whitelist (active=1 iff source ∈ whitelist, deleted=0).

    Purpose: store.insert sets active per GREEN only for **newly inserted** rows; when an
    old DB is upgraded, the active column added by `_migrate` defaults to all 0, and no path
    sets existing GREEN rows back to 1, so after the upgrade export (WHERE active=1) produces
    an empty library. Run this command once after an upgrade to fix it.
    Idempotent: rerunning yields the same result. The whitelist comes from license_safe_sources.json (same source as store).
    """
    json_path = Path(args.json)
    db_path = Path(args.db)
    if not json_path.exists():
        print(f"ERROR: whitelist JSON not found: {json_path} (run build first)", file=sys.stderr)
        return 1
    green_sources = set(json.loads(json_path.read_text(encoding="utf-8")).get("sources", []))
    if not green_sources:
        print("ERROR: whitelist is empty; refusing to clear active to 0 for the whole library", file=sys.stderr)
        return 1

    con = sqlite3.connect(db_path)
    before = con.execute("SELECT COUNT(*) FROM skills WHERE active=1 AND deleted=0").fetchone()[0]
    con.execute("CREATE TEMP TABLE _green(source TEXT PRIMARY KEY)")
    con.executemany("INSERT OR IGNORE INTO _green VALUES (?)", [(s,) for s in green_sources])
    # Activate only: rows from GREEN sources currently having active!=1. **Deactivate** no rows
    # (non-GREEN rows with active=0 stay 0; non-GREEN rows already at active=1 are left alone) — to
    # avoid retroactively deactivating existing rows.
    to_activate = con.execute(
        "SELECT COUNT(*) FROM skills WHERE deleted=0 AND active!=1 "
        "AND source IN (SELECT source FROM _green)"
    ).fetchone()[0]
    print(f"whitelist GREEN sources: {len(green_sources):,}")
    print(f"to activate (GREEN and currently not active): {to_activate:,}  (current active=1 {before:,})")

    if args.dry_run:
        con.execute("DROP TABLE _green")
        print("(dry-run, nothing written)")
        return 0

    cur = con.cursor()
    cur.execute(
        "UPDATE skills SET active=1 WHERE deleted=0 AND active!=1 "
        "AND source IN (SELECT source FROM _green)"
    )
    con.execute("DROP TABLE _green")
    con.commit()
    after = con.execute("SELECT COUNT(*) FROM skills WHERE active=1 AND deleted=0").fetchone()[0]
    print(f"done: active=1 {before:,} → {after:,} (newly activated {cur.rowcount:,} rows, no rows deactivated)")
    return 0


# ---------------------------------------------------------------------------
# stats — show distribution
# ---------------------------------------------------------------------------
def cmd_stats(args: argparse.Namespace) -> int:
    """Print license distribution across CSV (source-level) and DB (skill-level)."""
    csv_path = Path(args.csv)
    db_path = Path(args.db)

    # source-level (CSV)
    csv_dist: dict[str, int] = {}
    for cat in _load_csv_map(csv_path).values():
        csv_dist[cat] = csv_dist.get(cat, 0) + 1

    # skill-level (DB)
    con = sqlite3.connect(db_path)
    db_dist: dict[str, int] = {}
    for lic, n in con.execute(
        "SELECT license, COUNT(*) FROM skills WHERE deleted=0 AND active=1 "
        "GROUP BY license ORDER BY 2 DESC"
    ):
        db_dist[lic or "(null)"] = n

    def tag(cat: str) -> str:
        if cat in GREEN_LICENSES:  return "GREEN"
        if cat in RED_LICENSES:    return "RED"
        if cat in YELLOW_LICENSES: return "YELLOW"
        if cat in _NON_LICENSE_CATEGORIES:
            return "MISSING"
        return "OTHER"

    print(f"=== source-level (CSV: {csv_path.name}) ===")
    total = sum(csv_dist.values())
    for cat, n in sorted(csv_dist.items(), key=lambda x: -x[1]):
        pct = 100 * n / total if total else 0.0
        print(f"  [{tag(cat):<7}] {cat:<30} {n:>6}  ({pct:.1f}%)")
    print(f"  total sources : {total:,}")

    print(f"\n=== skill-level (DB: {db_path.name}, active=1) ===")
    total = sum(db_dist.values())
    for lic, n in list(db_dist.items())[:25]:
        norm = normalize_license(lic) or lic
        pct = 100 * n / total if total else 0.0
        print(f"  [{tag(norm):<7}] {lic:<30} {n:>6}  ({pct:.1f}%)")
    if len(db_dist) > 25:
        rest = sum(list(db_dist.values())[25:])
        print(f"  ... +{len(db_dist) - 25} more categories ({rest:,} rows)")
    print(f"  total active : {total:,}")

    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m skill_library.license_audit",
        description="Single-entry license maintenance for SkillCorpus.",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    def _add_common(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--db", default=str(DEFAULT_DB),
                        help=f"DB path (default: {DEFAULT_DB})")
        sp.add_argument("--csv", default=str(DEFAULT_CSV),
                        help=f"CSV path (default: {DEFAULT_CSV})")

    # refresh
    sp = sub.add_parser("refresh", help="GitHub API → CSV (incremental)")
    _add_common(sp)
    sp.add_argument("--workers", type=int, default=8)
    sp.add_argument("--source", help="only refresh this one source")
    sp.add_argument("--refresh-all", action="store_true",
                    help="re-fetch even sources already in CSV")
    sp.add_argument("--dry-run", action="store_true")
    sp.set_defaults(func=cmd_refresh)

    # build
    sp = sub.add_parser("build", help="CSV → license_safe_sources.json")
    sp.add_argument("--csv", default=str(DEFAULT_CSV))
    sp.add_argument("--out", default=str(DEFAULT_JSON))
    sp.add_argument("--dry-run", action="store_true")
    sp.set_defaults(func=cmd_build)

    # validate
    sp = sub.add_parser("validate", help="cross-check CSV ↔ JSON ↔ DB")
    _add_common(sp)
    sp.add_argument("--json", default=str(DEFAULT_JSON))
    sp.set_defaults(func=cmd_validate)

    # apply
    sp = sub.add_parser("apply", help="backfill DB skills.license from CSV")
    _add_common(sp)
    sp.add_argument("--dry-run", action="store_true")
    sp.set_defaults(func=cmd_apply)

    # activate (upgrade backfill: align the active column with the GREEN whitelist)
    sp = sub.add_parser("activate",
                        help="align skills.active with the GREEN whitelist (must be run after an old DB upgrade, otherwise export is empty)")
    _add_common(sp)
    sp.add_argument("--json", default=str(DEFAULT_JSON))
    sp.add_argument("--dry-run", action="store_true")
    sp.set_defaults(func=cmd_activate)

    # stats
    sp = sub.add_parser("stats", help="show license distribution")
    _add_common(sp)
    sp.set_defaults(func=cmd_stats)

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
