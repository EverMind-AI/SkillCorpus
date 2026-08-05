"""Pure content/name hashing + cosine similarity — no package deps.

Lives in core because core.store keys skills by name_hash; the dedup stage
(curate.dedup) and everyone else import the hashes from here.
"""
from __future__ import annotations

import hashlib
import re

_WS_RE = re.compile(r"\s+")
# In canonical_name, replace common separators like . _ - / with spaces, then compress
_NAME_SEP_RE = re.compile(r"[^a-z0-9]+")


def normalize_body(body: str) -> str:
    """Normalize body text (strip leading/trailing whitespace + collapse consecutive whitespace) for hash comparison."""
    text = body.strip()
    # Collapse consecutive whitespace but preserve single-newline semantics
    lines = [_WS_RE.sub(" ", line.strip()) for line in text.splitlines()]
    return "\n".join(l for l in lines if l)


def content_hash(body: str) -> str:
    """Compute the SHA-256 of the normalized body (whitespace differences removed)."""
    normalized = normalize_body(body)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def canonical_name(name: str) -> str:
    """Normalize a skill name to its canonical form, absorbing case/punctuation/separator differences.

    Examples:
      "react-hooks"   → "react hooks"
      "React Hooks"   → "react hooks"
      "react_hooks"   → "react hooks"
      "React.Hooks!!" → "react hooks"
      "  pdf   gen "  → "pdf gen"
    """
    s = name.strip().lower()
    s = _NAME_SEP_RE.sub(" ", s)
    s = _WS_RE.sub(" ", s).strip()
    return s


def name_hash(name: str) -> str:
    """Compute the SHA-256 of canonical_name.

    After canonicalization, "react-hooks" / "React Hooks" / "react_hooks"
    hit the same hash, so near-identical names across sources are also recognized as collisions.
    """
    return hashlib.sha256(canonical_name(name).encode("utf-8")).hexdigest()


def short_hash(full_hash: str, n: int = 8) -> str:
    """Take the first n characters of the hash as the skill_id suffix."""
    return full_hash[:n]


# ---------------------------------------------------------------------------
# Approximate similarity (used after embedding retrieval)
# ---------------------------------------------------------------------------

def cosine_sim(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity in pure Python. Adequate for small-scale vectors."""
    import math
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)
