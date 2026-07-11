"""Minimal OpenAI-compatible chat client (stdlib only).

Talks to Ollama's /v1/chat/completions on mini. Requests stream:true under
the hood and reassembles the full response — loop strategies still get one
whole ChatResult back, never partial chunks. Streaming isn't for UX; a
stream:false request on a long coder generation sits silent for minutes with
no bytes on the wire, and something upstream of mini (NAT/relay hop) kills
those idle-looking connections well before this client's own socket timeout.
Streaming keeps real traffic flowing so the connection is never mistaken for
idle. Retries with backoff because a cold model load can take a while and
transient 5xx during a model swap is normal on a single-node box.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

from loopkit.models import resolve_model

DEFAULT_BASE_URL = "http://mini:11434/v1"


class ChatError(RuntimeError):
    """Request failed after all retries."""


@dataclass
class ChatResult:
    content: str
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_s: float = 0.0
    reasoning: str = ""   # thinking text when the endpoint returns it separately
    raw: dict = field(default_factory=dict, repr=False)


class ChatClient:
    """One method that matters: chat(messages) -> ChatResult."""

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = 600.0,
        retries: int = 3,
        backoff_s: float = 5.0,
    ):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.retries = retries
        self.backoff_s = backoff_s

    def chat(
        self,
        messages: list[dict],
        model: str = "general",
        temperature: float | None = None,
        max_tokens: int | None = None,
        seed: int | None = None,
        reasoning_effort: str | None = None,
    ) -> ChatResult:
        payload: dict = {
            "model": resolve_model(model),
            "messages": messages,
            # Streamed, not because callers want partial output (they don't —
            # this method still returns one assembled ChatResult), but
            # because a stream:false request holds an otherwise-silent TCP
            # connection open for the whole generation. Long coder/best_of_n
            # calls (several minutes, no bytes exchanged) were getting killed
            # part-way through by an idle-connection timeout somewhere on
            # the path to mini (NAT/relay hop upstream of Tailscale, ~9m),
            # well under this client's own 600s socket timeout. Streaming
            # keeps real bytes flowing every token so nothing on the path
            # mistakes the request for an idle connection.
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        # "none" disables thinking on Ollama's /v1 (verified on 0.31.2); the
        # /no_think soft switch and a think:false body field are ignored there.
        if reasoning_effort is not None:
            payload["reasoning_effort"] = reasoning_effort
        if temperature is not None:
            payload["temperature"] = temperature
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        if seed is not None:
            payload["seed"] = seed

        body = json.dumps(payload).encode("utf-8")
        url = f"{self.base_url}/chat/completions"
        last_err: Exception | None = None
        for attempt in range(self.retries):
            start = time.monotonic()
            try:
                req = urllib.request.Request(
                    url, data=body, headers={"Content-Type": "application/json"}
                )
                model_name = payload["model"]
                usage: dict = {}
                content_parts: list[str] = []
                reasoning_parts: list[str] = []
                last_chunk: dict = {}
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    for raw_line in resp:
                        line = raw_line.decode("utf-8").strip()
                        if not line or not line.startswith("data:"):
                            continue
                        data_str = line[len("data:"):].strip()
                        if data_str == "[DONE]":
                            break
                        chunk = json.loads(data_str)
                        last_chunk = chunk
                        model_name = chunk.get("model", model_name)
                        if chunk.get("usage"):
                            usage = chunk["usage"]
                        choices = chunk.get("choices") or []
                        if not choices:
                            continue
                        delta = choices[0].get("delta") or {}
                        if delta.get("content"):
                            content_parts.append(delta["content"])
                        reasoning_piece = delta.get("reasoning") or delta.get("reasoning_content")
                        if reasoning_piece:
                            reasoning_parts.append(reasoning_piece)
                return ChatResult(
                    content="".join(content_parts),
                    model=model_name,
                    prompt_tokens=usage.get("prompt_tokens", 0),
                    completion_tokens=usage.get("completion_tokens", 0),
                    latency_s=time.monotonic() - start,
                    reasoning="".join(reasoning_parts),
                    raw=last_chunk,
                )
            except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError, KeyError) as err:
                last_err = err
                if attempt < self.retries - 1:
                    time.sleep(self.backoff_s * (attempt + 1))
        raise ChatError(f"chat() failed after {self.retries} attempts against {url}: {last_err}")

    def ask(self, prompt: str, model: str = "general", system: str | None = None, **kw) -> ChatResult:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        return self.chat(messages, model=model, **kw)
