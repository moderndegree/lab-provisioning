"""Shared fixtures: a scripted fake client so no test touches the network."""

from __future__ import annotations

import pytest

from quality_loop.client import ChatResult


class FakeClient:
    """Returns queued responses in order; records every call it receives."""

    def __init__(self, responses: list[str]):
        self.responses = list(responses)
        self.calls: list[dict] = []

    def chat(self, messages, model="general", **kw) -> ChatResult:
        self.calls.append({"messages": messages, "model": model, **kw})
        if not self.responses:
            raise AssertionError("FakeClient ran out of scripted responses")
        return ChatResult(
            content=self.responses.pop(0),
            model=model,
            prompt_tokens=10,
            completion_tokens=20,
            latency_s=0.01,
        )

    def ask(self, prompt, model="general", system=None, **kw) -> ChatResult:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        return self.chat(messages, model=model, **kw)


@pytest.fixture
def fake_client_factory():
    return FakeClient
