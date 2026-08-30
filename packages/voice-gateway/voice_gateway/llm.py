"""Streaming chat against mini's quality instance.

Straight to `http://mini:8090/v1` — NOT through Hermes. Hermes's :8645 is a Nous
Portal proxy that forwards to a Nous subscription and has nothing to do with the
`model.provider`/`base_url` it is configured with (see
ser5/ansible/roles/hermes/tasks/verify.yml). Going direct keeps every
conversational turn on Tier L and, just as importantly, keeps it STREAMING,
which is what makes sentence-chunked TTS possible at all.

Two request-level details are load-bearing:

  enable_thinking: false
      :8090 is configured with `chat_template_kwargs: {enable_thinking: true}`
      at the server. docs/operating-manual.md records what happens when a small
      token cap meets thinking: "a 300-token cap was consumed entirely by
      reasoning, returning empty content". For voice that is not a degradation,
      it is a total failure — silence, then nothing. Every request here turns it
      off explicitly.

  max_tokens
      A latency bound, not a memory one. The KV is partitioned statically when
      llama-server starts; nothing we send changes residency. What it bounds is
      how long a rambling answer can hold a slot and keep the user waiting for
      "done".
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator, Sequence
from typing import Any

import httpx

log = logging.getLogger(__name__)

Message = dict[str, Any]


class LlmClient:
    def __init__(
        self,
        base_url: str,
        *,
        model: str,
        max_tokens: int,
        temperature: float,
        timeout: float = 120.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._max_tokens = max_tokens
        self._temperature = temperature
        # Long read timeout, short connect timeout: a slow first token is normal
        # (prefill on a cold prefix), an unreachable mini is not and should fail
        # fast enough to say so out loud.
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout, connect=5.0),
            # llama-server takes no API key and ignores what it is sent; the
            # boundary is mini's UFW (tailnet-only), not authentication.
            headers={"Authorization": "Bearer llamacpp"},
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def stream(self, messages: Sequence[Message]) -> AsyncIterator[str]:
        """Yield content deltas as they arrive."""
        payload = {
            "model": self._model,
            "messages": list(messages),
            "stream": True,
            "max_tokens": self._max_tokens,
            "temperature": self._temperature,
            "chat_template_kwargs": {"enable_thinking": False},
        }
        async with self._client.stream(
            "POST", f"{self._base_url}/chat/completions", json=payload
        ) as resp:
            if resp.status_code != 200:
                body = (await resp.aread()).decode("utf-8", "replace")[:400]
                raise RuntimeError(f"LLM {resp.status_code}: {body}")
            async for line in resp.aiter_lines():
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if not data or data == "[DONE]":
                    if data == "[DONE]":
                        return
                    continue
                try:
                    chunk = json.loads(data)
                except json.JSONDecodeError:
                    continue
                choices = chunk.get("choices") or []
                if not choices:
                    continue
                delta = choices[0].get("delta") or {}
                # If thinking ever comes back on despite the kwarg above, the
                # tokens land in reasoning_content and `content` stays empty.
                # Log it once rather than emitting silence with no explanation.
                if delta.get("reasoning_content") and not delta.get("content"):
                    log.debug("model emitted reasoning_content; thinking is on")
                text = delta.get("content")
                if text:
                    yield text

    async def models(self) -> list[str]:
        resp = await self._client.get(f"{self._base_url}/models", timeout=5.0)
        resp.raise_for_status()
        return [m["id"] for m in resp.json().get("data", [])]
