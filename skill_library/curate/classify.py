"""curate.classify — 16-class LLM classifier + rule-based tag extraction."""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any

from ..core.llm import LLMClient
from ..core.store import CATEGORIES

logger = logging.getLogger("skill_library.curate.classify")


# ════════════════════ 16-class classification + tag extraction ════════════════════
"""LLM classifier — LLM-based, 16-class single-label.

The sole implementation of producer-side classification. Replaces the deprecated
classify.py (rules) / classify_llm.py (old 15-class LLM) / classify_facets.py (4-facet).

Measured on 1000 samples with Qwen3.5-397B-A17B-GPTQ-Int4:
    100% succ / 0 OOV / 0 fail / average confidence 0.95

Usage:
    cls = Classifier(llm)
    result = cls.classify(name, description, body)
    # result.category ∈ VOCAB (16 classes), result.confidence ∈ [0,1]

Failure fallback:
    LLM returns non-JSON / category OOV / network error → category="OTHER", confidence=0.0
"""






# 16-class vocabulary — single source = store.CATEGORIES (avoid drift from the enum)
VOCAB: frozenset[str] = frozenset(CATEGORIES)


PROMPT_TEMPLATE = """\
You are classifying an agent skill into ONE primary category. Output STRICT JSON only.

# Core principle: classify by SKILL'S PRIMARY DELIVERABLE

Almost every skill in 2026 uses LLM internally. AI-ML is NOT a catch-all for
"this skill uses an LLM". Look at what the skill PRODUCES / DELIVERS to the user.
That output determines the category.

# Taxonomy (16 categories, pick EXACTLY ONE)

DEV            — General coding / SaaS API integration / SDK wrappers / generic backend logic.
                 Excludes: identity (→AUTH), AI as deliverable (→AI-ML), data pipelines (→DATA),
                 frontend (→FRONTEND-UI), CI/CD (→DEVOPS-INFRA).

FRONTEND-UI    — Web frontend / mobile apps / UI components / design systems / visual layout.

DEVOPS-INFRA   — CI/CD / deployment / containers / k8s / cloud infra / observability / monitoring.

DATA           — STRUCTURED/QUANTITATIVE output: data engineering / ETL / databases /
                 SQL / BI / data analysis / visualization / reference DBs.

AI-ML          — Skill's PRIMARY DELIVERABLE is an AI system itself:
                 ✅ The skill IS an AI agent / LLM persona / role-play character
                 ✅ The skill IS a multi-agent orchestration system
                 ✅ Model training / inference / fine-tuning / RAG infrastructure / prompt engineering
                 ✅ Game-playing AI bots (chess/poker/strategy)
                 ❌ NOT just because skill uses LLM internally — look at OUTPUT:
                    - LLM writes prose → WRITING (not AI-ML)
                    - LLM generates images/video/audio → MULTIMEDIA (not AI-ML)
                    - LLM parses documents → DOC-PROC (not AI-ML)
                    - LLM queries data → DATA (not AI-ML)
                    - LLM calls SaaS APIs → DEV (not AI-ML)
                    - LLM coordinates business steps → WORKFLOW (not AI-ML)

TESTING        — SOFTWARE or HARDWARE testing only: unit / integration / E2E / fuzz /
                 coverage / QA / debugging / test infrastructure / test planning of code/hardware.

SECURITY       — Vulnerability scanning / pen-test / encryption / threat detection /
                 audit / forensics. Excludes identity (→AUTH).

AUTH           — Authentication / authorization / OAuth / SSO / IAM / token / permission management.

DOC-PROC       — Processing EXISTING documents: pdf/docx/xlsx/pptx/markdown — parse, extract,
                 convert, validate.

WRITING        — Generating ORIGINAL PROSE: articles / emails / reports / strategy documents /
                 advisory recommendations / coaching narratives / summaries / market analysis prose.

MULTIMEDIA     — Image / video / audio generation or processing.

COMMS          — Messaging channel integrations: email / chat / IM (Slack/Teams/Discord/
                 DingTalk/WeChat).

WORKFLOW       — Multi-step BUSINESS workflows / playbooks / cross-step orchestration.
                 EXCLUDES: AI agent systems (→AI-ML), CI/CD (→DEVOPS-INFRA),
                 single-task office work (→PRODUCTIVITY).

PRODUCTIVITY   — SINGLE-TASK individual office work: calendar event / one schedule /
                 one admin record / one note / one booking / one filing.

META           — SKILL CREATION/MANAGEMENT tooling ONLY: skill builders / registries /
                 marketplaces / library management / MCP server creators.

OTHER          — Pure lifestyle / cultural / specialized academic / niche engineering
                 with no fitting activity. NOT for "vertical domain" alone.

# Conflict-resolution priorities
1. AUTH > DEV
2. DEVOPS-INFRA > WORKFLOW (CI/CD)
3. WRITING > DATA (prose) ; DATA > WRITING (numeric)
4. WRITING > PRODUCTIVITY (strategy)
5. WORKFLOW > PRODUCTIVITY (multi-step)
6. DATA > DOC-PROC (lookup vs parse)

# AI-ML decision tree
Q1: Is the skill's PRIMARY DELIVERABLE an AI system (agent/persona/RAG/model)?
    YES → AI-ML
    NO  → continue
Q2: What is the OUTPUT?
    Prose → WRITING ; Image/video/audio → MULTIMEDIA ; Parsed doc → DOC-PROC ;
    Numbers/tables → DATA ; SaaS action → DEV ; Multi-step process → WORKFLOW

# Specific guidance
- "Watch X then notify Slack/Telegram": WORKFLOW
- "Pure send email/message": COMMS
- "Book/schedule appointment": PRODUCTIVITY
- "AI persona / role-play / character agent": AI-ML
- "LLM writes report / summary / strategy doc": WRITING (LLM is tool)
- "LLM generates image / presentation": MULTIMEDIA (output is media)
- "LLM extracts data from PDF": DOC-PROC (input is doc)
- "LLM-powered SQL search": DATA (output is structured)
- "Reference DB (parts/tax/regulations)": DATA
- "Test planning of code/hardware": TESTING
- "Compliance audit": WORKFLOW (multi-step) or PRODUCTIVITY (single)

# When NOT to use OTHER
"Vertical domain" alone is NOT a reason. Real estate / healthcare / crypto /
e-commerce / marketing fit DATA / AI-ML / PRODUCTIVITY / WRITING.

OTHER only for:
  ✅ Pure lifestyle/hobby
  ✅ Specialized academic/engineering with no clear fit
  ✅ Cultural narrative / lore

Reply with strict JSON, no markdown, no commentary, no thinking text.

# Output schema
{"category": "<one of the 16 codes>", "confidence": <0.0-1.0>, "reason": "<one short sentence>"}

# Skill to classify
Name: {name}
Description: {desc}
Body excerpt: {body}

JSON output:"""


