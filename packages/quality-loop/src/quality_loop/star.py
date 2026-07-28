"""STaR-style bootstrapping: harvest correct reasoning traces as training data.

Run tasks through a strategy, keep only the traces that score correct, and
emit them as chat-format JSONL (system/user/assistant) ready for SFT/LoRA
fine-tuning later. Optionally rationalize failures: re-ask with the answer
given as a hint, and keep the trace only if the model then reproduces it —
the STaR trick that recovers training signal from tasks the model can't yet
solve unaided.

Fine-tuning itself is out of scope here (gfx1151 training support is still
experimental); this module produces the dataset so that work can start the
day the training stack is trusted.
"""

from __future__ import annotations

import json
from pathlib import Path

from quality_loop.client import ChatClient
from quality_loop.evals import ANSWER_SUFFIX, Task, load_suite, score
from quality_loop.loops import STRATEGIES

RATIONALIZE_SYSTEM = """Solve the task. A trusted hint gives the correct final answer —
work out a genuine step-by-step justification that reaches it. Do not mention the hint."""


def _example(task: Task, answer: str, reasoning: str) -> dict:
    """One SFT example in chat format. Reasoning is folded into the reply."""
    reasoning = reasoning.strip()
    if "ANSWER:" in reasoning:
        content = reasoning  # trace already carries its own ANSWER line
    elif reasoning:
        content = f"{reasoning}\n\nANSWER: {answer}"
    else:
        content = f"ANSWER: {answer}"
    return {
        "messages": [
            {"role": "user", "content": task.prompt},
            {"role": "assistant", "content": content},
        ],
        "meta": {"task_id": task.id},
    }


def bootstrap(
    client: ChatClient,
    suite_path: str | Path,
    out_path: str | Path,
    strategy: str = "single",
    worker: str = "general",
    rationalize: bool = True,
    limit: int | None = None,
    **strategy_kwargs,
) -> dict:
    """Generate traces, filter by correctness, write chat JSONL."""
    tasks = load_suite(suite_path)
    if limit:
        tasks = tasks[:limit]
    loop_fn = STRATEGIES[strategy]
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    kept = failed = rationalized = 0
    with open(out, "w", encoding="utf-8") as fh:
        for task in tasks:
            prompt = f"{task.prompt}\n\n{ANSWER_SUFFIX}"
            trace = loop_fn(client, prompt, worker=worker, **strategy_kwargs)
            if score(task, trace.answer) >= 1.0:
                fh.write(json.dumps(_example(task, task.expected, trace.answer)) + "\n")
                kept += 1
                continue
            if not rationalize:
                failed += 1
                continue
            # Rationalization pass: hint the answer, keep only if reproduced.
            hinted = client.ask(
                f"{task.prompt}\n\nHint — the correct final answer is: {task.expected}\n\n{ANSWER_SUFFIX}",
                model=worker,
                system=RATIONALIZE_SYSTEM,
            )
            if score(task, hinted.content) >= 1.0:
                fh.write(json.dumps(_example(task, task.expected, hinted.content)) + "\n")
                kept += 1
                rationalized += 1
            else:
                failed += 1

    return {
        "tasks": len(tasks),
        "kept": kept,
        "rationalized": rationalized,
        "failed": failed,
        "out": str(out),
    }
