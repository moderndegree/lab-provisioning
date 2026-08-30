# voice-gateway

The lab's presence. Speech in, speech out, reasoning on mini — and a bench that
does the slow work without ever stalling the conversation.

Implements [`docs/presence.md`](../../docs/presence.md), which is the authority
on *why* any of this is shaped the way it is. Runs on ser5 as two systemd user
units, provisioned by [`ser5/ansible/roles/voice`](../../ser5/ansible/roles/voice).
Never on mini — mini serves inference only, and these are loops with state.

```
Windows workstation                 ser5                                mini
┌──────────────────┐   WS: PCM    ┌────────────────────────┐   HTTP   ┌───────────────┐
│ voice_client.py  │ ───────────► │ voice-gateway   :8772  │ ───────► │ :8090         │
│  PTT hotkey      │   ONE socket │   PRESENCE             │   SSE    │ 35b-a3b MTP   │
│  openWakeWord    │ ◄─────────── │   vad → stt → llm →tts │ ◄─────── │ 4 slots       │
│  playback        │  PCM + evts  │   judgment / doorman   │          ├───────────────┤
└──────────────────┘              │   return               │   HTTP   │ whisper-stt   │
                                  │                        │ ───────► │ :8092 Vulkan  │
                                  │ voice-speech    :8770  │          ├───────────────┤
                                  │   speaches: TTS (+STT  │          │ :8091         │
                                  │   fallback, ser5 mode) │   SSE    │ 27b deep      │
                                  ├────────────────────────┤ ───────► │ 1 slot        │
                                  │ voice-bench            │          └───────────────┘
                                  │   BENCH — briefs       │
                                  │   searxng :8888 ───────┼──► the world
                                  │   hermes (pinned)      │
                                  └───────────┬────────────┘
                                    ledger.db │ WAL — the only shared state
                                              └──► /data/brain/notes/errands
```

**Two processes, one database.** The presence holds the floor and never blocks.
The bench turns briefs into results and is the only thing with a route off the
property. They share nothing but `ledger.db`, which is why either can restart
without losing an errand: the row *is* the handoff.

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

## The 500 ms target, and where the time actually goes

The strategy asks for the first audible word inside 500 ms. Measured through the
live gateway on 2026-08-30, push-to-talk, identical stimulus, before and after
turning 802.11 power save off on mini:

| stage | power save on (n=10) | power save off (n=12) |
|---|---:|---:|
| STT finalize — median | 64 ms | 62 ms |
| STT finalize — **p95** | **207 ms** | **75 ms** |
| model → first sentence | 357 ms | 374 ms |
| synthesis | 281 ms | 305 ms |
| to first audible word — median | 742 ms | **740 ms** |
| to first audible word — **p95** | **1088 ms** | **970 ms** |

**The wifi fix bought consistency, not speed, and that is worth saying plainly
rather than dressing up.** The link itself improved enormously — ser5 → mini over
60 pings went from avg 22.064 ms / max 175.010 ms / mdev 36.632 ms to avg
6.543 ms / max 11.671 ms / mdev 1.793 ms, on a -56 dBm link at 650 Mbit/s, so
none of it was ever radio quality. STT finalize is the stage most exposed to that
link and its p95 fell 207 → 75 ms accordingly. The transcript also stopped
mishearing the box's own name.

The median did not move, because **the median was never network-bound**. Of the
740 ms, 679 is compute: 374 ms of model before there is a sentence worth
speaking, and 305 ms of piper synthesising it on ser5's CPU. That is where the
remaining 240 ms has to come from, and there are only two places to look:

- **Synthesis, 305 ms.** The largest single block, and the one question this
  package has never asked. `roles/voice/defaults/main.yml` says "TTS stays on
  ser5 — nothing measured suggested moving it" — which is the same sentence that
  was true of STT until someone measured mini's GPU and won 671 ms → 66 ms. Gate
  it on a GPU-contention rehearsal against live :8090 traffic first, exactly as
  the STT move was.
