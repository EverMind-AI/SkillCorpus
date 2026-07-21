"""rules.py — 纯规则阶段 (无 LLM): parse + safety + license。

三块: SKILL.md frontmatter 解析、safety 正则硬拦、license GREEN 闸。
"""
from __future__ import annotations

import csv
import re
from pathlib import Path
from typing import Any



# ======================== SKILL.md 解析 ========================
"""SKILL.md 解析: frontmatter (YAML) + body.

优先用 PyYAML (支持嵌套字段如 antigravity 的 metadata.openclaw);
fallback 到简单 line-by-line 解析 (兼容 OpenSpace skill_utils 风格).
"""



try:
    import yaml  # PyYAML
    _HAS_YAML = True
except ImportError:
    _HAS_YAML = False

_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\s*\n?(.*)", re.DOTALL)

SKILL_FILENAME = "SKILL.md"
SKILL_FILENAME_LOWER = "skill.md"


class ParseError(ValueError):
    """SKILL.md 解析失败."""


def parse_skill_md(content: str) -> tuple[dict[str, Any], str]:
    """解析 SKILL.md 文本, 返回 (frontmatter_dict, body).

    Body 不含 frontmatter 和分隔符.
    """
    if not content.lstrip().startswith("---"):
        raise ParseError("no frontmatter (content must start with '---')")

    # 确保从 --- 开始匹配
    content = content.lstrip()
    m = _FRONTMATTER_RE.match(content)
    if m is None:
        raise ParseError("malformed frontmatter (no closing '---')")

    fm_raw = m.group(1)
    body = m.group(2)

    if _HAS_YAML:
        try:
            fm = yaml.safe_load(fm_raw) or {}
        except yaml.YAMLError as e:
            raise ParseError(f"invalid YAML: {e}") from e
    else:
        fm = _simple_parse(fm_raw)

    if not isinstance(fm, dict):
        raise ParseError("frontmatter is not a dict")

    return fm, body


def _simple_parse(fm_raw: str) -> dict[str, Any]:
    """后备的朴素 key:value 解析 (仅一级)."""
    fm: dict[str, Any] = {}
    for line in fm_raw.split("\n"):
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        k = k.strip()
        v = v.strip()
        if (v.startswith('"') and v.endswith('"')) or (v.startswith("'") and v.endswith("'")):
            v = v[1:-1]
        if k:
            fm[k] = v
    return fm


def parse_skill_file(path: Path) -> tuple[dict[str, Any], str]:
    """从路径读并解析 SKILL.md."""
    content = path.read_text(encoding="utf-8", errors="replace")
    return parse_skill_md(content)


def find_skill_md(skill_dir: Path) -> Path | None:
    """在 skill_dir 里找 SKILL.md (大小写兼容)."""
    for name in (SKILL_FILENAME, SKILL_FILENAME_LOWER):
        candidate = skill_dir / name
        if candidate.is_file():
            return candidate
    return None


class ValidationError(ValueError):
    """Skill 元数据验证失败."""


def validate_skill(fm: dict[str, Any], body: str) -> None:
    """验证必填字段 (agentskills.io 规范).

    必填: name, description
    Raises: ValidationError
    """
    name = fm.get("name")
    if not name or not isinstance(name, str):
        raise ValidationError("missing or invalid 'name'")
    description = fm.get("description")
    if not description or not isinstance(description, str):
        raise ValidationError("missing or invalid 'description'")

    # name 规范: lowercase + digit + hyphen, ≤ 64
    if not re.fullmatch(r"[a-z0-9][a-z0-9\-]{0,63}", name):
        raise ValidationError(
            f"name '{name}' does not match slug spec (lowercase letters/digits/hyphens, ≤64)"
        )

    if len(description) > 1024:
        # agentskills.io 上限, 截断但不拒
        pass


