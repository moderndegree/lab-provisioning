# voice-gateway

The lab's voice loop. Speech in, speech out, reasoning on mini.

Runs on ser5 as a systemd user unit, provisioned by
[`ser5/ansible/roles/voice`](../../ser5/ansible/roles/voice). Never on mini —
mini serves inference only, and this is a loop with per-connection state.

```
Windows workstation                 ser5                                mini
┌──────────────────┐   WS: PCM    ┌────────────────────────┐   HTTP   ┌───────────────┐
│ voice_client.py  │ ───────────► │ voice-gateway   :8772  │ ───────► │ :8090         │
│  PTT hotkey      │              │   silero VAD           │   SSE    │ llama-quality │
│  openWakeWord    │ ◄─────────── │   route / chunk        │ ◄─────── │ 4 slots       │
│  playback        │  PCM + evts  │                        │          ├───────────────┤
└──────────────────┘              │ voice-speech    :8770  │   HTTP   │ whisper-stt   │
                                  │   speaches: TTS (+STT  │ ───────► │ :8092 Vulkan  │
                                  │   fallback, ser5 mode) │          └───────────────┘
                                  └────────────────────────┘
                                       │ searxng :8888
                                       └ hermes chat -q
```

STT defaults to mini (`voice_stt_backend: mini`) — see Measured latency below.
`voice_stt_backend: ser5` routes it back through speaches instead, no code
changes either way.

## Measured latency

Push-to-talk, ~2.3 s spoken command, via
[`bench/voicebench.py`](bench/voicebench.py). Current default is STT on mini's
GPU (`voice_stt_backend: mini`); the ser5-CPU path (speaches) is still there
as a fallback, n=15/n=10 respectively:

| stage | ser5 CPU (was default) | mini GPU (now default) |
|---|---:|---:|
| STT finalize | 671 ms | 66 ms |
| model → first sentence | 349 ms | 346 ms |
| synthesis (time to first byte) | 273 ms | 306 ms |
| **to first audible word** | **1305 ms** | **779 ms** |

mini's whisper-server needs no shim — `--inference-path` renames its one route
to the exact OpenAI path this package's `SttClient` already calls (see
[`mini/ansible/roles/whispercpp`](../../mini/ansible/roles/whispercpp)). Adopted
2026-08-30 on this measured win, gated on a GPU-contention rehearsal against
live LLM traffic first — that role's defaults have the full numbers. p95 on
the mini path is 1165 ms. A web-search turn adds roughly 1.6 s for the
SearXNG round trip regardless of STT backend.

mini's prefix cache is doing real work here: time-to-first-token measured
**774 ms cold, 75 ms warm**, which is why the system prompt is byte-stable and
kept in a file rather than built per request.

Nothing above is an estimate. Re-run the bench before changing any of it.

## Why these engines

Both original choices lost on measurement and were replaced. STT, 3.88 s clip:

| path | tiny.en | base.en | small.en |
|---|---:|---:|---:|
| faster-whisper in-process | 237 ms | 408 ms | 1260 ms |
| **speaches** (HTTP container) | 460 ms | **708 ms** | 1876 ms |
| WhisperLive REST | 3060 ms | 3060 ms | 3100 ms |
| WhisperLive streaming (tail lag) | 2300 ms | 2750 ms | 2200 ms |

WhisperLive was picked first because streaming partials should make the finalize
cheap. Its partials are real and do arrive during speech — but its server
refuses to transcribe a chunk under one second (`backend/base.py`:
`if duration < 1.0: time.sleep(0.1)`), so an utterance's tail waits out that
window plus a whisper pass. The lag is identical across model sizes, which
proves it is the cadence rather than the compute; no flag reaches it.

TTS, time to **first byte** — what a listener experiences, not total synthesis:

| engine | TTFB | RTF |
|---|---:|---:|
| **speaches + piper** en_US-amy-medium | **250 ms** | 0.047 |
| openedai-speech + piper (`tts-1`) | 1028 ms | 0.349 |
| speaches + Kokoro-82M ONNX int8 | 5592 ms | 1.125 |

Kokoro is the better-sounding model and is not viable on this CPU — RTF above 1
means it synthesises slower than you can listen. It would need a GPU.

`base.en` is the STT default over `tiny.en` because on a 2.25 s command it heard
*"the GPU temperature on Mini"* where tiny.en heard *"on many"*. 240 ms is worth
paying to get the name of the box right. Set `VOICE_STT_MODEL` to trade back.

The table above is all **ser5 CPU** — the engine bake-off for what runs on this
box. It never asked what mini's GPU would do to the same problem, because the
lab's hard rule was "speech models are I/O codecs, keep them off mini." That
rule was reasoned from ser5's own GPU being useless here (a 2020-era iGPU with
no matrix cores), not from mini's — mini's Strix Halo GPU reports
`matrix cores: KHR_coopmat`, the same hardware already doing 85-168 tok/s LLM
decode. Measured 2026-08-30: 66ms vs 671ms. See `voice_stt_backend` above and
[`mini/ansible/roles/whispercpp`](../../mini/ansible/roles/whispercpp) for the
full story, including the GPU-contention rehearsal that gated adopting it.

