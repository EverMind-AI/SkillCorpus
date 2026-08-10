"""Data models + SQLite schema.

SkillRecord is adapted from OpenSpace's SkillRecord:
  - drops the 4 counters / lineage / evolution fields (no execution tracking or evolution)
  - adds library-management fields like source / category / tags / quality_score / safety_flags
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class Category(str, Enum):
    """Primary LLM category — 16 classes (including the OTHER fallback)."""
    # software development stack, 5 classes
    DEV = "DEV"
    FRONTEND_UI = "FRONTEND-UI"
    DEVOPS_INFRA = "DEVOPS-INFRA"
    TESTING = "TESTING"
    SECURITY = "SECURITY"

    # data / AI, 2 classes
    DATA = "DATA"
    AI_ML = "AI-ML"

    # authentication, 1 class
    AUTH = "AUTH"

    # content output, 4 classes
    DOC_PROC = "DOC-PROC"
    WRITING = "WRITING"
    MULTIMEDIA = "MULTIMEDIA"
    COMMS = "COMMS"

    # workflow / office, 2 classes
    WORKFLOW = "WORKFLOW"
    PRODUCTIVITY = "PRODUCTIVITY"

    # meta-tooling, 1 class
    META = "META"

    # fallback
    OTHER = "OTHER"


CATEGORIES: list[str] = [c.value for c in Category]


CATEGORY_DESCRIPTIONS: dict[str, str] = {
    "DEV":          "General coding / SaaS API integration / SDK wrappers / generic backend logic",
    "FRONTEND-UI":  "Frontend / mobile / UI components / design systems / visual layout",
    "DEVOPS-INFRA": "CI/CD / deployment / containers / k8s / cloud infrastructure / monitoring",
    "DATA":         "Structured/quantitative output — data engineering / ETL / databases / SQL / BI / analysis / reference DBs",
    "AI-ML":        "Deliverable is an AI system itself — agent / persona / multi-agent / RAG / training and inference",
    "TESTING":      "Software/hardware testing — unit / integration / E2E / fuzz / QA / debug / test planning",
    "SECURITY":     "Vulnerability scanning / pen-test / encryption / threat detection / audit / forensics",
    "AUTH":         "Authentication / authorization / OAuth / SSO / IAM / token / permission management",
    "DOC-PROC":     "Processing existing documents — parse, extract, convert pdf/docx/xlsx/pptx/md",
    "WRITING":      "Generating original prose — articles/emails/reports/strategy documents/advisory/summaries",
    "MULTIMEDIA":   "Image / video / audio generation or processing",
    "COMMS":        "Messaging channel integration — email/IM/Slack/Teams/Discord/DingTalk/WeChat",
    "WORKFLOW":     "Multi-step business workflows / playbooks / cross-step orchestration (not AI agent / not CI/CD)",
    "PRODUCTIVITY": "Single-task office work — scheduling/booking/admin/notes/single-step records",
    "META":         "Skill creation/management tooling — skill builders / registries / MCP servers",
    "OTHER":        "Pure lifestyle / academic / engineering long-tail — no fitting activity",
}


@dataclass
class SkillRecord:
    """Skill record — the library's core data model."""

    # --- identity ---
    skill_id: str                       # {source}__{name_slug}__{hash8}
    name: str                           # name from the frontmatter
    description: str                    # description from the frontmatter
    body: str                           # SKILL.md body (excluding frontmatter)
    frontmatter_raw: dict[str, Any] = field(default_factory=dict)

    # --- source ---
    source: str = ""                    # "anthropics" | "karanb192" | "clawhub" | ...
    source_url: str | None = None
    source_path: str = ""               # relative path within the original repo
    license: str | None = None

    # --- content hash (for dedup) ---
    content_hash: str = ""              # SHA-256 (normalized body)
    name_hash: str = ""                 # SHA-256 (lowercased name)

    # --- classification ---
    category: str = Category.OTHER.value
    tags: list[str] = field(default_factory=list)

    # --- quality ---
    quality_score: float = 0.0          # 0.0 - 1.0
    safety_flags: list[str] = field(default_factory=list)
    body_tokens: int = 0                # rough tiktoken estimate

    # --- structural features ---
    has_scripts: bool = False
    has_references: bool = False

    # --- status ---
    deleted: bool = False               # soft-delete marker (near-dup loser OR safety-hard-gated)
    # a skill merged after cross-source near-dup detection records the winner's skill_id
    # (with deleted=True). This keeps the winner's files/metadata traceable.
    superseded_by: str | None = None

    # --- timestamps ---
    added_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    # --- optional storage path ---
    stored_path: str = ""               # in-library storage path (relative to library root)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "SkillRecord":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


# ---------------------------------------------------------------------------
# SQLite schema
# ---------------------------------------------------------------------------

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS skills (
    skill_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT NOT NULL,
    body TEXT NOT NULL,
    frontmatter_raw TEXT NOT NULL DEFAULT '{}',       -- JSON
    source TEXT NOT NULL,
    source_url TEXT,
    source_path TEXT NOT NULL DEFAULT '',
    license TEXT,
    content_hash TEXT NOT NULL,
    name_hash TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT 'OTHER',
    tags TEXT NOT NULL DEFAULT '[]',                   -- JSON array
    quality_score REAL NOT NULL DEFAULT 0.0,
    safety_flags TEXT NOT NULL DEFAULT '[]',           -- JSON array
    body_tokens INTEGER NOT NULL DEFAULT 0,
    has_scripts INTEGER NOT NULL DEFAULT 0,            -- 0/1
    has_references INTEGER NOT NULL DEFAULT 0,
    deleted INTEGER NOT NULL DEFAULT 0,
    superseded_by TEXT,                                -- points to the winner skill_id when merged by near-dup detection
    added_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    stored_path TEXT NOT NULL DEFAULT '',
    active INTEGER NOT NULL DEFAULT 0                   -- GREEN-license gate; 0=excluded from export (safe-by-default)
);

CREATE INDEX IF NOT EXISTS idx_skills_source      ON skills(source);
CREATE INDEX IF NOT EXISTS idx_skills_category    ON skills(category);
CREATE INDEX IF NOT EXISTS idx_skills_content_hash ON skills(content_hash);
CREATE INDEX IF NOT EXISTS idx_skills_name_hash    ON skills(name_hash);
CREATE INDEX IF NOT EXISTS idx_skills_name         ON skills(name);
CREATE INDEX IF NOT EXISTS idx_skills_deleted      ON skills(deleted);
CREATE INDEX IF NOT EXISTS idx_skills_active       ON skills(active);
CREATE INDEX IF NOT EXISTS idx_skills_superseded_by ON skills(superseded_by);
"""

# Vector table (sqlite-vec). Created separately in store.py since it
# requires loading the sqlite-vec extension.
VEC_TABLE_NAME = "vec_skills"