# ======================== safety 正则硬拦 ========================
"""安全检查 — regex hard-block(只保留 1 条).

仅 `blocked.*` 类规则触发拒入库.

历史:之前还有 6 条 `suspicious.*`(keyword / secrets / crypto / webhook /
script / url_shortener)记为 audit flag.2026-05-21 跑了 stratified 100-skill
audit,LLM-judge 标 TP/FP,**overall FP rate = 98%**:
  - suspicious.secrets   命中 48,962 skill (45% 全库), precision 0% (20/20 FP)
  - suspicious.webhook                                  precision 0%
  - suspicious.script / .url_shortener / .keyword       precision 0%
  - suspicious.crypto                                   precision 10% (2/20)
全是 substring match,无 context 感知,LLM judge (3-dim + 19-flag) 已 cover
真实风险.故彻底删除.

剩余的细粒度 safety 判定由 LLM judge 完成,hard-gate 由 quality.py 处理:
  - 数字 hard-gate:LLM safety < 3 → quality = 0
  - flag hard-gate:5 个 (prompt_injection / cmd_injection / unsafe_exec /
                         auth_bypass / csam_risk) → quality = 0
"""



_SAFETY_RULES: list[tuple[str, re.Pattern]] = [
    ("blocked.malware", re.compile(r"(ClawdAuthenticatorTool)", re.IGNORECASE)),
]

_BLOCKING_FLAGS = frozenset({"blocked.malware"})


def check_safety(text: str) -> list[str]:
    """返回触发的 flag 名; 空列表表示安全."""
    return [flag for flag, pat in _SAFETY_RULES if pat.search(text)]


def is_blocked(flags: list[str]) -> bool:
    """有 blocked.* flag 即拒绝入库."""
    return any(f in _BLOCKING_FLAGS for f in flags)


# ======================== license GREEN 闸 ========================
"""License filter — enforce GREEN-only (commercially redistributable) active set.

Adds a hard-gate step on top of safety filtering: a skill is admitted
to the released `active` set only if its source repository's
GitHub-API `spdx_id` falls in the GREEN allow-list, OR its per-skill
licence string normalises to a GREEN identifier.

Use:
    from skill_library.rules import (
        is_green_license,
        normalize_license,
        load_source_license_map,
    )

    src_lic = load_source_license_map("source_license_report.csv")
    # ... during ingest, after LLM judging:
    if not is_green_license(record, src_lic):
        record.active = 0
        record.reason = "non-green license"

The GREEN allow-list mirrors the consumer-side mass-pool policy
(see docs/15_consumer_skill_库现状.md) and matches the paper's
released artifact.
"""



# OSI-approved permissive licences that allow commercial
# redistribution without copyleft, share-alike, or non-commercial
# restrictions.  This is the strict GREEN set used by the consumer-side
# mass pool and enforced in the released SkillCorpus active set.
GREEN_LICENSES = frozenset({
    "Apache-2.0",
    "BSD",
    "BSD-2-Clause",
    "BSD-3-Clause",
    "0BSD",
    "CC0-1.0",
    "ISC",
    "MIT",
    "MIT-0",
    "MPL-2.0",
    "Mulan-PSL-2.0",
    "Unlicense",
    "WTFPL",
})

# License strings that signal commercial-incompatible terms; explicit
# REJECT list for clarity (any unknown string also falls to NO_LICENSE).
RED_LICENSES = frozenset({
    "AGPL-1.0", "AGPL-3.0", "AGPL-3.0-only", "AGPL-3.0-or-later",
    "GPL-2.0", "GPL-3.0", "GPL-3.0-only", "GPL-3.0-or-later",
    "CC-BY-NC-4.0", "CC-BY-NC-SA-4.0", "CC-BY-ND-4.0",
    "FSL-1.1", "FSL-1.1-MIT", "FSL-1.1-Apache-2.0",
    "PolyForm-NC-1.0", "PolyForm-Noncommercial-1.0.0",
    "BUSL-1.1",
    "Proprietary",
})

YELLOW_LICENSES = frozenset({
    "LGPL-2.1", "LGPL-3.0", "LGPL-3.0-only",
    "CC-BY-SA-4.0", "CC-BY-4.0",
    "EPL-2.0",
})

_JUNK_LIC_STRINGS = frozenset({
    "", "Unknown", "unknown", "LICENSE", "LICENSE.txt", "LICENSE.md",
    "License", "License.txt", "License.md", "license", "license.txt",
})


