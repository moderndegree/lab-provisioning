"""One-page "current quality" markdown summary, built from runs.db.

Renders the Phase 1 baseline matrix (suite x strategy x worker, latest run of
each combo) plus a per-suite best-vs-single delta, so a quality claim can
point at a generated artifact instead of vibes.
"""

from __future__ import annotations

import datetime

STRATEGIES = ("single", "refine", "best_of_n")
WORKERS = ("general", "coder")


def render_markdown_summary(rows: list[dict]) -> str:
    """rows: RunStore().matrix() output — one dict per (suite, strategy, worker)."""
    generated = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    by_key = {(r["suite"], r["strategy"], r["worker"]): r for r in rows}
    suites = sorted({r["suite"] for r in rows})

    lines = ["# Loopkit quality summary", "", f"Generated: {generated}", ""]

    if not rows:
        lines.append("No runs recorded yet — run `loopkit eval` against a suite first.")
        return "\n".join(lines) + "\n"

    total_expected = len(suites) * len(STRATEGIES) * len(WORKERS)
    lines.append(f"Recorded combos: {len(rows)} / {total_expected} expected (suite x strategy x worker).")
    lines.append("")

    for suite in suites:
        lines.append(f"## {suite}")
        lines.append("")
        lines.append("| Strategy | " + " | ".join(WORKERS) + " |")
        lines.append("|---" * (len(WORKERS) + 1) + "|")
        for strategy in STRATEGIES:
            cells = []
            for worker in WORKERS:
                row = by_key.get((suite, strategy, worker))
                cells.append(f"{row['mean_score']:.3f} ({row['tokens']}tok)" if row else "—")
            lines.append(f"| {strategy} | " + " | ".join(cells) + " |")
        lines.append("")

        for worker in WORKERS:
            baseline = by_key.get((suite, "single", worker))
            if not baseline:
                continue
            best_strategy, best_row = "single", baseline
            for strategy in ("refine", "best_of_n"):
                row = by_key.get((suite, strategy, worker))
                if row and row["mean_score"] > best_row["mean_score"]:
                    best_strategy, best_row = strategy, row
            delta = best_row["mean_score"] - baseline["mean_score"]
            verdict = "beats" if delta > 0 else ("ties" if delta == 0 else "underperforms")
            lines.append(
                f"- `{worker}`: best strategy is `{best_strategy}` "
                f"({best_row['mean_score']:.3f} vs single {baseline['mean_score']:.3f}, "
                f"{verdict} baseline by {delta:+.3f})."
            )
        lines.append("")

    return "\n".join(lines) + "\n"
