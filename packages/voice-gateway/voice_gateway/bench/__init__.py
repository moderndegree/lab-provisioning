"""The Bench — staff who never speak.

Turns briefs into results. Deep model, retrieval, and eventually hands. Slow on
purpose, interruptible on purpose, and voiceless on purpose: nothing in this
package may write to a WebSocket or synthesise a word. Results re-enter the
conversation through the presence, at a seam, in the presence's own voice.

THIS PACKAGE HOLDS THE ONLY ROUTE OFF THE BOX. Web retrieval lives here and
nowhere else. Two doors in the same wall, and only one of them opens:

    facts may come in     — the bench may fetch public content
    thinking never goes out — no conversation is routed to a model that is not
                              yours, and no utterance is ever forwarded as a
                              search query

The second door stays shut *because* the first one opens. A system with a live
outbound path and a model gateway sitting next to it will drift through the
wrong door on the day something is slow, so the separation is structural: the
presence's process has no client that can reach either.

WHY IT IS A SEPARATE PROCESS. `voice-bench.service` runs beside
`voice-gateway.service`, sharing only the Ledger. That buys three things a
task in the gateway's event loop could not: the bench can be niced and
preempted so deep work cannot starve the fast path; a bench crash does not take
the voice down; and the egress rule has exactly one cgroup to name, which is what
turns "local only" from a claim in a comment into something verifiable on the
wire.
"""

from .hermes import HermesDelegate
from .web_search import WebSearch

__all__ = ["HermesDelegate", "WebSearch"]
