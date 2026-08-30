"""Judgment and consent — the two places a wrong reading has real consequences."""

from __future__ import annotations

import re
import time

import pytest

from voice_gateway.hands import Action, Hands, default_registry
from voice_gateway.judgment import amendment, is_refusal, judge


@pytest.mark.parametrize("utterance,kind,capability", [
    ("what is the kv ceiling on mini", "answer", ""),
    ("search for strix halo benchmarks", "errand", "retrieval"),
    ("google the rocm release notes", "errand", "retrieval"),
    ("what's the latest on rocm", "errand", "retrieval"),
    # The contracted form was the only one that matched until 2026-08-30; the
    # full spoken form fell through to a plain answer, which is the exact case
    # that most needs the world.
    ("what is the latest on rocm 7.15", "errand", "retrieval"),
    ("have hermes wire up the searxng verify", "errand", "delegation"),
    ("hermes, restart open webui", "errand", "delegation"),
])
def test_routing(utterance, kind, capability):
    verdict = judge(utterance)
    assert (verdict.kind, verdict.capability) == (kind, capability)


def test_delegation_wins_over_retrieval():
    assert judge("have hermes search for x").capability == "delegation"


@pytest.mark.parametrize("answer,refused", [
    ("262144 per slot.", False),
    ("I don't know that one.", True),
    ("That needs current information and I have no web results.", True),
    ("I can't reach the internet from here.", True),
])
def test_the_presence_refusal_is_a_dispatch_signal(answer, refused):
    assert is_refusal(answer) is refused


@pytest.mark.parametrize("utterance,expected", [
    ("actually, make it vulkan only", "make it vulkan only"),
    ("no, wait, use the 27b instead", "use the 27b instead"),
    ("scratch that, compare all three", "compare all three"),
    # The verb IS the correction here and must survive: "shorter" alone is a
    # fragment, not an instruction.
    ("make it shorter", "make it shorter"),
    ("what is the kv ceiling on mini", None),
    ("tell me a joke", None),
])
def test_amendments(utterance, expected):
    assert amendment(utterance) == expected


def _hands(ran=None, **kwargs):
    """A registry with one irreversible action in it, and Hands around it."""

    async def run():
        (ran if ran is not None else []).append("ran")
        return "done"

    registry = default_registry()
    registry.add(
        Action(
            name="restart-inference",
            phrases=(re.compile(r"^\s*restart (?:the )?inference\b", re.I),),
            run=run,
            describe="Restart inference on mini?",
            reversible=False,
        )
    )
    return Hands(registry, **kwargs)


def test_irreversible_actions_need_a_spoken_yes():
    ran: list[str] = []
    hands = _hands(ran)
    now = time.monotonic()

    outcome, action, said = hands.resolve("restart the inference", now)
    assert outcome == "confirm" and "Say yes" in said
    assert action is not None and action.name == "restart-inference"
    assert ran == [], "nothing happens before consent"
    assert hands.resolve("yes", now + 1)[0] == "run"
    assert ran == [], "resolve decides; it never executes"


@pytest.mark.parametrize("reply,outcome", [
    ("no", "cancelled"),
    ("never mind", "cancelled"),
    # Not a yes. A confirmation prompt that accepts ambiguity has stopped being
    # a confirmation.
    ("sure, but check the temperature first", "none"),
    ("what is the kv ceiling on mini", "none"),
])
def test_anything_short_of_a_clean_yes_drops_the_action(reply, outcome):
    hands = _hands()
    now = time.monotonic()
    hands.resolve("restart the inference", now)
    assert hands.resolve(reply, now + 1)[0] == outcome
    assert hands.waiting is None


def test_consent_expires():
    hands = _hands(consent_window_s=30)
    now = time.monotonic()
    hands.resolve("restart the inference", now)
    outcome, _, said = hands.resolve("yes", now + 31)
    assert outcome == "cancelled" and "let it go" in said


def test_a_reversible_action_needs_no_confirmation():
    hands = Hands(default_registry())
    assert hands.resolve("uptime", time.monotonic())[0] == "run"
