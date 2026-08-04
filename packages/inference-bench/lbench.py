#!/usr/bin/env python3
"""Decode-throughput benchmark against Lemonade's OpenAI-compatible endpoint.

Uses /v1/chat/completions with streaming so time-to-first-token (prefill) is
excluded from the decode rate, matching how vbench.py measures the toolbox
server. Reasoning tokens count as output here -- they are real decoded tokens.
"""
import argparse
import json
import statistics
import threading
import time
import urllib.request

PROMPT = (
    "Write a thorough technical explanation of how memory bandwidth, cache "
    "hierarchy, and arithmetic intensity interact to determine the performance "
    "of large language model inference on unified-memory APU architectures."
)


def stream(base, model, prompt, max_tokens):
    body = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": 0.0,
        "stream": True,
    }).encode()
    req = urllib.request.Request(
        f"{base}/chat/completions", data=body,
        headers={"Content-Type": "application/json"},
    )
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
            # count reasoning tokens too -- they are decoded output
            piece = (delta.get("content") or delta.get("reasoning")
                     or delta.get("reasoning_content") or "")
            if not piece:
                continue
            if ttft is None:
                ttft = time.perf_counter() - t0
            n += 1
    total = time.perf_counter() - t0
    if ttft is None:
        return None
    return ttft, max(total - ttft, 1e-9), n


def run(base, model, level, max_tokens):
    out = [None] * level
    def w(i):
        out[i] = stream(base, model, f"[{i}] " + PROMPT, max_tokens)
    ts = [threading.Thread(target=w, args=(i,)) for i in range(level)]
    t0 = time.perf_counter()
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    wall = time.perf_counter() - t0
    ok = [r for r in out if r]
    if not ok:
        return None
    return {
        "concurrency": level,
        "aggregate_tok_s": sum(r[2] for r in ok) / wall,
        "per_stream_decode_tok_s": statistics.median(r[2] / r[1] for r in ok),
        "median_ttft_s": statistics.median(r[0] for r in ok),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:13305/api/v1")
    ap.add_argument("--model", required=True)
    ap.add_argument("--label", default="lemonade")
    ap.add_argument("--concurrency", default="1,8")
    ap.add_argument("--max-tokens", type=int, default=192)
    args = ap.parse_args()

    stream(args.base, args.model, "warmup", 8)
    for level in [int(x) for x in args.concurrency.split(",")]:
        r = run(args.base, args.model, level, args.max_tokens)
        if r:
            print(
                f"[{args.label}] c={r['concurrency']:>3}  "
                f"agg={r['aggregate_tok_s']:7.1f} tok/s  "
                f"per-stream={r['per_stream_decode_tok_s']:6.1f} tok/s  "
                f"ttft={r['median_ttft_s']:6.2f}s",
                flush=True,
            )


if __name__ == "__main__":
    main()
