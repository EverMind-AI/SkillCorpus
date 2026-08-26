"""Lightweight lexical guard for catalogs that always return Top K."""

from __future__ import annotations

import re
from typing import Any

STOP = {
    "a",
    "an",
    "the",
    "to",
    "for",
    "with",
    "using",
    "use",
    "create",
    "make",
    "help",
    "please",
    "and",
    "or",
    "of",
    "in",
    "on",
    "my",
    "me",
    "i",
    "want",
    "need",
    "how",
    "can",
    "from",
    "this",
    "that",
    "these",
    "those",
    "such",
    "no",
    "帮我",
    "请",
    "一个",
    "一下",
    "如何",
    "怎么",
    "使用",
    "需要",
    "想要",
    "进行",
}
ALIASES = {
    "k8s": ("kubernetes",),
    "pr": ("pull", "request"),
    "ppt": ("powerpoint",),
    "pptx": ("powerpoint",),
    "postgres": ("postgresql",),
    "transcription": ("transcribe",),
}
GENERIC = {
    "extract",
    "review",
    "deploy",
    "deployment",
    "generate",
    "generator",
    "analysis",
    "optimize",
    "optimization",
    "process",
    "processing",
    "data",
    "code",
    "task",
}
TOKEN = re.compile(r"[a-z0-9+#.-]+|[\u3400-\u4dbf\u4e00-\u9fff]+", re.I)


def query_terms(query: str) -> list[str]:
    raw: list[str] = []
    for chunk in TOKEN.findall(query.lower()):
        if re.fullmatch(r"[\u3400-\u4dbf\u4e00-\u9fff]+", chunk) and len(chunk) >= 2:
            raw.extend(chunk[index : index + 2] for index in range(len(chunk) - 1))
        else:
            raw.append(chunk)
    terms: list[str] = []
    for token in raw:
        if token in STOP or len(token) < 2:
            continue
        normalized = token.strip(".-")
        for term in ALIASES.get(normalized, (_stem(normalized),)):
            if term and term not in STOP and term not in terms:
                terms.append(term)
    return terms


def check_keyword_relevance(
    query: str,
    *,
    name: str,
    description: str = "",
    tags: Any = None,
) -> dict[str, Any]:
    terms = query_terms(query)
    if not terms:
        return {"passed": False, "matched_terms": [], "required_matched": False, "match_ratio": 0.0}
    tag_text = " ".join(str(tag) for tag in tags) if isinstance(tags, (list, tuple)) else ""
    haystack = f"{name} {description} {tag_text}".lower()
    matched = [term for term in terms if _contains(haystack, term)]
    required = [term for term in terms if term not in GENERIC]
    required_matched = not required or any(term in matched for term in required)
    minimum = 2 if len(terms) >= 4 else 1
    return {
        "passed": required_matched and len(matched) >= minimum,
        "matched_terms": matched,
        "required_matched": required_matched,
        "match_ratio": len(matched) / len(terms),
    }


def _stem(token: str) -> str:
    if token.endswith("ies") and len(token) > 4:
        return token[:-3] + "y"
    if token.endswith("ing") and len(token) > 5:
        return token[:-3]
    if token.endswith("ed") and len(token) > 4:
        return token[:-2]
    if token.endswith("s") and len(token) > 4 and not token.endswith(("ss", "us", "is", "es")):
        return token[:-1]
    return token


def _contains(text: str, term: str) -> bool:
    if re.fullmatch(r"[a-z0-9+#.-]+", term):
        return re.search(rf"(^|[^a-z0-9]){re.escape(term)}([^a-z0-9]|$)", text, re.I) is not None
    return term in text
