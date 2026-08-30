# The Presence — strategy

The authority on *why* the voice system is shaped the way it is. It contains no
implementation; `packages/voice-gateway/README.md` and
`ser5/ansible/roles/voice` are the how, and where the two disagree this document
is the one that is right and the code is the one that is wrong.

## Context

You want an always-on system you talk to naturally, hosted on the MS-S1 Max, that never
stalls the conversation to do hard work, and that other devices on your tailnet can reach
as glass. Local, with one scoped exception for web retrieval. The organizing difficulty is
not "run a model at home" — it is that you specified two irreconcilable response-time
regimes in one product and required that the slow one never contaminate the fast one, while
the conversation itself remains able to redirect work already in flight.

This document is the strategy. It contains no implementation.

---

## What it is

**A single conversational presence that always holds the floor, backed by staff who never
speak.** One voice, one thread, one memory, resident on the MS-S1 Max. When a request
exceeds what it can answer in the moment, it dispatches an errand and keeps talking. The
errand is not a frozen prompt — it is a living brief the ongoing conversation can amend.
Results come back through the same voice, at a seam in the conversation, attributed to
what you asked.

## What it is not

- Not a router or a menu. The user never sees a handoff, never picks a model, never waits.
- Not a second brain on any client. Clients are microphone, speaker, and wake detection.
- Not a job queue with notifications. Errands are steerable, not filed.
- Not resilient to the host being down. When the MS-S1 Max is down, the system is down.
  That is what keeps the clients thin, and it is a choice, not a defect.
- **Not a capability ladder to the cloud.** No thinking happens elsewhere. The ceiling on
  intelligence is this hardware's ceiling, permanently.

## The bet

Presence beats intelligence at the microphone. The front-of-house model is chosen for
latency, not for score; its job is to be *there*, immediately, every time. Intelligence
arrives asynchronously and is re-voiced by the same presence. The system is allowed to be
shallow in conversation. It is never allowed to claim an errand is finished when it isn't.

## The one exception — retrieval, not reasoning

Web search is permitted, inside Hermes, as errands require it. The boundary is a
**capability** boundary, not a network one:

- **Facts may come in. Thinking never goes out.** Hermes may fetch public content. Hermes
  may not route your conversation to a model that isn't yours. These are two different doors
  in the same wall, and the second one stays shut *because* the first one opens — a system
  with a live outbound path and a model gateway sitting next to it will drift through the
  wrong door on the day something is slow.
- **Only the Bench reaches the network. Never the Presence.** If the fast path can touch the
  internet, the fast path's latency is the internet's latency and the response guarantee is
  gone. Web access belongs to errands exclusively.
- **Queries are terms, not transcripts.** The search string is the only thing that leaves.
  Your utterance is not a search query and is never forwarded as one.

## The staff entrance

**Everything the Presence delegates goes through Hermes — models, retrieval, and actions —
and Hermes owns access control.** The voice system does not implement a second
authorization scheme. One policy point, not two that disagree.

Two things this makes true, and they are not optional:

- **Hermes' configuration is now a first-class artifact of this system, not an incidental
  service.** It is the entire trust boundary: what can be reached, what can be run, and —
  because Hermes also serves models — whether "local only" is still true. The answer to
  "is this local?" becomes "whatever Hermes is currently serving," which means it has to be
  verified as running state rather than assumed from config. Your own rule applies here.
- **Hermes owns *whether*. The Presence owns *whether you meant it.*** Delegation covers
  permission; it cannot cover misheard intent. An authorization system can confirm you are
  allowed to restart something. It cannot know that the utterance was a transcription error,
  or aimed at the dog. Voice introduces a failure mode auth models don't have: a request
  that was never made. So irreversible actions get spoken confirmation from the Presence
  regardless of what Hermes would allow.

A denial is re-voiced, never surfaced. Hermes refusing is not a conversation; the Presence
saying "I can't do that one" is.

## Responsibilities