@dataclass
class ClassifyResult:
    category: str               # one of VOCAB
    confidence: float           # 0.0 - 1.0
    reason: str = ""            # one short sentence
    method: str = "llm"         # "llm" | "fallback"


def _slice(s: str, n: int) -> str:
    if not s:
        return ""
    return s[:n].rstrip() + (" ..." if len(s) > n else "")


def _build_classify_prompt(name: str, description: str, body: str) -> list[dict[str, str]]:
    user = (
        PROMPT_TEMPLATE
        .replace("{name}", name or "")
        .replace("{desc}", _slice(description, 500))
        .replace("{body}", _slice(body, 2500))
    )
    return [
        {"role": "system", "content": "You output strict JSON only."},
        # /no_think prefix — Qwen3 thinking-model trigger to skip reasoning
        {"role": "user", "content": "/no_think\n\n" + user},
    ]


def _parse_classify_response(text: str) -> dict | None:
    """Classification JSON → {category,confidence,reason}, validate category∈VOCAB; None on failure.

    Extraction (<think>/fences/prose) all goes through LLMClient.extract_json; this only does field validation."""
    d = LLMClient.extract_json(text)
    if not isinstance(d, dict):
        return None
    cat = str(d.get("category", "")).strip().upper().replace(" ", "-")
    if cat not in VOCAB:
        return None
    try:
        conf = float(d.get("confidence", 0.5))
    except Exception:
        conf = 0.5
    conf = max(0.0, min(1.0, conf))
    return {
        "category": cat,
        "confidence": conf,
        "reason": str(d.get("reason", ""))[:300],
    }


