"""Deliver a skill's bundled attachment files (scripts/references/assets) into
the agent's Docker container.

Background
----------
Skill injection puts only the SKILL.md *body text* into the prompt. But many
skills ship bundled resources on disk — e.g. `scripts/mesh_tool.py`,
`references/*.md` — and the body text refers to them ("use scripts/foo.py",
"see references/bar.md"). If those files never reach the container, the agent
follows instructions that point at non-existent paths and fails.

This module resolves each injected skill's source directory from the
mass-library DB and `docker cp`s its clean attachment files into the container
under `/skills/<name>/` (see CONTAINER_SKILL_ROOT below for why that path).
"""
import os
import shutil
import sqlite3
import subprocess
import tempfile
from pathlib import Path

# Roots where skill source dirs live on disk (DB `path` is tried first, these
# are the fallback resolved as <root>/<source>/<name>/).
SKILL_ATTACH_ROOTS = [p for p in [os.environ.get("SKILL_ATTACH_ROOT")] if p]
# The mass-library DB may be referenced under different mount prefixes across
# hosts; resolve to whichever copy is actually readable.
MASS_DB_CANDIDATES = [p for p in [os.environ.get("MASS_LIBRARY_DB")] if p]


def _resolve_mass_db(mass_db):
    """Return a readable mass-library DB path, trying the given path then known
    mount-prefix alternatives. None if nothing readable."""
    cands = []
    if mass_db:
        cands.append(str(mass_db))
        # try swapping the mount prefix of the given path
        base = os.path.basename(str(mass_db))
        cands.extend(c for c in MASS_DB_CANDIDATES if os.path.basename(c) == base)
    cands.extend(MASS_DB_CANDIDATES)
    for c in cands:
        if c and Path(c).exists():
            return c
    return None
SKILL_META_FILES = {"SKILL.md", "skill.json", "_meta.json"}
SKILL_JUNK_DIRS = {
    "node_modules", ".git", "__pycache__", ".venv", "venv", "site-packages",
    ".egg-info", "dist-info", ".pytest_cache", "build", ".mypy_cache",
}
SKILL_JUNK_EXT = {".pyc", ".pyo", ".so", ".pyd", ".o", ".class", ".lock"}
SKILL_MAX_ATTACH = 60          # cap per skill — guard against vendored blobs
# Where bundled skill files are placed so the agent can read them — NOT a
# Claude-Code path. Use a container ROOT-level /skills/<name>/ so it sits OUTSIDE
# every SkillsBench task's grading scan roots (tasks rglob /root, /app, and cwd;
# none scan / or /skills), keeping skill/noskill arms grading-comparable. For
# local (non-Docker) runners the caller passes its own dest_root explicitly.
CONTAINER_SKILL_ROOT = "/skills"

# Bridging note appended to the injected skill section so the agent maps the
# skill bodies' (wildly inconsistent) relative paths to where files actually live.
def skill_resources_note(root):
    """root: the directory under which each skill's bundled files were placed,
    e.g. 'skills' (relative to the agent's working dir) or '/app/skills'."""
    root = str(root).rstrip("/")
    return (
        "\n\n**Bundled skill resources:** Each skill above may ship files "
        "(scripts / references / assets). They are on disk under "
        f"`{root}/<skill-name>/`, one folder per skill (named by the skill). "
        "When a skill body refers to a relative path such as `references/foo.md` "
        "or `scripts/bar.py`, read it from that skill's folder — e.g. "
        f"`{root}/<skill-name>/references/foo.md`.\n"
    )


SKILL_CODE_EXT = {".py", ".sh", ".js", ".ts", ".rb", ".pl", ".r"}


def skill_scripts_manifest(skills, mass_db, root):
    """Return a markdown block listing the runnable script files that were
    injected for each skill, with concrete container paths, so the agent knows
    exactly what it can run. `skills` is an iterable of name or (name, body).
    Returns "" if no runnable scripts were found.
    """
    root = str(root).rstrip("/")
    mass_db = _resolve_mass_db(mass_db)
    if not mass_db:
        return ""
    lines = []
    for item in skills:
        if isinstance(item, (tuple, list)):
            name = item[0]; body = item[1] if len(item) > 1 else ""
        else:
            name, body = item, ""
        if not name:
            continue
        src = _resolve_skill_dir(name, mass_db, body=body)
        if src is None:
            continue
        files = _gather_attachments(src)
        scripts = [f for f in files if f.suffix.lower() in SKILL_CODE_EXT]
        if not scripts:
            continue
        for f in scripts:
            rel = f.relative_to(src)
            lines.append(f"- `{root}/{name}/{rel}`")
    if not lines:
        return ""
    return (
        "\n\n**Runnable scripts available for this task** (prefer these over "
        "writing your own):\n" + "\n".join(lines) + "\n"
    )


