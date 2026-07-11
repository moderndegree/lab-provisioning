"""Minimal OpenAI-compatible chat client (stdlib only).

Talks to Ollama's /v1/chat/completions on mini. No streaming — the lab's
opencode config also runs stream:false; loop strategies want whole responses
anyway. Retries with backoff because a cold model load can take a while and
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
            "stream": False,
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
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                msg = data["choices"][0]["message"]
                usage = data.get("usage") or {}
                return ChatResult(
                    content=msg.get("content") or "",
                    model=data.get("model", payload["model"]),
                    prompt_tokens=usage.get("prompt_tokens", 0),
                    completion_tokens=usage.get("completion_tokens", 0),
                    latency_s=time.monotonic() - start,
                    reasoning=msg.get("reasoning") or msg.get("reasoning_content") or "",
                    raw=data,
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
