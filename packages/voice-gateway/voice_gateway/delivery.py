"""Return — when a finished result re-enters the conversation, and when it doesn't.

The bench produces answers on its own schedule. This decides when one gets
spoken, and the decision is almost entirely about NOT speaking:

    not mid-turn      the presence holds the floor; a result that interrupts an
                      answer in progress is a second voice, which is the one
                      thing this system is built to never have
    not immediately   `quiet_s` of silence first, so a result landing half a
                      second after you stopped talking does not read as the
                      assistant having been about to say something anyway
    not forever       past `expires_ts` a result is no longer volunteered. It is
                      still there to be asked about; it just stops arriving
                      unprompted. The result of a question asked yesterday,
                      spoken out of nowhere this morning, is worse than silence

WHY IT VOLUNTEERS AT ALL, rather than waiting to be asked. "Results return at a
conversational seam, correctly attributed, twenty minutes later if need be" is
the deep-work criterion, and a system that only answers when prompted has not met
it — it has just moved the polling to the human.

ATTRIBUTION IS THE HARD PART OF SAYING IT. Twenty minutes later, "llama.cpp wins
on both" is a non-sequitur. The phrasing below always names what was asked, which
is why `phrase` needs the brief and not just the result text.
"""

from __future__ import annotations

import logging
import time

from .ledger import Brief, Ledger, Result

log = logging.getLogger(__name__)


class Returner:
    def __init__(self, ledger: Ledger, *, quiet_s: float) -> None:
        self._ledger = ledger
        self._quiet_s = quiet_s

    def seam(self, *, idle: bool, quiet_for: float) -> bool:
        """Is this a moment where a result may be spoken?"""
        return idle and quiet_for >= self._quiet_s

    async def next_due(self, conversation_id: str) -> tuple[Result, Brief] | None:
        """The oldest undelivered, unexpired result, with the brief behind it.

        Expiry is enforced by the query rather than swept: a result nobody was
        around to hear simply stops appearing here, and stays in the Ledger to be
        asked about. Nothing is deleted — an errand that ran is a thing that
        happened.
        """
        due = await self._ledger.deliverable(conversation_id)
        if not due:
            return None
        result = due[0]
        brief = await self._ledger.get_brief(result.brief_id)
        if brief is None:  # cannot attribute it, so do not say it
            log.warning("result %s has no brief; not speaking it", result.id)
            await self._ledger.mark_delivered(result.id)
            return None
        return result, brief

    def phrase(self, result: Result, brief: Brief) -> str:
        """What the presence actually says, in its own voice.

        Always opens by naming the errand. The listener has had a whole
        conversation since asking, and an answer that arrives without its
        question is just an interruption with facts in it.
        """
        waited = time.time() - brief.created_ts
        lead = f"About {brief.statement}"
        if waited > 900:
            lead = f"Coming back to {brief.statement}"
        return f"{lead} — {result.text.strip()}"

    async def delivered(self, result_id: int) -> None:
        await self._ledger.mark_delivered(result_id)