- **Model → first sentence, 374 ms.** TTFT is 75 ms warm, so ~300 ms of this is
  tokens generated before the chunker has a full sentence. The "LEAD WITH THE
  ANSWER" instruction already cut it 532 → 284 ms once. Re-running the
  `first_chunk_clause_break` A/B is nearly free and it lost by only 155 ms when
  synthesis cost 306 ms; the arithmetic changes if synthesis gets cheap.

A later run on a genuinely quiet box came in at **576 ms median** (n=10,
model → first sentence 253 ms, synthesis 263 ms) — the best figure yet and only
76 ms over. The same configuration measured **917 ms** half an hour earlier while
mini was still busy. Both numbers are real, and the gap between them is not
noise: it is the same GPU-contention finding as the load test below, seen from
the idle side. What the presence costs depends on what else is touching mini.

So the honest statement is a range, not a figure: **576–917 ms idle depending on
what else has the GPU, against a 500 ms target.** `voicebench.py` prints
`BUDGET MISSED` and by how much, rather than quietly revising the target to
whatever the run produced.

## Under load — the only test that matters

> *"the same numbers hold while errands are running. Within ~15% of idle."*

It did not. Measured 2026-08-30 with a real errand on mini's `:8091`:

| stage | idle | 1 errand running | drift |
|---|---:|---:|---:|
| STT finalize | 61 ms | 155 ms | +155% |
| model → first sentence | 498 ms | 2041 ms | **+310%** |
| synthesis (ser5 CPU) | 284 ms | 263 ms | −8% |
| **first audible word** | **861 ms** | **2451 ms** | **+184%** |

`--load 2` gave +122%, `--load 1` gave +184%. Not noise, and the shape is the
whole diagnosis: **everything hosted on mini collapses, everything hosted on
ser5 is untouched.** `:8090` and `:8091` are two llama-server processes sharing
one GPU and they do not isolate. `voice-bench.service`'s `Nice=10` and
`CPUWeight=20` are decorative against this — the contention is not CPU.

**So the bench yields.** The presence claims a floor in the Ledger at the moment
of intent — the client's `start` frame, sent at key-press, *before* the sentence
is finished — which buys a second or two for the bench to abandon its generation
before the presence needs the GPU. The bench also refuses to *begin* an errand
while the floor is held.

The floor is a deadline, not a lock (`voice_floor_hold_s`, default 5 s): the
presence pushes it forward and never releases it, so a crashed gateway frees the
bench by itself. Nothing blocks and nothing can deadlock.

The cost is real and is the point: **an errand interrupted mid-generation throws
away the tokens it had already paid for and restarts that phase.** A chatty hour
makes errands much slower. That is the strategy's own accepted tradeoff — "deep
work slower than its peak, because steerability requires yielding" — extended
from steering to contention.

A yield is not a completion, and the code refuses to confuse them: abandoning a
generation raises `FloorTaken` rather than returning what it had, because banking
a partial answer would be the system claiming an errand is finished when it
isn't.

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

## The parts, and what each one owns

| | Owns | Lives in |
|---|---|---|
| **Presence** | the mic, the voice, turn-taking, the floor. Never blocks, never touches the world | `session.py` |
| **Judgment** | answer-now vs. errand, in ~zero milliseconds. Biased toward dispatching | `judgment.py` |
| **Ledger** | durable conversation, briefs, results, egress — each attributed to a speaker and a device | `ledger.py` |
| **Doorman** | what a client gets when it connects. Session continuity, *not* authentication | `doorman.py` |
| **Bench** | briefs into results, including retrieval. Interruptible, checkpointed, voiceless | `bench/` |
| **Return** | when a finished result re-enters the conversation, and when it expires unspoken | `delivery.py` |
| **Hands** | acting on real systems, and the spoken consent that gates it | `hands/` |

## The Ledger