## Design

**Sentence chunking is the main lever.** The model stream is cut at sentence
boundaries and each sentence is synthesised as it lands, so you hear sentence
one while sentence three is still generating. Without it a 12-second answer is
12 seconds of silence.

**Barge-in.** VAD keeps running on the inbound stream while the assistant is
speaking. Real speech cancels the in-flight model response and drops queued
audio. This is the single most common reason a voice loop gets abandoned.

**Deterministic routing, not tool calls.** A native tool call costs a second
full pass through the model before the first audible word. `router.py` matches
two prefixes in about zero milliseconds and folds the result into the *same*
call that answers:

```
"what is the kv ceiling on mini"          → mini:8090, streams, Tier L
"search for strix halo benchmarks"        → SearXNG, then answer
"have hermes wire up the searxng verify"  → "on it", then `hermes chat -q` in
                                             the background, spoken when done
```

`VOICE_NATIVE_TOOLS=true` is the seam if that should ever change. It is off
until a bench number justifies it.

**Hermes via CLI, not :8645.** `hermes proxy` is a Nous Portal proxy that
forwards to a Nous subscription regardless of its configured `model.base_url`,
and it is currently down. Conversation goes straight to mini so it streams and
stays Tier L.

## Wire protocol

One protocol, every client — the desktop client, the bench, and any future
ESP32 satellite. Defined in [`voice_gateway/protocol.py`](voice_gateway/protocol.py).

```
client → gateway   text  {"type":"hello","device":str,"mode":"ptt"|"vad","sample_rate":16000}
                   bin   16 kHz mono int16 PCM, 20 ms frames (640 bytes)
                   text  {"type":"end"}     ptt release — the endpoint, no VAD wait
                   text  {"type":"cancel"}  barge-in / abort

gateway → client   text  {"type":"ready"}
                   text  {"type":"final","text":...}
                   text  {"type":"speaking","seq":n,"text":...}
                   bin   int16 PCM at 24000 Hz
                   text  {"type":"done"} | {"type":"cancelled"}
                   text  {"type":"notice","text":...}   delegated result
                   text  {"type":"error","message":...}
```

Audio out is 24000 Hz because speaches resamples piper's native 22050 on the way
out, and `response_format: pcm` returns bare `audio/pcm` with no rate to parse.
Getting it wrong does not error — the voice just plays at the wrong pitch — so
`roles/voice/tasks/verify.yml` asserts it against the WAV header.

## Running it

Provisioned: `enable_voice: true` in `ser5/ansible/group_vars/all.yml`, then
`cd ser5 && make provision`.

By hand, from a checkout:

```bash
pip install -e packages/voice-gateway
VOICE_VAD_MODEL_PATH=/data/services/voice/models/silero_vad.onnx \
  python -m voice_gateway
```

Every setting is an environment variable; see
[`voice_gateway/config.py`](voice_gateway/config.py), where each default carries
the measurement or incident behind it.

## Desktop client

On the workstation, not ser5 — the gateway never touches an audio device.

```bash
pip install -e "packages/voice-gateway[client]"
python packages/voice-gateway/clients/desktop/voice_client.py --host ser5
python packages/voice-gateway/clients/desktop/voice_client.py --host ser5 --wake-word hey_jarvis
```

Hold `ctrl+alt+space` to talk. Key release *is* the endpoint, which skips the
VAD hangover entirely — that is why push-to-talk is the fast path. With
`--wake-word` the phrase opens a turn hands-free and the gateway's VAD decides
when you stopped.

## Open WebUI

`roles/voice/tasks/main.yml` also points Open WebUI's `audio.stt.*`/
`audio.tts.*` PersistentConfig at whichever STT backend is active and at
speaches for TTS — automatic whenever both `enable_voice` and
`enable_openwebui` are true, no separate step. Gives mic/speaker in the
browser and on the iPhone. This is Open WebUI's own record → transcribe →
send flow, not this package's streaming loop — no barge-in, no sentence
chunking, no sub-second turnaround. Use the desktop client above when that
matters.

`voice-speech`'s quadlet joins `openwebui.network` when `enable_openwebui` is
true (in addition to its usual loopback publish) specifically so this works:
Open WebUI's backend calls STT/TTS from inside its own container, where
`127.0.0.1` means the openwebui container itself, not ser5.

## Benchmarking

```bash
python bench/voicebench.py --mode ptt -n 20
python bench/voicebench.py --mode vad -n 20     # what VAD endpointing costs
python bench/voicebench.py --wav sample.wav -n 20
```

The stimulus is synthesised by the lab's own TTS so every run gets byte-identical
input. Audio is paced on a 20 ms wall clock like a real microphone — blasting it
at the socket would report a finalize time no live turn can achieve.

It is a **timing** harness, not an accuracy one: a TTS→STT round trip
mispronounces proper nouns ("mini" comes back as "many"). Judge words from a real
microphone.