def _resolve_skill_dir(name, mass_db, body=""):
    """Return the on-disk source directory for a skill, or None.

    A skill name can be non-unique (multiple sources). When `body` is given we
    disambiguate by matching the DB body (exact, then 500-char prefix) so we
    copy the SAME skill whose body was injected. Falls back to the only row.
    """
    if not mass_db or not Path(mass_db).exists():
        return None
    try:
        conn = sqlite3.connect(mass_db)
        rows = conn.execute(
            "SELECT source, path, body FROM skills WHERE name = ?", (name,)
        ).fetchall()
        conn.close()
    except Exception:
        return None
    rows = [r for r in rows if r]
    if not rows:
        return None
    if len(rows) == 1 or not (body or "").strip():
        source, path = rows[0][0], rows[0][1]
    else:
        bnorm = body.strip()
        chosen = None
        for src, path, db_body in rows:
            if (db_body or "").strip() == bnorm:
                chosen = (src, path); break
        if chosen is None:
            for src, path, db_body in rows:
                if (db_body or "").strip()[:500] == bnorm[:500]:
                    chosen = (src, path); break
        if chosen is None:
            chosen = (rows[0][0], rows[0][1])
        source, path = chosen
    # path column points at .../<source>/<name>/SKILL.md
    if path:
        d = Path(path).parent
        if d.is_dir():
            return d
    if source:
        for root in SKILL_ATTACH_ROOTS:
            cand = Path(root) / source / name
            if cand.is_dir():
                return cand
    return None


def _gather_attachments(src_dir):
    """Clean attachment files under src_dir (skip meta/junk). [] if none/too many."""
    files = []
    for f in src_dir.rglob("*"):
        if not f.is_file() or f.name in SKILL_META_FILES:
            continue
        if any(j in f.parts for j in SKILL_JUNK_DIRS):
            continue
        if f.suffix.lower() in SKILL_JUNK_EXT:
            continue
        files.append(f)
    return files


def inject_skill_attachments_local(skills, mass_db, dest_root, log=None):
    """Local-filesystem variant of inject_skill_attachments (no Docker).

    Copies each injected skill's bundled attachments into `dest_root/<name>/`
    on the local filesystem — for harnesses where the agent runs locally
    instead of in Docker. `dest_root` MUST be the agent's working-dir skill
    folder, i.e. `<workspace>/skills`, so paths anchored on `skills/<name>/`
    resolve from the agent's cwd. Returns the total number of files copied.
    """
    def _log(msg):
        if log is not None:
            log.info(msg)

    mass_db = _resolve_mass_db(mass_db)
    if not mass_db:
        return 0

    total = 0
    for item in skills:
        if isinstance(item, (tuple, list)):
            name = item[0]
            body = item[1] if len(item) > 1 else ""
        else:
            name, body = item, ""
        if not name:
            continue
        src = _resolve_skill_dir(name, mass_db, body=body)
        if src is None:
            continue
        files = _gather_attachments(src)
        if not files:
            continue
        if len(files) > SKILL_MAX_ATTACH:
            _log(f"skill {name}: {len(files)} attachment files (>{SKILL_MAX_ATTACH}) — skipping")
            continue
        dest = Path(dest_root) / name
        dest.mkdir(parents=True, exist_ok=True)
        for f in files:
            rel = f.relative_to(src)
            dst = dest / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            try:
                shutil.copy2(f, dst)
                total += 1
            except Exception:
                pass
        _log(f"skill {name}: injected {len(files)} attachment file(s) -> {dest}")
    return total


def inject_skill_attachments(cid, skills, mass_db, log=None):
    """Copy bundled attachments of each injected skill into the container.

    Parameters
    ----------
    cid : str          Docker container id.
    skills : iterable  Each item is a skill name (str) or a (name, body) tuple.
                       Body, when present, disambiguates same-named skills.
    mass_db : str      Path to the mass-library SQLite DB.

    Returns the total number of attachment files copied. Files land at
    `/skills/<name>/` (CONTAINER_SKILL_ROOT), preserving subdir structure.
    """
    def _log(msg):
        if log is not None:
            log.info(msg)

    mass_db = _resolve_mass_db(mass_db)
    if not mass_db:
        return 0

    subprocess.run(["docker", "exec", cid, "mkdir", "-p", CONTAINER_SKILL_ROOT],
                   timeout=30, check=False, capture_output=True)

    total = 0
    for item in skills:
        if isinstance(item, (tuple, list)):
            name = item[0]
            body = item[1] if len(item) > 1 else ""
        else:
            name, body = item, ""
        if not name:
            continue
        src = _resolve_skill_dir(name, mass_db, body=body)
        if src is None:
            continue
        files = _gather_attachments(src)
        if not files:
            continue
        if len(files) > SKILL_MAX_ATTACH:
            _log(f"skill {name}: {len(files)} attachment files (>{SKILL_MAX_ATTACH}) "
                 f"— skipping to avoid container pollution")
            continue
        # Stage clean files into a temp dir mirroring structure, then docker cp.
        stage = Path(tempfile.mkdtemp(prefix="_skillattach_"))
        try:
            for f in files:
                rel = f.relative_to(src)
                dst = stage / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                try:
                    shutil.copy2(f, dst)
                except Exception:
                    pass
            dest = f"{CONTAINER_SKILL_ROOT}/{name}"
            subprocess.run(["docker", "exec", cid, "mkdir", "-p", dest],
                           timeout=30, check=False, capture_output=True)
            r = subprocess.run(["docker", "cp", f"{stage}/.", f"{cid}:{dest}"],
                               timeout=120, check=False, capture_output=True)
            if r.returncode == 0:
                total += len(files)
                _log(f"skill {name}: injected {len(files)} attachment file(s) -> {dest}")
            else:
                _log(f"skill {name}: docker cp failed: {r.stderr.decode()[:200]}")
        finally:
            shutil.rmtree(stage, ignore_errors=True)
    return total
