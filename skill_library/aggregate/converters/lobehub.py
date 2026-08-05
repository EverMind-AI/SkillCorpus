"""Convert lobehub/lobe-chat-agents JSON → Claude SKILL.md tree.

Source format (one .json per agent):
    {
      "identifier": "<slug>",
      "author": "<github user>",
      "homepage": "https://...",
      "config": {
        "systemRole": "<long prompt>",
        "openingMessage": "...",
        "openingQuestions": ["..."]
      },
      "examples": [{"role": "user|assistant", "content": "..."}, ...],
      "meta": {
        "title": "Human-readable title",
        "description": "...",
        "tags": [...],
        "avatar": "..."
      }
    }

Output: <out_dir>/<identifier>/SKILL.md with standard frontmatter +
systemRole as body. examples appended as "## Examples" section.

I18n note: src/ has both `<id>.json` (English primary) and `<id>.zh-CN.json`
(Chinese translation). We dedup by identifier — main wins; zh-CN-only entries
(no English counterpart) are kept too.

Usage:
    python -m skill_library.aggregate.converters.lobehub \\
        --src /tmp/lobe-lobe-chat-agents/src \\
        --out /tmp/lobehub-converted
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))


def _slugify(s: str) -> str:
    """SKILL name must be lowercase + hyphen + alnum."""
    s = re.sub(r"[^a-z0-9-]+", "-", s.lower())
    s = re.sub(r"-+", "-", s).strip("-")
    return s or "unknown"


def _yaml_str(s: str) -> str:
    """Quote a string for YAML, escaping safely."""
    s = s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ").strip()
    return f'"{s}"'


def _yaml_list(items) -> str:
    if not items:
        return "[]"
    return "[" + ", ".join(_yaml_str(str(x)) for x in items) + "]"


def _build_skill_md(rec: dict) -> tuple[str, str]:
    """Returns (skill_name_slug, skill_md_text)."""
    identifier = (rec.get("identifier") or "").strip()
    name = _slugify(identifier)
    meta = rec.get("meta") or {}
    title = (meta.get("title") or identifier or "").strip()
    desc_raw = (meta.get("description") or "").strip()
    # SKILL frontmatter description hard-cap 1024 (we only validate; producer
    # will truncate further if needed). Combine title + description so the
    # title isn't lost.
    description = f"{title}: {desc_raw}" if desc_raw and desc_raw != title else (title or desc_raw)
    description = description[:1020].rstrip().rstrip(":").rstrip() or "(no description)"

    tags = meta.get("tags") or []
    cfg = rec.get("config") or {}
    system_role = (cfg.get("systemRole") or "").strip()
    opening_msg = (cfg.get("openingMessage") or "").strip()
    examples = rec.get("examples") or []
    author = (rec.get("author") or "").strip()
    homepage = (rec.get("homepage") or "").strip()

    # Frontmatter
    fm_lines = [
        "---",
        f"name: {name}",
        f"description: {_yaml_str(description)}",
        f"tags: {_yaml_list(tags)}",
        "license: MIT",
    ]
    if author:
        fm_lines.append(f'author: {_yaml_str(author)}')
    if homepage:
        fm_lines.append(f'homepage: {_yaml_str(homepage)}')
    fm_lines.append("---")

    # Body — system role is the main content
    body_parts = [f"# {title or name}", ""]
    if system_role:
        body_parts.extend(["## System Prompt", "", system_role, ""])
    if opening_msg:
        body_parts.extend(["## Opening Message", "", opening_msg, ""])
    if examples:
        body_parts.extend(["## Examples", ""])
        for ex in examples:
            role = ex.get("role", "?")
            content = (ex.get("content") or "").strip()
            if content:
                body_parts.append(f"**{role}:** {content}")
                body_parts.append("")

    return name, "\n".join(fm_lines) + "\n\n" + "\n".join(body_parts).rstrip() + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", type=Path, required=True,
                    help="lobe-chat-agents/src dir containing *.json files")
    ap.add_argument("--out", type=Path, required=True,
                    help="output dir to receive <identifier>/SKILL.md tree")
    args = ap.parse_args()

    if not args.src.is_dir():
        print(f"!! src not found: {args.src}", file=sys.stderr)
        return 1
    args.out.mkdir(parents=True, exist_ok=True)

    files = sorted(args.src.glob("*.json"))
    print(f"scanning {len(files)} JSON files in {args.src}")

    # Dedup: prefer English primary (identifier.json) over zh-CN
    # (identifier.zh-CN.json). Build {identifier: chosen_path}.
    chosen: dict[str, Path] = {}
    for f in files:
        # Strip locale suffix to get identifier
        stem = f.stem
        # Match common locale codes at end: .zh-CN, .de, .fr, .es, .ja, .ko, .pt-BR, .ru, .it
        m = re.match(r"^(.+)\.(zh-CN|en-US|de|fr|es|ja|ko|pt-BR|ru|it|tr|vi|ar)$", stem)
        ident = m.group(1) if m else stem
        if ident in chosen:
            # Keep the one without locale suffix (English primary).
            existing = chosen[ident]
            if not re.match(r"^.+\.(zh-CN|en-US|de|fr|es|ja|ko|pt-BR|ru|it|tr|vi|ar)$",
                            existing.stem):
                continue  # existing is primary, keep it
        chosen[ident] = f
    print(f"after i18n dedup: {len(chosen)} unique agents")

    written = 0
    skipped = 0
    errors = 0
    for ident, src_path in sorted(chosen.items()):
        try:
            rec = json.loads(src_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            errors += 1
            continue
        # Need at least systemRole + meta.title or meta.description
        cfg = rec.get("config") or {}
        meta = rec.get("meta") or {}
        if not (cfg.get("systemRole") or "").strip():
            skipped += 1
            continue
        if not ((meta.get("title") or "").strip() or (meta.get("description") or "").strip()):
            skipped += 1
            continue

        name, md_text = _build_skill_md(rec)
        out_dir = args.out / name
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "SKILL.md").write_text(md_text, encoding="utf-8")
        written += 1

    print(f"\nwritten: {written}, skipped (missing required): {skipped}, errors: {errors}")
    print(f"output tree: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
