"""LLM client — OpenAI-compatible chat interface (single endpoint).

Purpose: LLM classification / tagging / quality scoring / fallback.

Configuration is read from the ``llm`` section of config.yaml, using a **single endpoint**
(taking ``llm.endpoints[0]`` or falling back to ``llm.base_url``).
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any

logger = logging.getLogger("skillcorpus.llm")

_JSON_RE = re.compile(r"\{[\s\S]*\}")


class LLMClient:
    """OpenAI-compatible single-endpoint LLM client (chat + retry)."""

    def __init__(
        self,
        base_url: str = "http://localhost:8211/v1",
        model: str = "qwen3",
        api_key: str = "dummy",
        *,
        temperature: float = 0.1,
        max_tokens: int = 512,
        timeout: int = 60,
        max_retries: int = 2,
        enable_thinking: bool = False,   # Qwen3 / R1: disable thinking for speed
    ):
        self.base_url = base_url
        self.model = model
        self.api_key = api_key
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.timeout = timeout
        self.max_retries = max_retries
        self.enable_thinking = enable_thinking
        self._client: Any = None
        self._available: bool | None = None
        logger.info("LLMClient initialised: %s#%s", base_url, model)

    def is_available(self) -> bool:
        if self._available is not None:
            return self._available
        # Short-timeout, no-retry probe so an unreachable endpoint fails in
        # seconds. self.chat() retries max_retries x self.timeout, which made
        # SkillLibrary.open()'s startup probe hang for minutes when down.
        try:
            from openai import OpenAI
            probe = OpenAI(base_url=self.base_url, api_key=self.api_key,
                           timeout=5, max_retries=0)
            r = probe.chat.completions.create(
                model=self.model, max_tokens=4,
                messages=[{"role": "user", "content": "ping"}],
            )
            self._available = bool(r and r.choices)
        except Exception as e:
            logger.debug(f"LLM availability check failed: {e}")
            self._available = False
        return self._available

    def _get_client(self):
        if self._client is None:
            try:
                from openai import OpenAI
            except ImportError as e:
                raise RuntimeError("openai package not installed") from e
            self._client = OpenAI(
                base_url=self.base_url, api_key=self.api_key, timeout=self.timeout,
            )
        return self._client

    def chat(
        self,
        messages: list[dict[str, str]],
        temperature: float | None = None,
        max_tokens: int | None = None,
        response_format: str | None = None,
    ) -> str | None:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature if temperature is None else temperature,
            "max_tokens": self.max_tokens if max_tokens is None else max_tokens,
        }
        if response_format == "json":
            kwargs["response_format"] = {"type": "json_object"}
        if not self.enable_thinking:
            kwargs["extra_body"] = {"chat_template_kwargs": {"enable_thinking": False}}

        client = self._get_client()
        last_err: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                resp = client.chat.completions.create(**kwargs)
                return resp.choices[0].message.content or ""
            except Exception as e:
                last_err = e
                if attempt < self.max_retries:
                    time.sleep(0.5 + attempt * 0.5)
                    continue
        logger.warning("LLM chat failed after %d attempts: %s",
                       self.max_retries + 1, last_err)
        return None

    @staticmethod
    def extract_json(text: str | None) -> dict | None:
        """Best-effort parse of a single JSON object from the LLM output.

        Uniform handling: strip ``<think>...</think>`` thinking blocks, markdown ```` ``` ```` fences,
        and surrounding prose, then ``json.loads``; on failure, fall back to the first balanced ``{...}``.
        Quality judging / classification / dedup judging all share this extraction (each does its own field validation).
        """
        if not text:
            return None
        text = re.sub(r"<think>[\s\S]*?</think>", "", text).strip()
        # Strip fence markers (leading ```json / ``` anywhere)
        text = re.sub(r"```(?:json)?\s*", "", text)
        text = re.sub(r"```\s*$", "", text).strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
        m = _JSON_RE.search(text)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError as e:
                logger.debug("JSON fallback parse failed: %s", e)
                return None
        return None
