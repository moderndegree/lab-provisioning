"""ACE-style evolving playbook: a context that learns from experience.

A playbook is a markdown file of numbered strategy bullets, injected into the
system prompt of future runs. After each run, a reflector model proposes
delta operations (ADD / UPDATE / REMOVE) based on what worked or failed —
incremental deltas rather than full rewrites avoid the context-collapse
failure mode where a rewriting model erases hard-won details.

The file lives in a git-friendly location; version it with the repo that owns
it (on ser5 the agentlab role puts playbooks under /data/agentlab/playbooks).
"""

from __future__ import annotations

import datetime
import re
from dataclasses import dataclass
from pathlib import Path

from quality_loop.client import ChatClient

REFLECT_SYSTEM = """You maintain a playbook of numbered strategy bullets that guide future
attempts at similar tasks. Given a task, an attempt trace, and its outcome, propose
delta operations — the fewest edits that capture the lesson. Formats (one per line):

ADD: <new bullet — a concrete, reusable tactic or pitfall, one sentence>
UPDATE <n>: <replacement text for bullet n>
REMOVE <n>

Rules: never restate existing bullets as ADDs; prefer UPDATE over ADD when a bullet
already covers the topic; propose at most 3 operations; if there is no durable
lesson, reply exactly NO_CHANGES."""


@dataclass
class Bullet:
    number: int
    text: str
    helpful: int = 0
    harmful: int = 0


class Playbook:
    """Numbered markdown bullets with ADD/UPDATE/REMOVE delta semantics."""

    def __init__(self, path: str | Path, max_bullets: int = 60):
        self.path = Path(path)
        self.max_bullets = max_bullets
        self.bullets: list[Bullet] = []
        if self.path.exists():
            self._load()

    def _load(self) -> None:
        self.bullets = []
        pattern = re.compile(r"^(\d+)\.\s+(.*?)(?:\s+<!--\s*\+(\d+)/-(\d+)\s*-->)?$")
        for line in self.path.read_text(encoding="utf-8").splitlines():
            m = pattern.match(line.strip())
            if m:
                self.bullets.append(
                    Bullet(
                        number=int(m.group(1)),
                        text=m.group(2).strip(),
                        helpful=int(m.group(3) or 0),
                        harmful=int(m.group(4) or 0),
                    )
                )
        self._renumber()

    def save(self) -> None:
        stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        lines = [f"# Playbook — {self.path.stem}", f"<!-- updated {stamp} -->", ""]
        for b in self.bullets:
            lines.append(f"{b.number}. {b.text} <!-- +{b.helpful}/-{b.harmful} -->")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def _renumber(self) -> None:
        for i, b in enumerate(self.bullets, start=1):
            b.number = i

    def as_context(self) -> str:
        """Render for injection into a system prompt. Empty string when new."""
        if not self.bullets:
            return ""
        body = "\n".join(f"{b.number}. {b.text}" for b in self.bullets)
        return f"Playbook of tactics learned from previous attempts:\n{body}"

    # ─── delta operations ───────────────────────────────────────────────

    def apply_ops(self, ops_text: str) -> list[str]:
        """Parse and apply reflector output; returns human-readable log lines."""
        applied: list[str] = []
        if "NO_CHANGES" in ops_text:
            return applied
        existing = {self._norm(b.text) for b in self.bullets}
        for line in ops_text.splitlines():
            line = line.strip().lstrip("-* ")
            if m := re.match(r"ADD:\s*(.+)", line):
                text = m.group(1).strip()
                if self._norm(text) in existing or len(self.bullets) >= self.max_bullets:
                    continue
                self.bullets.append(Bullet(number=len(self.bullets) + 1, text=text))
                existing.add(self._norm(text))
                applied.append(f"ADD: {text}")
            elif m := re.match(r"UPDATE\s+(\d+):\s*(.+)", line):
                idx = int(m.group(1)) - 1
                if 0 <= idx < len(self.bullets):
                    self.bullets[idx].text = m.group(2).strip()
                    applied.append(f"UPDATE {idx + 1}")
            elif m := re.match(r"REMOVE\s+(\d+)", line):
                idx = int(m.group(1)) - 1
                if 0 <= idx < len(self.bullets):
                    removed = self.bullets.pop(idx)
                    applied.append(f"REMOVE {idx + 1}: {removed.text[:60]}")
        self._renumber()
        return applied

    @staticmethod
    def _norm(text: str) -> str:
        return re.sub(r"\W+", " ", text.lower()).strip()

    def reflect(
        self,
        client: ChatClient,
        task: str,
        trace_summary: str,
        outcome: str,
        reflector: str = "general",
    ) -> list[str]:
        """Ask the reflector for delta ops based on a run, then apply + save."""
        current = self.as_context() or "(playbook is empty)"
        prompt = (
            f"CURRENT PLAYBOOK:\n{current}\n\n"
            f"TASK:\n{task}\n\n"
            f"ATTEMPT TRACE (summary):\n{trace_summary}\n\n"
            f"OUTCOME: {outcome}\n\n"
            "Propose delta operations."
        )
        result = client.ask(prompt, model=reflector, system=REFLECT_SYSTEM)
        applied = self.apply_ops(result.content)
        if applied:
            self.save()
        return applied
