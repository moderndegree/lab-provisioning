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

A delegation is bound to the session that asked for it. If the client has
disconnected by the time the work finishes, the result is logged and dropped —
there is no store-and-forward, because a spoken answer to a question asked an
hour ago is worse than nothing.
"""

from __future__ import annotations

import asyncio
import logging
import shutil

log = logging.getLogger(__name__)


class HermesDelegate:
    def __init__(self, binary: str, *, timeout: int = 900) -> None:
        self._binary = binary
        self._timeout = timeout

    def available(self) -> bool:
        """Whether the binary resolves on THIS process's PATH.

        The unit bakes a PATH that includes ~/.local/bin (where the NousResearch
        installer puts hermes) and ~/.npm-global/bin. Getting that PATH wrong is
        a real, already-made mistake in this lab: Hermes could not see `opencode`
        until 2026-08-15 for exactly this reason, and reported it as "not
        installed" rather than as a path problem.
        """
        return shutil.which(self._binary) is not None

    async def run(self, prompt: str) -> str:
        """Run one delegation to completion and return what Hermes said."""
        proc = await asyncio.create_subprocess_exec(
            self._binary,
            "chat",
            "-q",
            prompt,
            "-Q",
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