def normalize_license(lic: str | None) -> str | None:
    """Normalize a free-form licence string to a canonical SPDX-style
    identifier, returning None for unparseable strings."""
    if not lic:
        return None
    s = lic.strip()
    if not s or s in _JUNK_LIC_STRINGS:
        return None
    sl = s.lower()
    if sl.startswith("complete terms in"):
        return None

    # MIT family
    if "mit-0" in sl:
        return "MIT-0"
    if re.match(r"^mit($|\s|\.|,|;)", sl) and "0" not in sl:
        return "MIT"
    if sl in ("mit", "mit license", "mit licence"):
        return "MIT"

    # Apache
    if sl in (
        "apache-2.0", "apache 2.0", "apache 2", "apache-2", "apache2",
        "apache-2.0 license", "apache 2.0 license", "apache license 2.0",
    ):
        return "Apache-2.0"
    if "apache" in sl and "2" in sl:
        return "Apache-2.0"

    # BSD / ISC / MPL / CC0 / Unlicense / WTFPL / Mulan
    if "bsd-3-clause" in sl or "3-clause bsd" in sl or "3 clause bsd" in sl:
        return "BSD-3-Clause"
    if "bsd-2-clause" in sl or "2-clause bsd" in sl:
        return "BSD-2-Clause"
    if sl == "0bsd":
        return "0BSD"
    if sl == "bsd":
        return "BSD"
    if sl == "isc":
        return "ISC"
    if "mpl-2.0" in sl or sl == "mpl":
        return "MPL-2.0"
    if "cc0" in sl or "cc-0" in sl:
        return "CC0-1.0"
    if "unlicense" in sl:
        return "Unlicense"
    if "wtfpl" in sl:
        return "WTFPL"
    if "mulan" in sl:
        return "Mulan-PSL-2.0"

    # Non-green
    if "agpl" in sl:
        return "AGPL-3.0"
    if "lgpl" in sl:
        return "LGPL-3.0"
    if "gpl" in sl:
        return "GPL-3.0"
    if "cc-by-nc" in sl or "cc by-nc" in sl:
        return "CC-BY-NC-4.0"
    if "cc-by-nd" in sl or "cc by-nd" in sl:
        return "CC-BY-ND-4.0"
    if "cc-by-sa" in sl or "cc by-sa" in sl:
        return "CC-BY-SA-4.0"
    if "cc-by" in sl or "cc by" in sl:
        return "CC-BY-4.0"
    if "proprietary" in sl or "private" in sl:
        return "Proprietary"
    if "fsl" in sl:
        return "FSL-1.1"
    if "polyform" in sl:
        return "PolyForm-NC-1.0"
    if "busl" in sl:
        return "BUSL-1.1"

    # Unknown — return None so caller falls back to source-level
    return None


def load_source_license_map(csv_path: str | Path) -> dict[str, str]:
    """Load source → license_category mapping from the enrichment CSV
    produced by `scripts/enrich_unmapped_licenses.py`."""
    out: dict[str, str] = {}
    with open(csv_path) as f:
        for row in csv.DictReader(f):
            out[row["source"]] = row["license_category"]
    return out


def is_green_license(
    record_license: str | None,
    source: str,
    source_license_map: dict[str, str],
) -> bool:
    """Return True iff the skill is commercially redistributable.

    Resolution order:
      1. Normalise the per-skill licence string.  If it resolves to a
         GREEN identifier, return True.
      2. Otherwise fall back to the source repository's GitHub
         `spdx_id` from `source_license_map`.  If that is GREEN, return
         True.
      3. Otherwise return False (NO_LICENSE / fetch failed / RED /
         YELLOW / Custom / unparseable).

    Note that an explicit NON-GREEN per-skill string overrides any
    source-level GREEN inference, to honour the most restrictive
    declaration.  If the per-skill string is parseable AND non-GREEN,
    return False regardless of source-level licence.
    """
    norm = normalize_license(record_license)
    if norm is not None:
        if norm in GREEN_LICENSES:
            return True
        # Explicit non-green per-skill declaration: reject even if source-green
        return False
    # Per-skill string unparseable → fall back to source-level
    src_lic = source_license_map.get(source)
    return src_lic in GREEN_LICENSES if src_lic else False

# 批量按 license 维护 skill 的 active 状态见 ``license_audit.py``。