`/data/services/voice/ledger.db`, WAL, inside restic's `/data`. Before it existed
conversation was a Python list on the WebSocket — and both boxes are wifi-only,
so a dropped socket is routine rather than exotic. Every one of them silently
started the conversation over, and the phone and the workstation were two
assistants that had never met.

Attribution is recorded from day one even though there is exactly one user and
the answer is always the same. Isolation can be built later on top of attributed
history; attribution cannot be recovered retroactively.

```sh
sqlite3 /data/services/voice/ledger.db \
  "select speaker, device, count(*) from turns group by 1,2"
sqlite3 /data/services/voice/ledger.db "select ts, endpoint, query from egress"
```

That second query is the locality claim in a form you can read: every outbound
query, and the brief that justified it. A row with a null `brief_id` is the
presence having reached the network directly, which is what
`voice_presence_network: true` still permits until the bench takes retrieval
over. `roles/voice/tasks/verify.yml` flags both.

## Locality

**Facts may come in. Thinking never goes out.** Two doors in the same wall, and
the second stays shut *because* the first opens.

The rule is not "the gateway may not use the network" — the presence has to reach
mini for speech recognition and for every token it speaks, and mini is a
different machine. It is **the presence may reach the property, and not the
world**: loopback, the lab LAN, the tailnet. Only the bench's cgroup is exempt.

`voice_locality_enforce: true` loads an nftables table that enforces exactly
that, and verify proves it by opening a socket from the gateway's own cgroup and
requiring it to fail — because a cgroupv2 rule with a stale path parses, loads,
appears in `nft list ruleset`, and matches nothing.

## Design

**Sentence chunking is the main lever.** The model stream is cut at sentence
boundaries and each sentence is synthesised as it lands, so you hear sentence
one while sentence three is still generating. Without it a 12-second answer is
12 seconds of silence.

**Barge-in.** VAD keeps running on the inbound stream while the assistant is
speaking. Real speech cancels the in-flight model response and drops queued
audio. This is the single most common reason a voice loop gets abandoned.

**Deterministic judgment, not tool calls.** A native tool call costs a second
full pass through the model before the first audible word. `judgment.py` matches
prefixes in about zero milliseconds:

```
"what is the kv ceiling on mini"          → mini:8090, streams, spoken
"search for strix halo benchmarks"        → a brief; "on it"; the bench works
"have hermes wire up the searxng verify"  → a brief, capability=delegation
"actually, make it vulkan only"           → amends the brief already in flight
```

It also reads the answer *after* it is spoken: "I don't know" and "that needs
current information" are the model telling us, for free and after the fact, that
the turn was an errand. That catches the long tail no prefix ever will.

`VOICE_NATIVE_TOOLS=true` is the seam if that should ever change. It is off
until a bench number justifies it.

**One socket, per-turn mode.** The client used to open a connection per turn, so
each could pick its own endpointing. It now holds one and sends
`{"type":"start","mode":...}` instead — because a per-turn socket has nothing to
*receive* on, and an errand that finishes twenty minutes later has to come back
through the same voice. Two sockets would have been two things that can speak.

**Steering costs yielding.** The bench re-reads the brief before each phase and
every few dozen tokens mid-generation; when the revision has moved it abandons
what it was writing and restarts from the new wording. That makes deep work
slower than its peak, which is the trade the strategy accepts by name.

**Hermes is one capability behind the bench, not the policy point.** `hermes
proxy` on :8645 is a Nous Portal proxy that forwards to a Nous subscription
regardless of its configured `model.base_url`, and it is down. Conversation goes
straight to mini so it streams and stays local.

The bench names `--provider` and `-m` on *every* invocation rather than trusting
Hermes' configured default. `docs/todo.md` records four live hosted credentials
on this box — openrouter, opencode-zen, copilot, xai-oauth — each reachable on a
fallback or an explicit flag, and Hermes cannot be version-pinned because its
installer always fetches latest. That is the wrong component to make the sole
door in a wall whose whole purpose is "no thinking leaves the property", so the
bench holds the policy and Hermes is something it calls.

