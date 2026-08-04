#!/usr/bin/env python3
"""Simulate a multi-agent workflow session against mini's two llama-server instances.

Why this exists: every benchmark so far hit ONE model at a time. A real qloop-style
session runs an orchestrator (qwen, :8090) and a fan-out of workers (gpt-oss, :8091)
concurrently, so they contend for the same memory bandwidth. This measures that.

Shape of the simulated session (3 phases, as an orchestrator/worker loop runs):
  1. PLAN     orchestrator alone           -> task list
  2. FANOUT   N workers in parallel, WITH the orchestrator still working
              (this is the contention window that matters)
  3. JUDGE    orchestrator synthesises     -> verdict

Workers do a realistic 2-turn tool-calling exchange (call -> tool result -> answer),
because single-shot completions understate both prefill load and turn count.

Reports per-model throughput under contention against the isolated baselines, so
the interference cost is explicit rather than inferred.
"""
import argparse
import json
import statistics
import threading
import time
import urllib.request

QWEN = "http://127.0.0.1:8090/v1"
GPTOSS = "http://127.0.0.1:8091/v1"

# A system prompt with tool definitions, sized like a real agent harness (~1-2k
# tokens). Agent workloads are prefill-heavy in a way chat benchmarks are not.
SYSTEM = (
    "You are a component of an automated software-engineering loop operating on an "
    "infrastructure-as-code repository. You have access to tools. Follow the "
    "repository conventions exactly: idempotent Ansible tasks, fully-qualified "
    "module names, no hardcoded secrets, comment the non-obvious. "
) + ("Additional operating context and prior decisions are provided for grounding. " * 60)

TOOLS = [{
    "type": "function",
    "function": {
        "name": "read_file",
        "description": "Read a file from the repository",
        "parameters": {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "Repo-relative path"}},
            "required": ["path"],
        },
    },
}]

WORKER_TASKS = [
    "Inspect the llamacpp role and report whether the ExecStopPost reap pattern is correct.",
    "Check whether the ollama service lifecycle variables are wired into group_vars.",
    "Verify the toolboxes role documents why the vLLM flag profile sets PYTHONPATH.",
    "Determine whether the Open WebUI quadlet seeds both OpenAI endpoints.",
    "Report whether the KV ceiling warning in the llamacpp role is per-instance.",
    "Summarise how the GGUF is staged into the shared toolbox model directory.",
    "Explain why the tool-call parser for the vLLM path had to be qwen3_xml.",
    "Assess whether the MTP setting is documented as a per-workload trade.",
]

PLAN_PROMPT = (
    "Produce a numbered plan of 8 independent review tasks for auditing an "
    "Ansible role that manages inference servers. Be specific and concise."
)
JUDGE_PROMPT = (
    "Given eight independent review findings about an Ansible role, write a "
    "consolidated verdict: what is correct, what is risky, and what to change. "
    "Be decisive and concrete."
)


