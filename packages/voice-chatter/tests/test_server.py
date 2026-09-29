"""HTTP surface of voice-chatter, with the engine swapped for a fake."""

from __future__ import annotations

import wave
from io import BytesIO

from fastapi.testclient import TestClient

from voice_chatter.server import FakeSynth, SAMPLE_RATE, build_app, float_to_pcm16
import numpy as np


def client(voices: list[str] | None = None) -> tuple[TestClient, FakeSynth]:
    synth = FakeSynth(voices)
    return TestClient(build_app(synth)), synth


def test_health_lists_voices():
    api, _ = client(["default", "presence"])
    body = api.get("/health").json()
    assert body["status"] == "ok"
    assert body["voices"] == ["default", "presence"]


def test_speech_pcm_is_s16le_and_uses_the_named_voice():
    api, synth = client()
    resp = api.post(
        "/v1/audio/speech",
        json={"input": "On it.", "voice": "default", "response_format": "pcm"},
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("audio/pcm")
    assert len(resp.content) >= 2
    assert synth.calls == [("On it.", "default")]


def test_unknown_voice_is_a_client_error_not_a_crash():
    api, synth = client(["default"])
    resp = api.post("/v1/audio/speech", json={"input": "hello", "voice": "nobody"})
    assert resp.status_code == 400
    assert synth.calls == []


def test_path_shaped_voice_names_are_rejected():
    api, _ = client()
    resp = api.post(
        "/v1/audio/speech", json={"input": "hello", "voice": "../secret"}
    )
    assert resp.status_code == 400


def test_wav_container_declares_24k():
    api, _ = client()
    resp = api.post(
        "/v1/audio/speech",
        json={"input": "ok", "voice": "default", "response_format": "wav"},
    )
    assert resp.status_code == 200
    with wave.open(BytesIO(resp.content), "rb") as wav:
        assert wav.getframerate() == SAMPLE_RATE
        assert wav.getnchannels() == 1
        assert wav.getsampwidth() == 2


def test_float_to_pcm16_clips():
    pcm = float_to_pcm16(np.array([2.0, -2.0, 0.0], dtype=np.float32))
    samples = np.frombuffer(pcm, dtype=np.int16)
    assert samples[0] == 32767
    assert samples[1] == -32767
    assert samples[2] == 0
