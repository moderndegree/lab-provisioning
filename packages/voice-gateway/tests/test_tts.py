"""TTS client: primary engine, and piper as the fallback when it is gone."""

from __future__ import annotations

import httpx
import pytest

from voice_gateway.tts import TtsClient

pytestmark = pytest.mark.asyncio


def _client_for(handler) -> TtsClient:
    transport = httpx.MockTransport(handler)
    return TtsClient(
        "http://chatter.example:8774",
        model="chatterbox-turbo",
        voice="default",
        fallback_url="http://piper.example:8770",
        fallback_model="speaches-ai/piper-en_US-amy-medium",
        fallback_voice="en_US-amy-medium",
        http_client=httpx.AsyncClient(transport=transport),
    )


async def test_primary_pcm_is_what_gets_played():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "chatter.example"
        return httpx.Response(200, content=b"CHATTER")

    tts = _client_for(handler)
    chunks = [b async for b in tts.stream("hello")]
    assert b"".join(chunks) == b"CHATTER"
    await tts.aclose()


async def test_fallback_fires_when_the_primary_is_down():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "chatter.example":
            return httpx.Response(500, text="gpu asleep")
        return httpx.Response(200, content=b"PIPER")

    tts = _client_for(handler)
    chunks = [b async for b in tts.stream("hello")]
    assert b"".join(chunks) == b"PIPER"
    await tts.aclose()


async def test_fallback_does_not_splice_a_partial_primary():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "chatter.example":
            return httpx.Response(200, content=b"HALF")
        return httpx.Response(200, content=b"PIPER")

    tts = _client_for(handler)
    chunks = [b async for b in tts.stream("hello")]
    assert b"".join(chunks) == b"HALF"
    await tts.aclose()