def chat(base, model, messages, max_tokens, tools=None):
    """One streaming chat completion. Returns (ttft, decode_s, ntok, text, tool_call)."""
    payload = {
        "model": model, "messages": messages, "max_tokens": max_tokens,
        "temperature": 0.0, "stream": True,
    }
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"
    req = urllib.request.Request(
        f"{base}/chat/completions", data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    t0 = time.perf_counter()
    ttft = None
    n = 0
    text = []
    saw_tool = False
    with urllib.request.urlopen(req, timeout=1800) as resp:
        for raw in resp:
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data: "):
                continue
            body = line[6:]
            if body == "[DONE]":
                break
            try:
                d = json.loads(body)
            except json.JSONDecodeError:
                continue
            ch = d.get("choices", [{}])[0]
            delta = ch.get("delta", {}) or {}
            if delta.get("tool_calls"):
                saw_tool = True
            piece = (delta.get("content") or delta.get("reasoning")
                     or delta.get("reasoning_content") or "")
            if not piece:
                continue
            if ttft is None:
                ttft = time.perf_counter() - t0
            n += 1
            text.append(piece)
    total = time.perf_counter() - t0
    if ttft is None:
        ttft, n = total, 0
    return ttft, max(total - ttft, 1e-9), n, "".join(text), saw_tool


class Rec:
    """Thread-safe collector of per-request samples, tagged by model."""
    def __init__(self):
        self.lock = threading.Lock()
        self.rows = []

    def add(self, model, phase, ttft, decode_s, ntok, tool):
        with self.lock:
            self.rows.append({
                "model": model, "phase": phase, "ttft": ttft,
                "decode_s": decode_s, "ntok": ntok, "tool": tool,
            })

    def by(self, **kw):
        return [r for r in self.rows
                if all(r[k] == v for k, v in kw.items())]


def worker(rec, idx, task, max_tokens):
    """Two-turn tool-calling exchange, as an agent worker actually behaves."""
    msgs = [{"role": "system", "content": SYSTEM},
            {"role": "user", "content": task}]
    ttft, dec, n, _, tool = chat(GPTOSS, "gpt-oss-20b", msgs, max_tokens, TOOLS)
    rec.add("gpt-oss", "fanout-turn1", ttft, dec, n, tool)
    # Turn 2: feed a tool result back and ask for the final answer.
    msgs.append({"role": "assistant", "content": "Calling read_file."})
    msgs.append({"role": "user",
                 "content": "Tool result: the file exists and contains the "
                            "expected block. Now give your final finding in "
                            "two sentences."})
    ttft, dec, n, _, tool = chat(GPTOSS, "gpt-oss-20b", msgs, max_tokens)
    rec.add("gpt-oss", "fanout-turn2", ttft, dec, n, tool)


def orchestrator(rec, phase, prompt, max_tokens, rounds=1):
    for _ in range(rounds):
        msgs = [{"role": "system", "content": SYSTEM},
                {"role": "user", "content": prompt}]
        ttft, dec, n, _, tool = chat(QWEN, "qwen3.6-35b-a3b-mtp", msgs, max_tokens)
        rec.add("qwen", phase, ttft, dec, n, tool)


def summarize(rec, model, phase=None):
    rows = rec.by(model=model) if phase is None else rec.by(model=model, phase=phase)
    rows = [r for r in rows if r["ntok"] > 0]
    if not rows:
        return None
    return {
        "requests": len(rows),
        "tokens": sum(r["ntok"] for r in rows),
        "per_stream_tok_s": statistics.median(r["ntok"] / r["decode_s"] for r in rows),
        "ttft_p50": statistics.median(r["ttft"] for r in rows),
        "ttft_p95": sorted(r["ttft"] for r in rows)[int(len(rows) * 0.95) - 1] if len(rows) > 1 else rows[0]["ttft"],
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--orch-rounds", type=int, default=3,
                    help="orchestrator requests issued DURING fan-out")
    ap.add_argument("--max-tokens", type=int, default=320)
    args = ap.parse_args()

    rec = Rec()
    print(f"=== multi-agent session: {args.workers} workers + orchestrator "
          f"({args.orch_rounds} concurrent rounds) ===", flush=True)

    # Phase 1 — orchestrator alone (no contention).
    t0 = time.perf_counter()
    orchestrator(rec, "plan", PLAN_PROMPT, args.max_tokens)
    t_plan = time.perf_counter() - t0
    print(f"[1] PLAN     {t_plan:6.1f}s  (orchestrator alone)", flush=True)

    # Phase 2 — the contention window: workers fan out WHILE the orchestrator
    # keeps working. This is the part no earlier benchmark covered.
    t0 = time.perf_counter()
    threads = [threading.Thread(target=worker,
                                args=(rec, i, WORKER_TASKS[i % len(WORKER_TASKS)],
                                      args.max_tokens))
               for i in range(args.workers)]
    threads.append(threading.Thread(
        target=orchestrator,
        args=(rec, "fanout-orch", JUDGE_PROMPT, args.max_tokens, args.orch_rounds)))
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    t_fan = time.perf_counter() - t0
    print(f"[2] FANOUT   {t_fan:6.1f}s  ({args.workers} workers x2 turns "
          f"+ {args.orch_rounds} orchestrator, CONCURRENT)", flush=True)

    # Phase 3 — orchestrator synthesis, alone again.
    t0 = time.perf_counter()
    orchestrator(rec, "judge", JUDGE_PROMPT, args.max_tokens)
    t_judge = time.perf_counter() - t0
    print(f"[3] JUDGE    {t_judge:6.1f}s  (orchestrator alone)", flush=True)

    wall = t_plan + t_fan + t_judge
    fan_rows = [r for r in rec.rows if r["phase"].startswith("fanout") and r["ntok"] > 0]
    fan_tokens = sum(r["ntok"] for r in fan_rows)

    print(f"\n=== session totals ===")
    print(f"  wall clock            {wall:6.1f}s")
    print(f"  requests              {len(rec.rows)}")
    print(f"  tokens (all phases)   {sum(r['ntok'] for r in rec.rows)}")
    print(f"  FANOUT aggregate      {fan_tokens / t_fan:6.1f} tok/s "
          f"(both models, {len(fan_rows)} requests)")

    print(f"\n=== per model ===")
    for m, iso in (("qwen", 85.9), ("gpt-oss", 79.6)):
        s = summarize(rec, m)
        if not s:
            continue
        delta = (s["per_stream_tok_s"] / iso - 1) * 100
        print(f"  {m:8} reqs={s['requests']:3}  tokens={s['tokens']:6}  "
              f"per-stream={s['per_stream_tok_s']:6.1f} tok/s "
              f"({delta:+.0f}% vs isolated {iso})  "
              f"ttft p50={s['ttft_p50']:.2f}s p95={s['ttft_p95']:.2f}s")

    print(f"\n=== contention check (orchestrator, same prompt) ===")
    for ph, label in (("plan", "alone      "), ("fanout-orch", "under load "),
                      ("judge", "alone again")):
        s = summarize(rec, "qwen", ph)
        if s:
            print(f"  {label}  per-stream={s['per_stream_tok_s']:6.1f} tok/s  "
                  f"ttft={s['ttft_p50']:.2f}s")

    tool_rows = rec.by(model="gpt-oss", phase="fanout-turn1")
    if tool_rows:
        hits = sum(1 for r in tool_rows if r["tool"])
        print(f"\n  tool-call rate (worker turn 1): {hits}/{len(tool_rows)}")


if __name__ == "__main__":
    main()
