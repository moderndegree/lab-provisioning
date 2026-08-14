#!/usr/bin/env python3
"""Benchmark a small-fan-out session: N concurrent workers, then one judge pass.

The shape: N candidates generated CONCURRENTLY on one model, then ONE judge call
on the same model, repeated for R sequential rounds. Defaults (n=3, rounds=2) come
from the quality-loop package that used to drive this box — it was removed 2026-08,
but the SHAPE remains the realistic one for agent work: peak concurrency in the
low single digits, not the 16-32 a synthetic fan-out benchmark invites. Keep using
it to size `parallel`, and change -n to whatever your orchestrator actually
dispatches.

Reports per-round wall time and end-to-end session time, which is what a human
waiting on the loop actually experiences.
"""
import argparse
import json
import statistics
import threading
import time
import urllib.request

# Overridable so the same shape can be run against either endpoint — which is the
# only way to A/B a model swap. Defaults stay on quality/:8090 for continuity with
# the numbers already recorded in mini/AGENTS.md.
BASE = "http://127.0.0.1:8090/v1"
MODEL = "qwen3.6-35b-a3b-mtp"

TASK = (
    "Write an idempotent Ansible task block that installs a systemd user unit "
    "for a llama.cpp server, ensuring it is enabled and started, following "
    "repository conventions: fully-qualified module names, a name on every task, "
    "and no unguarded shell commands."
)
JUDGE = (
    "You are judging candidate solutions to an Ansible authoring task. Score each "
    "on correctness, idempotency, and convention adherence. Return a verdict "
    "naming the winner and the single most important flaw in each."
)


def chat(prompt, max_tokens, sink, base=None, model=None):
    base = base or BASE
    body = json.dumps({
        "model": model or MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens, "temperature": 0.0, "stream": True,
    }).encode()
    req = urllib.request.Request(f"{base}/chat/completions", data=body,
                                 headers={"Content-Type": "application/json"})
    t0 = time.perf_counter()
    ttft = None
    n = 0
    with urllib.request.urlopen(req, timeout=1800) as resp:
        for raw in resp:
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data: "):
                continue
            payload = line[6:]
            if payload == "[DONE]":
                break
            try:
                d = json.loads(payload)
            except json.JSONDecodeError:
                continue
            delta = d.get("choices", [{}])[0].get("delta", {}) or {}
            piece = (delta.get("content") or delta.get("reasoning")
                     or delta.get("reasoning_content") or "")
            if not piece:
                continue
            if ttft is None:
                ttft = time.perf_counter() - t0
            n += 1
    wall = time.perf_counter() - t0
    sink.append({"ttft": ttft or wall, "wall": wall, "ntok": n,
                 "tok_s": n / max(wall - (ttft or 0), 1e-9)})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-n", type=int, default=3, help="candidates per round (qloop default 3)")
    ap.add_argument("--rounds", type=int, default=2, help="qloop default 2")
    ap.add_argument("--max-tokens", type=int, default=1024,
                    help="qloop ModelSpec.output is 8192; 1024 keeps the run bounded")
    ap.add_argument("--base", default=BASE, help="OpenAI-compatible base URL")
    ap.add_argument("--model", default=MODEL, help="model id as the server reports it")
    args = ap.parse_args()

    print(f"=== qloop-shaped session: best_of_n n={args.n}, rounds={args.rounds}, "
          f"model={args.model} @ {args.base} ===", flush=True)
    t_session = time.perf_counter()
    round_times = []

    for r in range(1, args.rounds + 1):
        t_r = time.perf_counter()

        cands = []
        threads = [threading.Thread(target=chat,
                                    args=(f"[candidate {i}] {TASK}", args.max_tokens, cands,
                                          args.base, args.model))
                   for i in range(args.n)]
        t0 = time.perf_counter()
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        t_gen = time.perf_counter() - t0

        judged = []
        t0 = time.perf_counter()
        chat(JUDGE, args.max_tokens, judged, args.base, args.model)
        t_judge = time.perf_counter() - t0

        rt = time.perf_counter() - t_r
        round_times.append(rt)
        gen_tok = sum(c["ntok"] for c in cands)
        print(f"  round {r}: {rt:5.1f}s   generate {t_gen:5.1f}s "
              f"({args.n} concurrent, {gen_tok} tok, "
              f"{gen_tok / t_gen:5.1f} tok/s agg, "
              f"per-stream {statistics.median(c['tok_s'] for c in cands):5.1f})"
              f"   judge {t_judge:5.1f}s ({judged[0]['ntok']} tok, "
              f"{judged[0]['tok_s']:5.1f} tok/s)", flush=True)

    total = time.perf_counter() - t_session
    print(f"\n  SESSION WALL: {total:.1f}s  "
          f"(median round {statistics.median(round_times):.1f}s)")


if __name__ == "__main__":
    main()
