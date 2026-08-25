"""Skill body ref-path resolution.

Replaces `{baseDir}/x` placeholders and markdown links to bundled files
(``references/``, ``scripts/``, ``assets/``, ``examples/``) with absolute
paths rooted at the skill's directory. Used by both the active-skills
local render path and the router-hits render path (the post-gate
hydrate step) so the
two flows produce identical bodies.

Resolution is per-ref existence-checked: a ``{baseDir}/x`` whose target
is missing on disk is left literal rather than handed to the agent as a
confident 404. Code fences are skipped entirely so example markup is not
silently mutated.
"""

from __future__ import annotations

import re
from pathlib import Path

_BUNDLED_DIRS = ("references", "scripts", "assets", "examples")

_MD_LINK_RE = re.compile(
    r"\[([^\]]+)\]\((?:\.{0,2}/)?"
    rf"((?:{'|'.join(_BUNDLED_DIRS)})/[^)\s]+)\)"
)
_BASE_DIR_REF_RE = re.compile(r"\{baseDir\}/(\S+?)(?=[\s)\'\"`]|$)")
_BARE_BASE_DIR_RE = re.compile(r"\{baseDir\}(?!/)")
_CODE_FENCE_RE = re.compile(r"(```.*?```)", re.S)


def resolve_refs(body: str, skill_dir: Path | str | None) -> tuple[str, bool]:
    """Return ``(rewritten_body, any_resolved)``.

    ``skill_dir`` is the directory of ``SKILL.md`` — bundled files live
    under it (``<skill_dir>/references/x.md`` etc.). When ``None`` or
    not a real directory, the function strips ``{baseDir}/`` to bare
    relative paths and leaves markdown links alone — the agent then sees
    a bare ``references/x.md`` it can't auto-resolve but at least no
    nonsense literal ``{baseDir}/`` remains in the prompt.

    Returns ``any_resolved=True`` when at least one substitution
    materialized a real path on disk, so callers can decide whether to
    emit a "Skill directory: ..." hint header.
    """
    if not body:
        return "", False

    skill_path = Path(skill_dir) if skill_dir is not None else None
    has_dir = skill_path is not None and skill_path.is_dir()

    if not has_dir:
        if "{baseDir}" in body:
            body = body.replace("{baseDir}/", "").replace("{baseDir}", "")
        return body, False

    base_dir = str(skill_path)
    any_resolved = False

    def _md_sub(mo: re.Match[str]) -> str:
        nonlocal any_resolved
        rel = mo.group(2).rstrip(".,;:")
        cut = min((i for i in (rel.find("#"), rel.find("?")) if i != -1), default=-1)
        frag = rel[cut:] if cut != -1 else ""
        rel_file = rel[:cut] if cut != -1 else rel
        if rel_file and (skill_path / rel_file).exists():
            any_resolved = True
            return f"[{mo.group(1)}]({base_dir}/{rel_file}{frag})"
        return mo.group(0)

    segments = _CODE_FENCE_RE.split(body)
    body = "".join(seg if seg.startswith("```") else _MD_LINK_RE.sub(_md_sub, seg) for seg in segments)

    if "{baseDir}" in body:

        def _bd_sub(mo: re.Match[str]) -> str:
            nonlocal any_resolved
            ref = mo.group(1).rstrip(".,;:")
            if ref and (skill_path / ref).exists():
                any_resolved = True
                return f"{base_dir}/{mo.group(1)}"
            return mo.group(0)

        body = _BASE_DIR_REF_RE.sub(_bd_sub, body)
        # Use a function replacement, not a string: base_dir is a filesystem
        # path and on Windows contains backslashes that re.subn would otherwise
        # interpret as escape sequences (\U, \a, ...) → re.error "bad escape".
        body, bare_n = _BARE_BASE_DIR_RE.subn(lambda _m: base_dir, body)
        if bare_n:
            any_resolved = True

    return body, any_resolved


_PLACEHOLDER_RE = re.compile(r"\{\{([A-Z_]+)(?::([A-Za-z0-9._-]+))?\}\}")


def resolve_placeholders(
    body: str,
    skill_dir: str | None,
    *,
    state_dir: str | None = None,
    home_dir: str | None = None,
    output_dir: str | None = None,
) -> str:
    """Replace PathGuard placeholders in a skill body with real paths.

    ``{{SKILL_DIR}}`` / ``{{SKILL_DIR:<name>}}`` / ``{{AGENT_STATE_DIR}}`` /
    ``{{HOME}}`` / ``{{OUTPUT_DIR}}`` are written by an offline pass over a
    shared skill library (see ``skill_retire_and_classify/pathguard``) that
    rewrites hardcoded paths to agent-agnostic placeholders. This fills them
    in per agent, next to :func:`resolve_refs`.

    - ``{{SKILL_DIR}}`` → this skill's bundle directory; ``{{SKILL_DIR:<n>}}``
      → a sibling bundle under the same parent. ``<n>`` is restricted to a safe
      slug (``[A-Za-z0-9._-]+``), so a traversal like ``../../x`` or an absolute
      path does not match and stays literal.
    - ``{{AGENT_STATE_DIR}}`` → the agent's own config/state root, falling back
      to ``output_dir`` when this agent has none.
    - ``{{HOME}}`` → the agent's home, falling back to ``output_dir`` when unset.
    - ``{{OUTPUT_DIR}}`` → the agent's writable output directory.

    Substitution is unconditional — unlike :func:`resolve_refs` it never checks
    the filesystem, because a placeholder already carries its replacement target
    and only the host knows it.
    """
    if not body or "{{" not in body:
        return body

    sd = str(skill_dir) if skill_dir else None

    # Bare {{SKILL_DIR}} and {{SKILL_DIR}}/… — the slash is folded in so the
    # directory is written once. Without a directory on disk the placeholder is
    # left literal, not stripped: a bare `scripts/x.py` would tell the model the
    # bundle is present when it never downloaded.
    if sd:
        body = body.replace("{{SKILL_DIR}}/", sd.rstrip("/") + "/")
        body = body.replace("{{SKILL_DIR}}", sd)

    def _sub(m: re.Match[str]) -> str:
        name, arg = m.group(1), m.group(2)
        if name == "SKILL_DIR":  # {{SKILL_DIR:<name>}} — arg is a safe slug
            return (
                str(Path(sd).parent / arg)
                if sd and arg and arg not in {".", ".."}
                else m.group(0)
            )
        if name == "AGENT_STATE_DIR":
            return state_dir or output_dir or m.group(0)
        if name == "HOME":
            return home_dir or output_dir or m.group(0)
        if name == "OUTPUT_DIR":
            return output_dir or m.group(0)
        return m.group(0)

    return _PLACEHOLDER_RE.sub(_sub, body)


__all__ = ["resolve_placeholders", "resolve_refs"]
