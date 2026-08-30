"""Delegation to the Hermes agent.

Hermes is reached through its CLI, not its HTTP proxy, and that is a deliberate
choice rather than a convenience. `hermes proxy` on :8645 is a NOUS PORTAL proxy
— it forwards to a Nous subscription, ignores the configured
`model.provider`/`base_url`, and is currently down anyway ("Not logged into Nous
Portal", stopped by hand 2026-08-14 to end a restart loop). The CLI is the path
that actually works today and the one that honours Hermes's own routing to mini.

Delegation is FIRE AND FORGET from the turn's point of view. `hermes chat` takes
as long as the work takes; blocking a voice turn on it would mean minutes of
silence. Instead the session acknowledges immediately, this runs in the
background, and the result arrives later as an out-of-band `notice`.

THE PROVIDER IS PINNED ON EVERY INVOCATION, and that is the whole reason this
class is allowed to exist inside the bench at all. `docs/todo.md` records four
live hosted credentials on this box — openrouter, opencode-zen, copilot and
xai-oauth — each reachable on a fallback, an explicit `--provider`, or a `-m`
naming a hosted model. Hermes also cannot be version-pinned: the NousResearch
installer always fetches latest, so every converge is an unreviewed upgrade that
could change its default routing without anything in this repo noticing.

So the bench does not trust Hermes' configured default. It names the provider and
the base URL on the command line every time, and the bench — not Hermes — is the
policy point. If a future Hermes stops honouring these flags, the honest answer
is to stop invoking it, not to widen the door.

A delegation is no longer bound to the session that asked for it. It has a brief,
the brief has a result, and the result waits in the Ledger for a seam. The old
log-and-drop was correct when there was nowhere to put an answer; with a Ledger,
"twenty minutes later if need be" is what was actually wanted.
"""

from __future__ import annotations

import asyncio
import logging
import shutil

log = logging.getLogger(__name__)


class HermesDelegate:
    def __init__(
        self,
        binary: str,
        *,
        timeout: int = 900,
        provider: str = "",
        base_url: str = "",
        model: str = "",
    ) -> None:
        self._binary = binary
        self._timeout = timeout
        self._provider = provider
        self._base_url = base_url
        self._model = model

    def available(self) -> bool:
        """Whether the binary resolves on THIS process's PATH.

        The unit bakes a PATH that includes ~/.local/bin (where the NousResearch
        installer puts hermes) and ~/.npm-global/bin. Getting that PATH wrong is
        a real, already-made mistake in this lab: Hermes could not see `opencode`
        until 2026-08-15 for exactly this reason, and reported it as "not
        installed" rather than as a path problem.
        """
        return shutil.which(self._binary) is not None

    def _argv(self, prompt: str) -> list[str]:
        argv = [self._binary, "chat", "-q", prompt, "-Q"]
        # Named every time. An unpinned invocation inherits whatever the last
        # unreviewed upgrade left in config.yaml, which for a while on this box
        # was provider `xai-oauth` against api.x.ai — recorded in docs/todo.md
        # as egress to a third party nobody had chosen.
        if self._provider:
            argv += ["--provider", self._provider]
        if self._model:
            argv += ["-m", self._model]
        return argv

    async def run(self, prompt: str) -> str:
        """Run one delegation to completion and return what Hermes said."""
        proc = await asyncio.create_subprocess_exec(
            *self._argv(prompt),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=self._timeout
            )
        except TimeoutError:
            proc.kill()
            await proc.wait()
            return f"Hermes did not finish within {self._timeout} seconds."

        out = stdout.decode("utf-8", "replace").strip()
        if proc.returncode != 0:
            err = stderr.decode("utf-8", "replace").strip()[:300]
            log.warning("hermes exited %s: %s", proc.returncode, err)
            return f"Hermes failed: {err or 'no output'}"
        return out or "Hermes finished but said nothing."
