"""Embedding client — SkillRouter remote embedding endpoint (POST /embed).

Usage:
  os.environ["OPENAI_BASE_URL"] = "https://api.openai.com/v1"
  os.environ["OPENAI_API_KEY"]  = "sk-..."
  client = EmbeddingClient(dim=1024, base_url="http://host:1357/new")
  vec = client.embed("hello")
  vecs = client.embed_batch(["a", "b", "c"])

If no key is configured in the environment, `embed_batch` returns None (the caller skips
storing vectors and relies on BM25 retrieval only).
"""

from __future__ import annotations

import logging
import os
import re

logger = logging.getLogger("skillcorpus.embed")


# Frontmatter regex used by ``format_embedding_text`` to defend against
# bodies where the producer parse step didn't strip the YAML block (e.g.
# DB rows imported from third-party sources). ~99.8% of our existing rows
# are already stripped at parse time, so this is a safety net.
_FRONTMATTER_RE = re.compile(r"^---\n.*?\n---\s*\n?", re.DOTALL)


def _strip_frontmatter(body: str) -> str:
    return _FRONTMATTER_RE.sub("", body or "", count=1)


# ---------------------------------------------------------------------
# Canonical embedding-text formula.
#
# This is the single function both PRODUCER (this package)
# and CONSUMER (Raven's skill_forge
# ``_format_skill_text``) MUST agree on, otherwise mass-pool embeddings
# stored at ingest time cannot be matched against query embeddings
# computed at retrieval time (cosine drops to ~0.6-0.9 due to text-shape
# divergence alone, even with identical model).
#
# History — these were once divergent:
#   - ingest.py:   "{name}\n{description}\n{body[:2000]}"  (newline, 2000)
#   - retrieval:   "{name} | {description[:500]} | {strip(body)[:8000]}"
# The pipe + 8000 form is Tianyi's original (commit 84fd1c2, 2026-04-25),
# aligned across ingest + export on 2026-05-19. When switching embedding models, the vector
# space changes, so all active rows in the corpus must be re-embedded before re-exporting the mass pool.
# ---------------------------------------------------------------------
def format_embedding_text(
    name: str,
    description: str,
    body: str,
    desc_max: int = 500,
    body_max: int = 8000,
) -> str:
    body_clean = _strip_frontmatter(body or "")
    return f"{name} | {(description or '')[:desc_max]} | {body_clean[:body_max]}"


class EmbeddingClient:
    """Embedding client — SkillRouter remote inference endpoint.

    POST ``<base_url>/embed`` with body ``{"texts": [...]}`` →
    ``{"embeddings": [[...]]}``. The current base_url = <EMBEDDING_HOST>/new,
    using the same endpoint / same vector space as the consumer mass pool (label embedding-our-new).
    """

    def __init__(
        self,
        dim: int = 1024,
        base_url: str | None = None,
        api_key: str | None = None,
        batch_size: int = 32,
        timeout: int = 60,
    ):
        self.dim = dim
        self.batch_size = batch_size
        self.timeout = timeout
        self.base_url = base_url or os.environ.get("OPENAI_BASE_URL") or ""
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY") or "dummy"
        self._available: bool | None = None

    def _headers(self) -> dict[str, str]:
        h = {"Content-Type": "application/json"}
        if self.api_key and self.api_key != "dummy":
            h["Authorization"] = f"Bearer {self.api_key}"
        return h

    def is_available(self) -> bool:
        """Whether the endpoint is reachable (one fast single-shot probe).

        Uses a short timeout and NO retry so an unreachable endpoint fails in
        seconds. The full retry/backoff path in embed_batch (5 x self.timeout)
        is for real ingest calls, not the startup probe — going through it made
        SkillLibrary.open() hang for minutes when the endpoint was down.
        """
        if self._available is not None:
            return self._available
        if not self.api_key:
            self._available = False
            return False
        import json as _json
        import urllib.request
        base = self.base_url.rstrip("/")
        if base.endswith("/v1"):
            base = base[:-3]
        try:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            req = urllib.request.Request(
                f"{base}/embed",
                data=_json.dumps({"texts": ["test"]}).encode("utf-8"),
                headers=self._headers(),
            )
            resp = opener.open(req, timeout=5)
            embs = _json.loads(resp.read()).get("embeddings")
            self._available = (isinstance(embs, list) and len(embs) == 1
                               and isinstance(embs[0], list)
                               and len(embs[0]) == self.dim)
        except Exception:
            # transient probe failure — do NOT cache, so a later call can retry
            # (one startup blip must not disable embeddings for the whole build)
            return False
        return self._available

    def embed(self, text: str) -> list[float] | None:
        """Skip the is_available check to avoid recursion (is_available also calls embed internally)."""
        if not self.api_key:
            return None
        results = self.embed_batch([text], _skip_avail_check=True)
        return results[0] if results else None

    def _embed_batch_skillrouter(self, texts: list[str]) -> list[list[float]]:
        """Custom SkillRouter API: POST <base>/embed body {texts: [...]}.

        ``base_url`` should be the host root incl. model path prefix — e.g.
        ``http://<EMBEDDING_HOST>/new``. We strip a trailing ``/v1`` if present.

        Retries transient errors (RST / timeout) with exponential backoff
        — the SkillRouter endpoint occasionally drops connections under
        load when both producer ingest and consumer build hit it.
        """
        import json as _json
        import time as _time
        import urllib.error
        import urllib.request
        base = self.base_url.rstrip("/")
        if base.endswith("/v1"):
            base = base[:-3]
        url = f"{base}/embed"
        proxy_handler = urllib.request.ProxyHandler({})
        opener = urllib.request.build_opener(proxy_handler)
        max_retries = 5
        out: list[list[float]] = []
        for i in range(0, len(texts), self.batch_size):
            chunk = texts[i : i + self.batch_size]
            chunk = [t if t.strip() else "empty" for t in chunk]
            data = _json.dumps({"texts": chunk}).encode("utf-8")
            for attempt in range(max_retries):
                try:
                    req = urllib.request.Request(
                        url, data=data,
                        headers=self._headers(),
                    )
                    resp = opener.open(req, timeout=self.timeout)
                    payload = _json.loads(resp.read())
                    embs = payload.get("embeddings")
                    # A 200 response with a short/missing embeddings array
                    # would otherwise silently shift every later text→vector
                    # pairing (the `or []` swallowed it). Treat a length
                    # mismatch as a transient error so the chunk is retried;
                    # if it never recovers, embed_batch's except returns None
                    # and callers (c8/c9) skip the chunk loudly as failed.
                    if not isinstance(embs, list) or len(embs) != len(chunk):
                        got = len(embs) if isinstance(embs, list) else "non-list"
                        raise ConnectionError(
                            f"embed endpoint returned {got} embeddings for "
                            f"{len(chunk)} texts (partial/malformed response)"
                        )
                    out.extend(embs)
                    break
                except (urllib.error.URLError, ConnectionError, OSError) as e:
                    if attempt == max_retries - 1:
                        raise
                    delay = 2.0 ** attempt
                    logger.warning(
                        f"embed batch {i} attempt {attempt+1}/{max_retries} "
                        f"failed: {e!r}, retry in {delay:.1f}s"
                    )
                    _time.sleep(delay)
        return out

    def embed_batch(
        self, texts: list[str], _skip_avail_check: bool = False,
    ) -> list[list[float]] | None:
        if not texts:
            return []
        if not _skip_avail_check and not self.is_available():
            return None
        try:
            return self._embed_batch_skillrouter(texts)
        except Exception as e:
            logger.warning(f"embedding call failed: {e}")
            return None
