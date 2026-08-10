"""Embedding client for the producer's near-duplicate detection.

Supports two providers:
  - ``openai_compatible`` (default): POST ``<base_url>/embeddings`` with
    ``{"input": [...], "model": ...}`` — any OpenAI-compatible server
    (vLLM / TGI / infinity) serving the embedding model.
  - ``skillrouter_remote``: a custom shim, POST ``<base_url>/embed`` with
    ``{"texts": [...]}`` → ``{"embeddings": [[...]]}``.

Usage:
  client = EmbeddingClient(provider="openai_compatible",
                           base_url="http://localhost:8100/v1",
                           model="Qwen3-Embedding-0.6B", dim=1024)
  vec = client.embed("hello")

When the endpoint is unreachable, ``embed_batch`` returns None and the caller
skips storing vectors (dedup falls back to content_hash / name_hash).
"""

from __future__ import annotations

import logging
import os
import re

logger = logging.getLogger("skillcorpus.embed")


# Frontmatter regex used by ``format_embedding_text`` to defend against
# bodies where the parse step didn't strip the YAML block (e.g. rows
# imported from third-party sources). A safety net — bodies are normally
# stripped at parse time.
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
# The producer and consumer must use the identical formula (pipe-joined,
# description capped at 500, body at 8000). Switching embedding models changes
# the vector space, so all rows must be re-embedded before the corpus is
# re-exported.
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
    """Embedding client. See the module docstring for the two providers
    (``openai_compatible`` default / ``skillrouter_remote``)."""

    # When a probe fails (endpoint unreachable), cache the negative result for
    # this many seconds before re-probing. Without it, an unreachable endpoint
    # costs one 5s probe per skill during a build; with it, at most one probe
    # per interval, while still recovering if the endpoint comes back.
    _PROBE_TTL_S = 60.0

    def __init__(
        self,
        dim: int = 1024,
        base_url: str | None = None,
        api_key: str | None = None,
        batch_size: int = 32,
        timeout: int = 60,
        provider: str = "openai_compatible",
        model: str = "",
    ):
        self.dim = dim
        self.provider = provider
        self.model = model
        self.batch_size = batch_size
        self.timeout = timeout
        self.base_url = base_url or os.environ.get("OPENAI_BASE_URL") or ""
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY") or "dummy"
        self._available: bool | None = None
        self._probe_failed_at: float | None = None  # monotonic time of last failed probe

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
        import time as _time
        # Recently failed → skip the probe until the TTL elapses (avoids a 5s
        # probe per skill when the endpoint is down for the whole build).
        if (self._probe_failed_at is not None
                and _time.monotonic() - self._probe_failed_at < self._PROBE_TTL_S):
            return False
        import json as _json
        import urllib.request
        url, build, extract = self._endpoint()
        try:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            req = urllib.request.Request(
                url,
                data=_json.dumps(build(["test"])).encode("utf-8"),
                headers=self._headers(),
            )
            resp = opener.open(req, timeout=5)
            vecs = extract(_json.loads(resp.read()))
            self._available = (isinstance(vecs, list) and len(vecs) == 1
                               and isinstance(vecs[0], list)
                               and len(vecs[0]) == self.dim)
        except Exception:
            # Unreachable — record the failure time and back off for _PROBE_TTL_S
            # (a blip must not disable embeddings for the whole build, but we must
            # not re-probe a down endpoint on every skill either).
            self._probe_failed_at = _time.monotonic()
            return False
        return self._available

    def embed(self, text: str) -> list[float] | None:
        """Skip the is_available check to avoid recursion (is_available also calls embed internally)."""
        if not self.api_key:
            return None
        results = self.embed_batch([text], _skip_avail_check=True)
        return results[0] if results else None

    def _endpoint(self):
        """Return ``(url, build_body, extract_vecs)`` for the configured provider.

        ``build_body(chunk)`` builds the request JSON and ``extract_vecs(payload)``
        pulls the list of vectors out of the response, so the two wire protocols
        share one POST/retry path.
        """
        base = self.base_url.rstrip("/")
        if self.provider == "skillrouter_remote":
            if base.endswith("/v1"):
                base = base[:-3]
            return (f"{base}/embed",
                    lambda chunk: {"texts": chunk},
                    lambda p: p.get("embeddings"))
        return (f"{base}/embeddings",
                lambda chunk: {"input": chunk, "model": self.model},
                lambda p: [r["embedding"]
                          for r in sorted(p["data"], key=lambda d: d.get("index", 0))])

    def _post_chunk(self, opener, url, body, expect_len, extract):
        """POST one chunk with retry/backoff; return its vectors.

        Retries transient errors (RST / timeout) with exponential backoff. A
        short/missing vector array is treated as a transient error and retried,
        rather than silently shifting every later text->vector pairing; if it
        never recovers the exception propagates and ``embed_batch`` returns None.
        """
        import json as _json
        import time as _time
        import urllib.error
        import urllib.request
        data = _json.dumps(body).encode("utf-8")
        max_retries = 5
        for attempt in range(max_retries):
            try:
                req = urllib.request.Request(url, data=data, headers=self._headers())
                resp = opener.open(req, timeout=self.timeout)
                vecs = extract(_json.loads(resp.read()))
                if not isinstance(vecs, list) or len(vecs) != expect_len:
                    got = len(vecs) if isinstance(vecs, list) else "non-list"
                    raise ConnectionError(
                        f"embed endpoint returned {got} vectors for "
                        f"{expect_len} texts (partial/malformed response)"
                    )
                return vecs
            except (urllib.error.URLError, ConnectionError, OSError,
                    KeyError, TypeError) as e:
                if attempt == max_retries - 1:
                    raise
                delay = 2.0 ** attempt
                logger.warning(
                    f"embed chunk attempt {attempt+1}/{max_retries} failed: "
                    f"{e!r}, retry in {delay:.1f}s"
                )
                _time.sleep(delay)

    def _embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Embed all texts in ``batch_size`` chunks via the configured provider."""
        import urllib.request
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        url, build, extract = self._endpoint()
        out: list[list[float]] = []
        for i in range(0, len(texts), self.batch_size):
            chunk = [t if t.strip() else "empty"
                     for t in texts[i : i + self.batch_size]]
            out.extend(self._post_chunk(opener, url, build(chunk), len(chunk), extract))
        return out

    def embed_batch(
        self, texts: list[str], _skip_avail_check: bool = False,
    ) -> list[list[float]] | None:
        if not texts:
            return []
        if not _skip_avail_check and not self.is_available():
            return None
        try:
            return self._embed_batch(texts)
        except Exception as e:
            logger.warning(f"embedding call failed: {e}")
            return None
