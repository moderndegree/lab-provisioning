"""Deterministic intent routing.

Voice does not get to pay for a tool-call round trip. Native OpenAI tool calling
costs a full extra pass through the model — the model emits a call, we run it,
we call the model AGAIN to get prose — before the first audible word. That
doubles exactly the number this design exists to minimise.

So the two tools the lab actually has are routed here, by prefix, in about zero
milliseconds, and the tool result is folded into the SAME model call that
answers. `config.native_tools` is the seam if that ever needs revisiting, and it
is off until a voicebench number says otherwise.

Predictability is a feature, not a consolation. Spoken commands are short and
habitual; a user learns "search for X" in one turn and it works every time,
which is worth more than a classifier that is right 90% of the time and
unpredictable about which 10% it misses.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

Kind = Literal["chat", "search", "delegate"]

# Order matters: delegation is checked first, so "have hermes search for X"
# delegates rather than searching.
_DELEGATE = [
    re.compile(r"^\s*(?:have|ask|tell|get)\s+hermes\s+(?:to\s+)?(.+)$", re.I),
    re.compile(r"^\s*hermes[,:]?\s+(?:please\s+)?(.+)$", re.I),
    re.compile(r"^\s*delegate\s+(?:this\s+)?(?:to\s+hermes[,:]?\s*)?(.+)$", re.I),
]

_SEARCH = [
    re.compile(r"^\s*(?:search|look)\s+(?:the\s+web\s+)?(?:up\s+|for\s+)?(.+)$", re.I),
    re.compile(r"^\s*(?:google|web\s*search)\s+(?:for\s+)?(.+)$", re.I),
    re.compile(r"^\s*what'?s?\s+the\s+latest\s+(?:on\s+|about\s+)?(.+)$", re.I),
]


@dataclass(frozen=True)
class Route:
    kind: Kind
    text: str
    """For chat, the utterance. For search, the query. For delegate, the task."""


def route(utterance: str) -> Route:
    text = utterance.strip()
    if not text:
        return Route("chat", "")

    for pattern in _DELEGATE:
        m = pattern.match(text)
        if m:
            return Route("delegate", _clean(m.group(1)))

    for pattern in _SEARCH:
        m = pattern.match(text)
        if m:
            return Route("search", _clean(m.group(1)))

    return Route("chat", text)


def _clean(s: str) -> str:
    # Transcripts arrive with terminal punctuation that reads oddly when the
    # fragment is re-used as a search query or a task description.
    return s.strip().rstrip(".?!,").strip()
