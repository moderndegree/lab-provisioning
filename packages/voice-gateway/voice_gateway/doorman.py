"""The Doorman — session continuity across devices. NOT authentication.

The tailnet is the security boundary, and that is the whole of it: anything that
can reach :8772 is a full client. Admitting a device to the tailnet grants voice
control of this system, and removing it there is how that is revoked. Nothing in
this module checks anyone's identity, and adding a second authorization scheme
here would only give the lab two policies that disagree.

What it does own is the answer to "what does a client get when it connects":

    the conversation it is rejoining      (never a fresh one per socket)
    the tail of that conversation         (so a reconnect is not amnesia)
    the briefs still in flight            (so it can say what it is working on)
    results waiting to be spoken          (Return delivers these at a seam)

Before the Ledger this was all per-WebSocket state, which meant a dropped wifi
frame — on a lab that is wifi-only on both boxes — silently started the
conversation over. It also meant the phone and the workstation were two different
assistants that had never met.

WHY IT KEYS ON SPEAKER, NOT DEVICE. One presence, one thread, one memory. The
device is recorded on every turn so "where was I when I said that" survives, but
it does not fragment the conversation. There is one user today and the answer is
always the same; the shape is what matters, because attribution cannot be
recovered retroactively and isolation can be built on top of it later.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from .ledger import Brief, Ledger, Result, Turn

log = logging.getLogger(__name__)

# Fallback identity. There is exactly one user, so this is not a guess that can
# be wrong today — it is the value the attribution columns get until a client
# has a reason to send something else.
DEFAULT_SPEAKER = "brian"


@dataclass
class Admission:
    """Everything the session needs to pick the conversation back up."""

    conversation_id: str
    speaker: str
    device: str
    history: list[Turn] = field(default_factory=list)
    live_briefs: list[Brief] = field(default_factory=list)
    waiting: list[Result] = field(default_factory=list)

    @property
    def resumed(self) -> bool:
        return bool(self.history)


class Doorman:
    def __init__(self, ledger: Ledger, *, history_turns: int) -> None:
        self._ledger = ledger
        # Rows, not exchanges: one exchange is a user turn and an assistant turn.
        # Kept small on purpose — the system prompt is the prefix-cache key on
        # mini and every extra turn pushes the cacheable prefix further from the
        # front of the prompt, which is worth ~12x on time-to-first-token.
        self._rows = max(0, history_turns) * 2

    async def admit(self, *, device: str, speaker: str | None = None) -> Admission:
        who = (speaker or DEFAULT_SPEAKER).strip() or DEFAULT_SPEAKER
        try:
            conversation_id = await self._ledger.conversation_for(who)
            history = await self._ledger.recent_turns(conversation_id, self._rows)
            live = await self._ledger.live_briefs(conversation_id)
            waiting = await self._ledger.deliverable(conversation_id)
        except Exception:  # noqa: BLE001
            # A dead Ledger degrades the presence, it does not silence it. An
            # empty conversation_id is the session's signal that this turn will
            # not be durable; it still talks, it just does not remember, and
            # /v1/status refuses to call itself ok. Failing the socket instead
            # would trade "forgets" for "cannot speak", which is the worse half.
            log.exception("ledger unavailable; admitting %s WITHOUT memory", device)
            return Admission(conversation_id="", speaker=who, device=device)
        log.info(
            "admitted device=%s speaker=%s conversation=%s (%d turns, %d live briefs,"
            " %d waiting results)",
            device,
            who,
            conversation_id,
            len(history),
            len(live),
            len(waiting),
        )
        return Admission(
            conversation_id=conversation_id,
            speaker=who,
            device=device,
            history=history,
            live_briefs=live,
            waiting=waiting,
        )
