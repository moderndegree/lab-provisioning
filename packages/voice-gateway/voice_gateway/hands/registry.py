"""The action registry and the two-turn consent it enforces."""

from __future__ import annotations

import asyncio
import logging
import re
import shlex
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

Runner = Callable[[], Awaitable[str]]


@dataclass(frozen=True)
class Action:
    name: str
    """Short handle, used in logs and in the Ledger."""
    phrases: tuple[re.Pattern[str], ...]
    """What the utterance must look like. Deterministic, like Judgment: a spoken
    command is short and habitual, and a classifier that is right 90% of the time
    is the wrong tool for something that changes the world."""
    run: Runner
    describe: str
    """What the presence says when it asks. Spoken back to the person, so it is
    written as a question a person would answer, not as a command line."""
    reversible: bool = True
    """DECLARED, never derived. False means a spoken yes is required on the next
    turn before anything happens."""

    def matches(self, utterance: str) -> bool:
        return any(p.match(utterance.strip()) for p in self.phrases)


# What counts as consent. Deliberately narrow: "yeah", "go ahead", "do it".
# "Sure, but change X first" is NOT a yes — anything with a qualifier in it falls
# through to the fallback, which cancels. A confirmation prompt that accepts
# ambiguity has stopped being a confirmation.
_YES = re.compile(r"^\s*(yes|yeah|yep|yup|do it|go ahead|confirmed?|please do)\s*[.!]?\s*$", re.I)
_NO = re.compile(r"^\s*(no|nope|stop|cancel|never mind|nevermind|forget it)\s*[.!]?\s*$", re.I)


def is_yes(utterance: str) -> bool:
    return bool(_YES.match(utterance))


def is_no(utterance: str) -> bool:
    return bool(_NO.match(utterance))


@dataclass
class Registry:
    actions: list[Action] = field(default_factory=list)

    def add(self, action: Action) -> None:
        self.actions.append(action)

    def find(self, utterance: str) -> Action | None:
        for action in self.actions:
            if action.matches(utterance):
                return action
        return None


@dataclass
class Pending:
    """An irreversible action waiting for a spoken yes on the next turn."""

    action: Action
    asked_at: float


class Hands:
    """Match an utterance to an action, and hold consent between two turns.

    The pending action is dropped by ANY intervening utterance that is not a
    clear yes. It does not survive a topic change, a second question, or a
    reconnect — consent that persists is not consent, it is a standing
    authorisation nobody granted.
    """

    def __init__(self, registry: Registry, *, consent_window_s: float = 30.0) -> None:
        self._registry = registry
        self._window = consent_window_s
        self._pending: Pending | None = None

    @property
    def waiting(self) -> Action | None:
        return self._pending.action if self._pending else None

    def resolve(self, utterance: str, now: float) -> tuple[str, Action | None, str]:
        """Decide what this utterance means for Hands.

        Returns (outcome, action, spoken) where outcome is one of:

            "none"      not about Hands at all; carry on with the normal turn
            "confirm"   an irreversible action was asked for; `spoken` is the
                        question to ask, and nothing has happened yet
            "run"       go ahead — either a reversible action, or a yes to a
                        pending one
            "cancelled" a pending action was declined or abandoned
        """
        pending, self._pending = self._pending, None

        if pending is not None:
            if now - pending.asked_at > self._window:
                # Too long. Asking again is cheap; acting on a thirty-second-old
                # "yes" that was probably about something else is not.
                return "cancelled", pending.action, "That took a while, so I let it go."
            if is_yes(utterance):
                return "run", pending.action, ""
            if is_no(utterance):
                return "cancelled", pending.action, "Alright, leaving it."
            # Anything else — a new question, a qualifier, a half-answer — is not
            # a yes. Fall through and treat this utterance on its own merits,
            # having quietly dropped the pending action.
            log.info("consent for %s dropped: %r", pending.action.name, utterance[:60])

        action = self._registry.find(utterance)
        if action is None:
            return "none", None, ""
        if action.reversible:
            return "run", action, ""
        self._pending = Pending(action, now)
        return "confirm", action, f"{action.describe} Say yes and I'll do it."


async def shell(*argv: str, timeout: float = 30.0) -> str:
    """Run a command with NO shell. Helper for registered actions.

    argv, never a string. A voice-triggered action assembling a shell command out
    of transcribed speech is the shape of every command-injection bug there has
    ever been, and there is no argument for it here: the actions are fixed
    phrases, so their commands are fixed too.
    """
    proc = await asyncio.create_subprocess_exec(
        *argv,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        return f"{shlex.join(argv)} did not finish in {timeout:.0f} seconds."
    return out.decode("utf-8", "replace").strip() or "Done."


def default_registry() -> Registry:
    """The actions this lab ships with: read-only, and that is the point.

    NOTHING HERE CHANGES THE WORLD. Adding an action that does is a deliberate
    edit by someone who has decided it is worth a voice trigger and has set
    `reversible` honestly. The strategy leaves "which actions count as
    irreversible" open on purpose, and an empty-by-default registry is what
    keeps that decision from being made by omission.

    A worked example of the irreversible shape is in the module docstring of
    voice_gateway/hands/examples.py — deliberately not registered.
    """
    registry = Registry()
    registry.add(
        Action(
            name="uptime",
            phrases=(
                re.compile(r"^\s*(?:what'?s?\s+(?:the\s+)?|how long\s+)?uptime", re.I),
                re.compile(r"^\s*how long has (?:the box|ser5|it) been up", re.I),
            ),
            run=lambda: shell("uptime", "-p"),
            describe="Read the uptime?",
            reversible=True,
        )
    )
    return registry
