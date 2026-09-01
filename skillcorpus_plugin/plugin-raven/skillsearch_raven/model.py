"""A chat model for the rewriter and the gate, configured by this plugin.

Raven hands the segment factory a live `LLMProvider` under a private config
key, and for a long time that was the only way this plugin could reach a
model. The tool factory gets no such thing — `build_plugin_tools` passes the
config slice and a `ServiceLocator` and nothing else — so on-demand mode, the
default, ran with no rewriter and no gate.

Unfiltered is not a mild degradation here: fusion ranks by position, so every
source's best hit reaches the model however weakly it matched, and the gate is
the only step that removes those. So this is the fallback the other host
plugins already carry: one POST to an OpenAI-compatible endpoint, named by
ordinary config a user can write in TOML.

A copy rather than a shared module, matching how `model.ts` sits inside each
TypeScript plugin: the engine deliberately owns no model client, because which
endpoint to call is a property of the host's deployment, not of retrieval.

Deliberately not a dependency on any SDK. The engine's `ChatModel` port is
one method — messages in, text out — and that is one request.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

DEFAULT_BASE_URL = "https://api.openai.com/v1"


class OpenAICompatibleModel:
    """`ChatModel` over any endpoint that serves `/chat/completions`.

    Blocking `urllib` inside `run_in_executor`: the engine awaits this from
    a private event loop the adapter owns, and adding an async HTTP
    dependency to reach one endpoint would buy nothing the deadline above
    it does not already handle.
    """

    def __init__(
        self,
        *,
        base_url: str = DEFAULT_BASE_URL,
        api_key: str = "",
        model: str = "",
        timeout_s: float = 30.0,
    ) -> None:
        self._base = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self._api_key = api_key
        self._model = model
        self._timeout_s = timeout_s

    async def complete(
        self,
        messages: list[dict[str, str]],
        *,
        model: str | None = None,
        temperature: float = 0.0,
        max_tokens: int = 8192,
    ) -> str:
        """One completion.

        Args:
            messages: the conversation, already in OpenAI's role/content shape.
            model: the model to call; falls back to the configured one.
            temperature: sampling temperature.
            max_tokens: cap on the reply.

        Returns:
            The assistant's reply text.

        Raises:
            RuntimeError: on any transport or protocol failure. Both
                callers catch it and take their documented fallback, so
                the exception type carries no routing meaning.
        """
        import asyncio

        loop = asyncio.get_running_loop()
        payload = {
            "model": model or self._model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        return await loop.run_in_executor(None, self._post, payload)

    def _post(self, payload: dict[str, Any]) -> str:
        body = json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        request = urllib.request.Request(
            f"{self._base}/chat/completions", data=body, headers=headers
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout_s) as response:
                data = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as err:
            detail = err.read().decode("utf-8", "replace")[:200]
            raise RuntimeError(f"model endpoint returned HTTP {err.code}: {detail}") from err
        except Exception as err:
            raise RuntimeError(f"model endpoint unreachable: {err}") from err

        try:
            return str(data["choices"][0]["message"]["content"] or "")
        except Exception as err:
            raise RuntimeError(f"unexpected reply shape: {str(data)[:200]}") from err


__all__ = ["DEFAULT_BASE_URL", "OpenAICompatibleModel"]
