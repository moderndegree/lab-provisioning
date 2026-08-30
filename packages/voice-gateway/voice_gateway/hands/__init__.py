"""Hands — acting on real systems, and the confirmation that gates it.

PERMISSION IS NOT THE HARD PART. An authorization system can tell you whether
this speaker is allowed to restart Open WebUI. What it cannot tell you is whether
they said it. Voice introduces a failure mode that permission models do not have:

    a request that was never made

The microphone heard "restart open web UI" because the transcription of "the rest
of the open web, you why" went that way, or because you were talking to someone
else in the room, or to the dog. No amount of "yes, brian is allowed to do that"
touches the question of whether brian asked.

So Hands owns *intent* and treats permission as somebody else's job. Every
irreversible action is spoken back and requires a spoken yes on the NEXT turn,
regardless of what any policy layer would have allowed. The confirmation is not
a courtesy or a UX nicety — it is the only defence against an utterance that did
not happen.

WHAT COUNTS AS IRREVERSIBLE IS A DECLARATION, NOT A DERIVATION. Nothing here
guesses. An action is registered with `reversible=False` by a person who decided
it, and the registry below is where that decision is written down. The strategy
lists this as one of the things still yours to decide, and this module refuses to
decide it for you: it ships with the read-only actions and NOTHING that changes
the world, so that turning any of this on is a deliberate act.
"""

from .registry import Action, Hands, Registry, default_registry

__all__ = ["Action", "Hands", "Registry", "default_registry"]
