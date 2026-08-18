"""Query rewriter — turns a verbose message into a concise routing query.

One LLM call, strictly a cleaning step: strip noise (paths, IDs,
timestamps) and keep task type, domain and key technical details, so the
BM25 fan-out matches on what the turn is about rather than on its
incidentals.

It used to also decide whether retrieval should happen at all, and that
veto is gone. Two reasons, both measured here: the verdict was unstable
(one query, six runs, true and false three each), and it was made without
seeing a single candidate. The gate sees the shortlist *and* the agent's
tool list, so it is the only step with the standing to decide that nothing
should be injected. A rewrite that fails now costs the turn a cleaner
query, never its skills.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from skillsearch.ports import ChatModel

from skillsearch.replies import extract_json_object

log = logging.getLogger(__name__)

_REWRITE_PROMPT = """\
Rewrite the following user query for skill retrieval. \
Remove noise (paths, IDs, timestamps, boilerplate). \
Keep task type, domain, required capabilities, and key technical details. \
Do NOT answer or solve the query — only rewrite it.

Return JSON: {{"rewritten_query": "..." or null}}

{query}"""

_QUERY_MAX_LENGTH = 2000


@dataclass(frozen=True)
class RewriteResult:
    """What the rewriter produces: a cleaner query, or nothing usable.

    ``rewritten_query`` of ``None`` means "search the raw query" — every
    failure path lands here, and none of them stops retrieval.
    """

    rewritten_query: str | None = None


class QueryRewriter:
    """Rewrites a turn's message into a retrieval query.

    Goes through the host-supplied :class:`~skillsearch.ports.ChatModel`,
    so retry policy and provider extras are whatever the host already
    applies to its own calls.

    **The caller owns the deadline.** ``analyze`` awaits the model without
    bounding it, because the engine already wraps this call in
    ``rewrite_timeout_s``. Code calling this directly must bound it itself.
    """

    def __init__(
        self,
        provider: ChatModel,
        *,
        model: str | None = None,
        max_tokens: int = 8192,
        temperature: float = 0.3,
    ) -> None:
        self._provider = provider
        self._model = model
        self._max_tokens = max_tokens
        self._temperature = temperature

    def set_provider(self, provider: ChatModel, model: str) -> None:
        """Adopt the provider a live ``/model`` switch just built.

        Held rather than looked up per call, so without this the rewriter
        keeps calling the provider captured at construction after the loop
        has moved on -- a switch away from an unusable credential fixes the
        main path and leaves this one failing.
        """
        del model  # the rewriter runs on the provider's default model
        self._provider = provider

    async def analyze(self, query: str) -> RewriteResult:
        truncated = (query or "").strip()[:_QUERY_MAX_LENGTH]
        if not truncated:
            return RewriteResult()

        prompt = _REWRITE_PROMPT.format(query=truncated)
        try:
            resp = await self._provider.complete(
                [{"role": "user", "content": prompt}],
                model=self._model or None,
                max_tokens=self._max_tokens,
                temperature=self._temperature,
            )
            content = resp if isinstance(resp, str) else str(getattr(resp, "content", "") or "")
            # A model that answers with an error still returns text; treat
            # it as the failure it is rather than parsing the message.
            if getattr(resp, "finish_reason", None) == "error":
                # Raised inside the try on purpose: a provider error and a
                # transport error are the same event to this caller, and
                # the handler below is where both become the fallback.
                raise RuntimeError(content or "provider error")  # noqa: TRY301
        except Exception as e:
            log.warning("query rewrite failed (%s); searching the raw query", e)
            return RewriteResult()
        return self._parse(content)

    @staticmethod
    def _parse(content: str) -> RewriteResult:
        data = extract_json_object(content)
        if data is None:
            log.warning("rewrite response carried no JSON object; searching the raw query")
            return RewriteResult()

        raw = data.get("rewritten_query")
        rewritten = raw.strip() or None if isinstance(raw, str) else None
        return RewriteResult(rewritten_query=rewritten)


__all__ = ["QueryRewriter", "RewriteResult"]