## Wire protocol

One protocol, every client — the desktop client, the bench, and any future
ESP32 satellite. Defined in [`voice_gateway/protocol.py`](voice_gateway/protocol.py).

```
client → gateway   text  {"type":"hello","device":str,"mode":"ptt"|"vad",
                          "sample_rate":16000,"speaker":str?}
                   bin   16 kHz mono int16 PCM, 20 ms frames (640 bytes)
                   text  {"type":"end"}     ptt release — the endpoint, no VAD wait
                   text  {"type":"cancel"}  barge-in / abort

client → gateway   text  {"type":"start","mode":"ptt"|"vad"}   opens a turn

gateway → client   text  {"type":"ready","conversation":str,"resumed":int,
                          "working":int,"waiting":int}
                   text  {"type":"final","text":...}
                   text  {"type":"speaking","seq":n,"text":...}
                   bin   int16 PCM at 24000 Hz
                   text  {"type":"done"} | {"type":"cancelled"}
                   text  {"type":"brief","id":...,"statement":...}
                   text  {"type":"working","n":...}
                   text  {"type":"notice","text":...}   returned detail
                   text  {"type":"error","message":...}
```

Audio out is 24000 Hz because speaches resamples piper's native 22050 on the way
out, and `response_format: pcm` returns bare `audio/pcm` with no rate to parse.
Getting it wrong does not error — the voice just plays at the wrong pitch — so
`roles/voice/tasks/verify.yml` asserts it against the WAV header.

## Running it

Provisioned: `enable_voice: true` in `ser5/ansible/group_vars/all.yml`, then
`cd ser5 && make provision`. Three more flags, each off until turned on
deliberately:

| flag | what it changes |
|---|---|
| `enable_voice_bench` | deploys `voice-bench.service` AND flips the presence from refusing errands out loud to dispatching them. One flag, because two could disagree and the presence would promise work nothing was doing |
| `voice_presence_network` | `false` is this phase's actual claim — no network on the fast path at all. `true` only until the bench takes retrieval over |
| `voice_locality_enforce` | loads the nftables table that makes the locality claim true rather than stated |

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

## Tests

```bash
pip install -e "packages/voice-gateway[dev]"
pytest packages/voice-gateway/tests -q
```

They do not try to prove the loop sounds good — that is what `voicebench.py` and
a real microphone are for. They cover what listening cannot judge: the state
machine, the Ledger, and the ordering between them. Every one of them was
written for a bug that presented to a person as **"sometimes it just doesn't hear
me"** and produced no error anywhere.

The Silero graph is the only real dependency; the suite skips itself if it is
absent and says where to get it.

## Benchmarking

```bash
python bench/voicebench.py --mode ptt -n 20
python bench/voicebench.py --mode vad -n 20     # what VAD endpointing costs
python bench/voicebench.py --load 2 -n 20       # idle pass, then under deep load
python bench/voicebench.py --profile remote     # a declared, honestly worse budget
```

`--load N` is the only test that matters: it drives N concurrent deep
generations on mini's :8091 while measuring the presence on :8090, then prints
the drift between the two passes. Baseline first, in the *same* run — comparing
against a number from last week would fold in a different prefix-cache state and
attribute all of it to the load.

Budgets are declared in the script (`BUDGET_MS`): 500 ms in-home, 1500 ms remote.
A run either meets its budget or says by how much it missed. A target with no
number attached is the one thing this repo does not do.

The stimulus is synthesised by the lab's own TTS so every run gets byte-identical
input. Audio is paced on a 20 ms wall clock like a real microphone — blasting it
at the socket would report a finalize time no live turn can achieve.

It is a **timing** harness, not an accuracy one: a TTS→STT round trip
mispronounces proper nouns ("mini" comes back as "many"). Judge words from a real
microphone.
