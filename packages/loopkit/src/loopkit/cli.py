"""loopkit CLI.

  loopkit ask "prompt" [--model general]
  loopkit refine "task" [--worker general --judge general --rounds 3]
  loopkit bestof "task" [-n 4]
  loopkit eval suite.jsonl --strategy refine [--playbook pb.md --reflect --limit 5]
  loopkit star suite.jsonl --out data.jsonl [--no-rationalize]
  loopkit playbook show|reflect pb.md ...
  loopkit stats [--run-id ID] [--matrix]
  loopkit summary [--out FILE]
  loopkit models

Environment:
  LOOPKIT_BASE_URL  inference endpoint (default http://mini:11434/v1)
  LOOPKIT_DATA      run-tracking dir   (default ~/.loopkit)
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from loopkit.client import DEFAULT_BASE_URL, ChatClient
from loopkit.models import MODELS


def _client(args) -> ChatClient:
    return ChatClient(base_url=args.base_url, timeout=args.timeout)


def _print_trace_answer(trace) -> None:
    steps = ", ".join(f"{s.kind}" for s in trace.steps)
    print(
        f"[{trace.strategy}] rounds={trace.rounds} accepted={trace.accepted} "
        f"tokens={trace.total_completion_tokens} steps: {steps}",
        file=sys.stderr,
    )
    print(trace.answer)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="loopkit", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base-url", default=os.environ.get("LOOPKIT_BASE_URL", DEFAULT_BASE_URL))
    parser.add_argument("--timeout", type=float, default=600.0)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("ask", help="one-shot question")
    p.add_argument("prompt")
    p.add_argument("--model", default="general")
    p.add_argument("--system")

    p = sub.add_parser("refine", help="generate → critique → revise loop")
    p.add_argument("task")
    p.add_argument("--worker", default="general")
    p.add_argument("--judge", default="general")
    p.add_argument("--rounds", type=int, default=3)

    p = sub.add_parser("bestof", help="best-of-N sampling with a judge")
    p.add_argument("task")
    p.add_argument("-n", type=int, default=4)
    p.add_argument("--worker", default="general")
    p.add_argument("--judge", default="general")
    p.add_argument("--temperature", type=float, default=0.9)

    p = sub.add_parser("eval", help="run a task suite through a strategy")
    p.add_argument("suite")
    p.add_argument("--strategy", default="single", choices=["single", "refine", "best_of_n"])
    p.add_argument("--worker", default="general")
    p.add_argument("--playbook", help="playbook file to inject (and evolve with --reflect)")
    p.add_argument("--reflect", action="store_true", help="update the playbook after each task")
    p.add_argument("--limit", type=int)

    p = sub.add_parser("star", help="bootstrap correct traces into SFT JSONL")
    p.add_argument("suite")
    p.add_argument("--out", required=True)
    p.add_argument("--strategy", default="single", choices=["single", "refine", "best_of_n"])
    p.add_argument("--worker", default="general")
    p.add_argument("--no-rationalize", action="store_true")
    p.add_argument("--limit", type=int)

    p = sub.add_parser("playbook", help="show or evolve a playbook")
    p.add_argument("action", choices=["show", "reflect"])
    p.add_argument("path")
    p.add_argument("--task", help="reflect: the task attempted")
    p.add_argument("--trace", help="reflect: what happened")
    p.add_argument("--outcome", help="reflect: PASS/FAIL + detail")

    p = sub.add_parser("stats", help="summarize recorded runs")
    p.add_argument("--run-id")
    p.add_argument("--matrix", action="store_true",
                   help="one row per suite x strategy x worker (latest run of each)")

    p = sub.add_parser("summary", help="one-page markdown quality summary from runs.db")
    p.add_argument("--out", help="write to a file instead of stdout")

    sub.add_parser("models", help="list model aliases and their lab variants")

    args = parser.parse_args(argv)

    if args.command == "models":
        for alias, spec in MODELS.items():
            mode = "reasoning" if spec.reasoning else "direct"
            print(f"{alias:<11} → {spec.name:<20} ctx={spec.context:<7} {mode}")
        return 0

    if args.command == "stats":
        from loopkit.storage import RunStore
        store = RunStore()
        rows = store.matrix() if args.matrix else store.summary(args.run_id)
        if not rows:
            print("no recorded runs", file=sys.stderr)
            return 0
        for row in rows:
            print(json.dumps(row))
        return 0

    if args.command == "summary":
        from loopkit.report import render_markdown_summary
        from loopkit.storage import RunStore
        text = render_markdown_summary(RunStore().matrix())
        if args.out:
            from pathlib import Path
            Path(args.out).write_text(text, encoding="utf-8")
        else:
            print(text, end="")
        return 0

    if args.command == "playbook":
        from loopkit.playbook import Playbook
        pb = Playbook(args.path)
        if args.action == "show":
            print(pb.as_context() or "(empty playbook)")
            return 0
        if not (args.task and args.trace and args.outcome):
            parser.error("playbook reflect requires --task, --trace, and --outcome")
        applied = pb.reflect(_client(args), args.task, args.trace, args.outcome)
        print("\n".join(applied) if applied else "NO_CHANGES")
        return 0

    client = _client(args)

    if args.command == "ask":
        result = client.ask(args.prompt, model=args.model, system=args.system)
        print(result.content)
        return 0

    if args.command == "refine":
        from loopkit.loops import refine
        trace = refine(client, args.task, worker=args.worker, judge=args.judge, max_rounds=args.rounds)
        _print_trace_answer(trace)
        return 0

    if args.command == "bestof":
        from loopkit.loops import best_of_n
        trace = best_of_n(client, args.task, n=args.n, worker=args.worker,
                          judge=args.judge, temperature=args.temperature)
        _print_trace_answer(trace)
        return 0

    if args.command == "eval":
        from loopkit.evals import run_suite
        from loopkit.playbook import Playbook
        playbook = Playbook(args.playbook) if args.playbook else None
        summary = run_suite(
            client, args.suite, strategy=args.strategy, worker=args.worker,
            playbook=playbook, reflect=args.reflect, limit=args.limit,
            on_result=lambda t, tr, s: print(f"  {t.id}: {'PASS' if s >= 1 else 'FAIL'}", file=sys.stderr),
        )
        print(json.dumps(summary))
        return 0

    if args.command == "star":
        from loopkit.star import bootstrap
        summary = bootstrap(
            client, args.suite, args.out, strategy=args.strategy, worker=args.worker,
            rationalize=not args.no_rationalize, limit=args.limit,
        )
        print(json.dumps(summary))
        return 0

    parser.error(f"unhandled command {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