| Responsibility | Owns |
|---|---|
| **Presence** | The mic, the voice, turn-taking, persona, the floor. Never blocks. Never delegates its voice. Never touches the network. |
| **Judgment** | Deciding, in a fraction of the turn budget, answer-now vs. errand. Biased toward dispatching. |
| **Brief** | The mutable statement of what is wanted. Owned by Presence, read by the bench, amendable mid-flight. |
| **Bench** | Turning briefs into results, including outward retrieval. Interruptible, checkpointed, voiceless. |
| **Hands** | Acting on real systems. *Permission* is Hermes'. *Intent* stays here — see below. |
| **Ledger** | Durable conversation, briefs, results — each attributed to a speaker and a device. The only place state lives. |
| **Doorman** | Session continuity across devices. *Not* authentication — the tailnet is the boundary. |
| **Return** | When a finished result re-enters the conversation — and when it expires unspoken. |

## Placement

- **All inference on the MS-S1 Max.** It is the only machine with real capability; splitting
  inference to protect the fast path would degrade the thing that must be best.
- **The fast path is protected by silicon where possible, policy where not.** Wake, speech
  in/out, and the resident conversational model belong on hardware the deep tier cannot
  contend for. The NPU's strategic value here is *immunity*, not throughput. Where that
  isn't achievable, the same guarantee is bought with permanent residency and hard
  preemption — weaker, and to be treated as the fallback it is.
- **SER5 is the secretary, not a second brain.** Ledger, clock, wake-ups, gateway, consent
  surface, retrieval, and the execution of Hands. Things that must survive the big box
  being saturated or rebooting. It is also the only box that talks outward.

## Access

**Reachability is authorization for talking. Hermes is authorization for doing.** Anything
on the tailnet is a full client — the Doorman shrinks to session continuity and nothing
else. But the tailnet does not grant action; Hermes does, at the staff entrance.

The consequence, stated plainly: **admitting a device to the tailnet grants voice control
of the system.** Device admission and revocation is the whole conversational security
model, and a lost phone is a live microphone until it is removed there. What that voice can
then *do* is bounded by Hermes, which is the reason the two boundaries are separate.

## Scale later

One user now. Skip voice identification, memory partitions, permissions, and concurrent
independent conversations — none of them earn their weight yet.

Preserve one thing anyway: **the Ledger attributes every turn and every brief to a speaker
and a device from day one**, even while the answer is always the same. Isolation can be
built later on top of attributed history; attribution cannot be recovered retroactively.

Note for that day, not for now: several people in one room is a different product from one
assistant with several users. One presence holding a floor for a group is a room system.
The Ledger should not foreclose either reading.

## Success

- **Feel** — acknowledgment audible within 500 ms, every time, with no exceptions. First
  useful word p50 under ~600 ms in-home. Barge-in silences speech in under 200 ms. Measured
  mic-close to speaker-out, not at an API boundary.
- **Under load** — the same numbers hold while errands are running. Within ~15% of idle.
  This is the only test that matters; everything else is table stakes.
- **Deep work** — an errand amended mid-flight demonstrably changes its output. Results
  return at a conversational seam, correctly attributed, twenty minutes later if need be.
- **Thin clients** — wipe any client, lose nothing. A new client is glass plus audio.
  Host down means clients silent.
- **Locality** — every outbound packet originates from the Bench, goes to a retrieval
  endpoint, and traces to a named brief. No egress from the Presence, ever. No prompt
  leaves the property. Verified on the wire, not in config.

## Sequence

- **Now — it talks, and it's yours.** One room, one voice, always on, no deep tier and no
  network at all. Conversational feel proven by measurement at the microphone, and honest
  refusal of anything beyond it. If the feel isn't there with nothing else running, no
  later architecture rescues it.
- **Next — it runs errands without leaving the room.** Briefs dispatched, conversation
  never stalls, results return correctly. Retrieval opens here, Bench-only. Clients across
  the tailnet, including away, with a declared and worse remote budget. Fast path proven
  immune to deep load.
- **Later — the errand becomes a conversation.** Mid-flight steering, system access under
  consent, awareness of several briefs at once, proactive return. This is where it stops
  being a good voice assistant.

## Tradeoffs accepted

Permanent idle cost, and no reclaiming it. Occasional shallow conversational answers, in
exchange for never stalling. A single point of failure and a fixed capability ceiling.
Deep work slower than its peak, because steerability requires yielding. Away-from-home
turns that are honestly worse rather than falsely equal. A first phase whose most important
work — ledger and brief — has no demo value. Search queries as the one thing that leaves
the house.

## Still yours to decide

What idle power and heat you will pay for readiness. The persona and voice itself. How long
conversation is retained. Which actions count as irreversible enough to require spoken
confirmation — Hermes cannot answer that one for you.