class Classifier:
    """The sole LLM classifier.

    Usage:
        cls = Classifier(llm_client)
        result = cls.classify("stripe-payment", "Stripe SDK ...", body)
        # result.category == "DEV"
    """

    def __init__(self, llm: LLMClient):
        self.llm = llm

    def classify(
        self, name: str, description: str, body: str = "",
    ) -> ClassifyResult:
        """Single classification — returns a ClassifyResult, never raises.

        On failure (LLM unavailable / parse failure / category OOV) it falls back to OTHER + method='fallback'.
        """
        msgs = _build_classify_prompt(name, description, body)
        try:
            raw = self.llm.chat(msgs, response_format="json")
        except Exception as e:
            logger.debug("LLM error during classify: %s", e)
            raw = None

        if not raw:
            return ClassifyResult(
                category="OTHER", confidence=0.0,
                reason="LLM unavailable", method="fallback",
            )

        parsed = _parse_classify_response(raw)
        if parsed is None:
            logger.debug("classify parse failed: %s", raw[:200])
            return ClassifyResult(
                category="OTHER", confidence=0.0,
                reason="LLM response unparseable", method="fallback",
            )
        return ClassifyResult(
            category=parsed["category"],
            confidence=parsed["confidence"],
            reason=parsed["reason"],
            method="llm",
        )

# =============================================================
# Tag generation (originally skill_library/tags.py, now merged here)
# =============================================================
#
# Tag extraction — pull 3-5 keywords as tags from frontmatter / name / description.
#
# Decoupled from the 16-class CATEGORY above:
#   - Classifier (above) decides the PRIMARY CATEGORY (one of the 16 classes)
#   - extract_tags (below) pulls TAGS (free vocabulary, multiple tags, supplementary retrieval signal)
#
# The implementation is pure rules (no LLM call), because:
#   - tag extraction is fast: the ingest path is a hot path
#   - most upstream frontmatter already carries a tags field, which can be reused directly
#   - even a few arbitrary words are still usable by BM25 / full-text indexing



_SLUG_SPLIT = re.compile(r"[-_\s/.]+")
_WORD_RE = re.compile(r"[a-zA-Z][a-zA-Z0-9+#]{1,}")


_STOPWORDS: frozenset[str] = frozenset({
    # function words
    "the", "and", "for", "this", "that", "with", "from", "into", "your",
    "yours", "you", "use", "using", "used", "when", "whenever", "what",
    "which", "how", "where", "why", "here", "there", "their", "them",
    "have", "has", "had", "having", "are", "was", "were", "been", "being",
    "must", "should", "would", "could", "will", "can", "may", "might", "do",
    "does", "did", "doing", "done", "but", "not", "than", "then", "also",
    "out", "off", "over", "under", "such", "same", "other", "another", "more",
    "most", "any", "all", "some", "every", "each", "few", "many",
    # generic nouns
    "skill", "skills", "task", "tasks", "help", "helps", "helper",
    "description", "name", "claude", "agent", "agents", "user", "users",
    "tool", "tools", "return", "returns", "function", "functions",
    "create", "created", "creating", "generate", "generates", "generated",
    "produce", "produced", "work", "works", "working", "need", "needs",
    "needed", "etc", "want", "wants", "anything", "something",
    "file", "files", "data", "input", "output", "result", "results",
    "type", "types", "step", "steps", "way", "ways", "example", "examples",
    "case", "cases", "setup", "support", "supports", "supported",
})


def extract_tags(
    name: str, description: str,
    frontmatter: dict[str, Any] | None = None,
    max_tags: int = 5,
) -> list[str]:
    """Rule-based tag extraction, returning 0..max_tags lowercase keywords.

    Priority:
      1. frontmatter.tags / keywords (if present upstream)
      2. frontmatter.category (as a supplementary tag)
      3. slug segments of the name ('python-docx-generation' → [python, docx, generation])
      4. non-stopword tokens of length ≥4 from the description
    """
    out: list[str] = []
    seen: set[str] = set()

    def add(tag: str) -> None:
        t = tag.strip().lower()
        if not t or len(t) < 2 or t in seen or len(out) >= max_tags:
            return
        if t in _STOPWORDS:
            return
        seen.add(t)
        out.append(t)

    fm = frontmatter or {}

    # 1. frontmatter tags / keywords
    raw_tags = fm.get("tags") or fm.get("keywords")
    if isinstance(raw_tags, list):
        for t in raw_tags:
            if isinstance(t, str):
                add(t)
    elif isinstance(raw_tags, str):
        for t in re.split(r"[,\s;]+", raw_tags):
            add(t)

    # 2. frontmatter.category (as a tag, not the primary category)
    if isinstance(fm.get("category"), str):
        add(fm["category"])

    # 3. name slug segments
    for p in _SLUG_SPLIT.split(name or ""):
        add(p)

    # 4. long words from the description (≥4 chars)
    for w in _WORD_RE.findall(description or ""):
        if len(w) >= 4:
            add(w)

    return out[:max_tags]
