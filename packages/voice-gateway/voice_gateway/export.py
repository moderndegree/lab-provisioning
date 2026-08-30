"""Closed briefs, exported into the second brain as plain markdown.

docs/brain.md states the vault's rule: **files are sacred, indexes and apps are
disposable.** The Ledger is an index — queryable, prunable, an implementation
detail of how the presence works. A finished errand is not: it is a thing that
was wanted, what was done about it, and what came back. That belongs in
`/data/brain` where cortex MCP, the ai-workstation graph and Obsidian can all
read it, and where restic already keeps it.

WHAT IS AND IS NOT EXPORTED. Briefs, on close. **Not turns.** Conversation is
chatter — hundreds of rows a week, most of them "what's the GPU temperature" —
and pouring it into the vault would drown the notes that were written on purpose.
The rule of thumb: a row in `turns` is worth keeping for a day, a closed brief is
worth keeping for a year.

The note carries the errand's *provenance*, which is the part the Ledger alone
makes awkward to read back: who asked, on which device, what the brief said when
it was opened, every amendment it took mid-flight, and every query that left the
property on its behalf. That last section is the locality claim in a form a human
can audit without opening SQLite.

IDEMPOTENCE. The filename ends in the brief id, so re-exporting the same brief
overwrites its own note rather than creating a second one — which matters because
the title comes from the statement and the statement can be amended after the
first export. Any earlier note for the same id is renamed onto the new path.
"""

from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timezone
from pathlib import Path

from .ledger import EVENT_AMENDMENT, Brief, Ledger

log = logging.getLogger(__name__)

# Sits beside notes/postmortems and notes/decisions, which roles/brain already
# creates. Same shape, same reason: a kind of note that recurs gets a folder.
ERRANDS_SUBDIR = "notes/errands"

_SLUG_STRIP = re.compile(r"[^a-z0-9]+")


def _slug(text: str, *, words: int = 8) -> str:
    parts = _SLUG_STRIP.sub("-", text.lower()).strip("-").split("-")
    slug = "-".join(w for w in parts if w)[:80].strip("-")
    return slug or "errand"


class VaultExporter:
    def __init__(self, vault_dir: str, ledger: Ledger) -> None:
        self._root = Path(vault_dir) if vault_dir else None
        self._ledger = ledger

    @property
    def enabled(self) -> bool:
        return self._root is not None

    async def export(self, brief_id: str) -> Path | None:
        """Write (or rewrite) the note for one brief. Returns its path.

        Never raises. An export failure must not fail the errand it describes —
        the authoritative record is already in the Ledger, and this is a
        convenience for humans reading the vault.
        """
        if self._root is None:
            return None
        try:
            brief = await self._ledger.get_brief(brief_id)
            if brief is None:
                log.warning("export: no brief %s", brief_id)
                return None
            events = await self._ledger.events(brief_id)
            results = await self._ledger.results_for(brief_id)
            egress = await self._ledger.egress_for(brief_id)
            return self._write(brief, events, results, egress)
        except Exception:  # noqa: BLE001 - the Ledger is the record, not this
            log.exception("export of brief %s failed", brief_id)
            return None

    # ─── rendering ───────────────────────────────────────────────────────────
    def _write(self, brief: Brief, events, results, egress) -> Path:
        assert self._root is not None
        directory = self._root / ERRANDS_SUBDIR
        directory.mkdir(parents=True, exist_ok=True)

        day = datetime.fromtimestamp(brief.created_ts, timezone.utc).strftime("%Y-%m-%d")
        path = directory / f"{day}-{_slug(brief.statement)}-{brief.id[:8]}.md"

        # An amended brief gets a new title and therefore a new filename. Move
        # the old note rather than leaving two versions of one errand in the
        # vault, which is exactly the kind of duplicate a graph UI surfaces as
        # two unrelated notes.
        for stale in directory.glob(f"*-{brief.id[:8]}.md"):
            if stale != path:
                stale.rename(path)
                break

        path.write_text(self._render(brief, events, results, egress), encoding="utf-8")
        log.info("exported brief %s -> %s", brief.id, path)
        return path

    def _render(self, brief: Brief, events, results, egress) -> str:
        day = datetime.fromtimestamp(brief.created_ts, timezone.utc).strftime("%Y-%m-%d")
        amendments = [e for e in events if e["kind"] == EVENT_AMENDMENT]
        opened = amendments[0]["payload"]["was"] if amendments else brief.statement

        lines = [
            "---",
            "tags: [errand]",
            f"date: {day}",
            f"status: {brief.state}",
            f"brief: {brief.id}",
            f"speaker: {brief.speaker}",
            f"device: {brief.device}",
            "---",
            "",
            f"# Errand: {brief.statement}",
            "",
            "## Asked",
            "",
            f"{brief.speaker} on {brief.device}, {_stamp(brief.created_ts)}.",
            "",
            f"> {opened}",
            "",
        ]

        if amendments:
            lines += ["## Amended mid-flight", ""]
            for i, e in enumerate(amendments, 1):
                note = e["payload"].get("note") or ""
                lines.append(f"{i}. {_stamp(e['ts'])} — {e['payload']['now']}")
                if note:
                    lines.append(f"   ({note})")
            lines.append("")

        lines += ["## Result", ""]
        if results:
            for r in results:
                lines += [r["text"].strip(), ""]
        else:
            lines += [f"None — the brief ended `{brief.state}`.", ""]

        # The locality claim, auditable without opening SQLite. An empty section
        # is a positive statement, not a missing one: nothing left the property.
        lines += ["## What left the property", ""]
        if egress:
            lines.append("| when | endpoint | query |")
            lines.append("|---|---|---|")
            for e in egress:
                lines.append(f"| {_stamp(e['ts'])} | `{e['endpoint']}` | {e['query']} |")
        else:
            lines.append("Nothing. This errand was answered entirely on lab hardware.")
        lines += ["", f"*Exported from the voice ledger at {_stamp(time.time())}.*", ""]
        return "\n".join(lines)


def _stamp(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
