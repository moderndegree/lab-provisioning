"""Judgment — answer now, or dispatch an errand.

Decided in a fraction of the turn budget, which rules out asking a model. The
whole point of this module is that it costs nothing: a spoken turn has under
600 ms to its first audible word, and a classifier that spends 300 ms of it
deciding what kind of question it was has already lost whatever accuracy it
bought.

TWO THINGS IT WILL NOT DO, AND WHY

  It will not make a native tool call. OpenAI-style tool calling costs a full
  extra pass through the model — the model emits a call, we run it, we call the
  model AGAIN for prose — before the first audible word. That doubles exactly
  the number this design exists to minimise. `config.native_tools` is the seam if
  a voicebench number ever justifies revisiting it; it is off until one does.

  It will not be clever. Predictability is a feature, not a consolation. Spoken
  commands are short and habitual: a user learns "search for X" in one turn and
  it works every time, which is worth more than a classifier that is right 90% of
  the time and unpredictable about which 10% it misses.

BIASED TOWARD DISPATCHING. When the two readings are close, this returns an
errand. The failure modes are not symmetric — a wrongly dispatched errand costs
a few seconds of bench time nobody is waiting on, while a wrongly answered one
stalls the conversation on work the presence should never have attempted. The
presence is allowed to be shallow. It is not allowed to make you wait.

THE PRESENCE'S OWN REFUSAL IS ALSO A DISPATCH SIGNAL. `is_refusal` reads the
answer that was just spoken; "I don't know" and "that needs current information"
are the model telling us, for free and after the fact, that this turn was an
errand. No classifier, no second pass, and it catches everything the prefixes
below never will. It is consumed where dispatch happens, not here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

Kind = Literal["answer", "errand"]
# Which staff entrance the errand needs. The bench owns all of them; the
# presence owns none, and must not learn how to reach any of them.
Capability = Literal["", "retrieval", "delegation"]

# Order matters: delegation is checked first, so "have hermes search for X"
# delegates rather than searching.
_DELEGATE = [
    re.compile(r"^\s*(?:have|ask|tell|get)\s+hermes\s+(?:to\s+)?(.+)$", re.I),
    re.compile(r"^\s*hermes[,:]?\s+(?:please\s+)?(.+)$", re.I),
    re.compile(r"^\s*delegate\s+(?:this\s+)?(?:to\s+hermes[,:]?\s*)?(.+)$", re.I),
]

_RETRIEVAL = [
    re.compile(r"^\s*(?:search|look)\s+(?:the\s+web\s+)?(?:up\s+|for\s+)?(.+)$", re.I),
    re.compile(r"^\s*(?:google|web\s*search)\s+(?:for\s+)?(.+)$", re.I),
    # "what is the latest ..." as well as "what's/whats". The contracted form was
    # the only one this matched until now, so the full spoken form fell through to
    # a plain answer — the exact case that most needs the world.
    re.compile(r"^\s*what(?:'?s|\s+is)?\s+the\s+latest\s+(?:on\s+|about\s+)?(.+)$", re.I),
]

# Phrases the system prompt asks for by name — "If you do not know, say so in one
# sentence rather than guessing. If a question needs current information and no
# web results were provided, say that." These match that instruction's output,
# which is why they are stable enough to route on: they are not guesses about how
# a model phrases uncertainty, they are the phrasing we asked for.
_REFUSAL = re.compile(
    r"\b("
    r"i (?:do not|don't) (?:know|have)"
    r"|i'?m not sure"
    r"|needs? (?:current|up[- ]to[- ]date|recent) information"
    r"|(?:i )?(?:can'?t|cannot) (?:reach|access|check)"
    r"|no web results"
    r"|as of my"
    r")\b",
    re.I,
)


@dataclass(frozen=True)
class Verdict:
    kind: Kind
    text: str
    """For `answer`, the utterance. For `errand`, the brief's opening statement."""
    capability: Capability = ""


def judge(utterance: str) -> Verdict:
    text = utterance.strip()
    if not text:
        return Verdict("answer", "")

    for pattern in _DELEGATE:
        m = pattern.match(text)
        if m:
            return Verdict("errand", _clean(m.group(1)), "delegation")

    for pattern in _RETRIEVAL:
        m = pattern.match(text)
        if m:
            return Verdict("errand", _clean(m.group(1)), "retrieval")

    return Verdict("answer", text)


# An amendment is a correction aimed at work already in flight, not a new
# request. Two shapes, and they are treated differently:
#
#   discourse openers   "actually", "wait", "scratch that", "and also" — pure
#                       filler. Stripped; what follows is the correction.
#   instruction openers "make it ...", "change that to ..." — the verb IS the
#                       correction and must survive. "make it shorter" reduced to
#                       "shorter" is not an instruction, it is a fragment.
#
# Both are only consulted when a brief is ALREADY running. "Actually, what time
# is it" is an ordinary question when nothing is in flight, and reading it as an
# amendment would silently rewrite an errand the user never mentioned.
_AMEND_OPENER = re.compile(
    r"^\s*(?:"
    r"actually"
    r"|no,?\s+wait"
    r"|wait"
    r"|instead"
    r"|scratch that"
    r"|forget that"
    r"|and also"
    r"|also"
    r")[,]?\s+(.+)$",
    re.I,
)
_AMEND_WHOLE = re.compile(
    r"^\s*(?:make (?:it|that)|change that to|add|drop|skip|focus on)\s+.+$", re.I
)


def amendment(utterance: str) -> str | None:
    """The correction, if this utterance is steering something already in flight.

    Returns text meant to be APPENDED to the brief's current statement, not to
    replace it. Rewriting "compare the backends" into "vulkan only" would throw
    away the half of the request that did not change; the bench gets
    "compare the backends. Vulkan only." and reads it the way a person would.

    The caller must only ask when a brief is actually running — this function has
    no way to know, and answering for an idle conversation would turn every
    "actually, never mind" into a silent rewrite of nothing.
    """
    text = utterance.strip()
    if not text:
        return None
    m = _AMEND_OPENER.match(text)
    if m:
        rest = _clean(m.group(1))
        return rest or None
    if _AMEND_WHOLE.match(text):
        return _clean(text) or None
    return None


def is_refusal(answer: str) -> bool:
    """Whether the presence just admitted it could not answer.

    Read AFTER the answer is spoken, so it costs nothing on the fast path and
    catches the whole long tail the prefixes above cannot: every question that
    needed the world and did not announce itself with "search for".
    """
    return bool(_REFUSAL.search(answer))


def _clean(s: str) -> str:
    # Transcripts arrive with terminal punctuation that reads oddly when the
    # fragment is re-used as a search query or a brief's statement.
    return s.strip().rstrip(".?!,").strip()
