# inference-bench

> **See also: `llama-benchy`** (`mini/ansible/roles/llama_benchy`, run on mini as
> `llama-benchy-suite <suite>`). The two measure different things and you want both.
> These scripts measure a workload we invented — agent fan-out, tool calls,
> multi-turn — and answer "how does my workload feel". llama-benchy measures the
> SERVER along axes these scripts guess at: the prompt-processing vs
> token-generation split, throughput at context DEPTH, and prefix-cache reuse.
> Depth is the one that matters most for agents and that nothing here covered —
> an agent's context grows every turn, and decode does not stay flat as it does.

Benchmarks for mini's llama.cpp serving path. These exist because **every tuning
decision in `mini/ansible/roles/llamacpp` rests on numbers produced here** — if a
model, quant, or llama.cpp build changes, re-run them rather than trusting the
figures baked into that role's comments.

Run them **on mini** (they hit `127.0.0.1`; the servers bind `0.0.0.0` but
benchmarking across the tailnet measures the wifi, not the GPU).

## The scripts

| script | measures | use when |
|---|---|---|
| `lbench.py` | raw decode throughput at a given concurrency | comparing runtimes/quants |
| `agentsim.py` | multi-agent session: workers + orchestrator, tool calls, multi-turn | checking cross-model contention |
| `fanoutsim.py` | small fan-out: N concurrent workers then a judge pass | sizing `parallel` for real agent work |
| `benchy_report.py` | summarises llama-benchy JSON into comparison tables | after `llama-benchy-suite` runs on mini |

```sh
python3 lbench.py   --base http://127.0.0.1:8090/v1 --model qwen3.6-35b-a3b-mtp \
                    --concurrency 1,8 --max-tokens 192
python3 agentsim.py --workers 8 --orch-rounds 3 --max-tokens 320
python3 fanoutsim.py -n 3 --rounds 2 --max-tokens 1024
```

## Read this before trusting a number

**Workload shape changes throughput by ~2x.** `lbench.py` uses long sustained
generations and reported 190.7 tok/s aggregate for gpt-oss at 8-way. The same
server under agent-shaped traffic (short structured answers, multi-turn) does
**85-100 tok/s**. Plan capacity against the latter.

**Slot-count scaling reverses between the two.** With long generations, more
slots is monotonically better (190 -> 400 tok/s from 8 -> 32 slots). With
agent-shaped traffic at 32 concurrent clients it inverts:

| slots | agent-shaped aggregate | per-stream |
|-------|------------------------|------------|
| 8     | 98.5 tok/s             | 13.0       |
| 32    | 83.4 tok/s             |  3.1       |

Short answers mean per-request overhead dominates and simultaneous prefills
compete without reaching the batching efficiency long generations get. A sweep
run with the wrong workload shape will point you the wrong way.

**Running both models at once is no longer symmetric — re-measured 2026-08-03
after the ROCm 7.14 / quadlet rebuild, and the earlier finding no longer holds.**

The old numbers here (qwen -54%, gpt-oss -56%, fan-out wall 14.5s -> 30.8s)
described a roughly even split of the damage. It is now lopsided: the
orchestrator runs essentially free during fan-out, and the workers absorb the
whole cost.

| | orchestrator (qwen, :8090) | worker fan-out wall (gpt-oss, :8091) |
|---|---|---|
| workers alone (`--orch-rounds 0`) | — | 13.2-15.5s |
| orchestrator working during fan-out | +3 to +4% vs isolated | 20.8-24.1s |

Median session (8 workers x 2 turns + 3 orchestrator rounds): **~31s wall,
~100 tok/s aggregate**, tool-call rate 8/8. So supervising a fan-out from :8090
is close to free now; budget the contention against the workers instead.

**`per-stream` in `agentsim.py` conflates queueing with throughput loss — do not
read it as a contention cost.** It divides tokens by each request's total
elapsed time, including time queued. 8 workers x 2 turns = 16 requests through
8 slots is two waves, so gpt-oss shows -79% to -84% per-stream *with no
orchestrator running at all*. Only the -89% vs -84% gap is contention. Compare
fan-out wall time or aggregate throughput; ignore per-stream across configs
with different request counts.

**Discard the first run after a server restart.** A cold prompt cache cost 26%:
run 1 gave 39.1s wall / 77.8 tok/s aggregate and showed the orchestrator at
-42%, while runs 2-4 gave 28.7/31.8/31.2s and +3 to +4%. Warm up, then measure.

**Size `parallel` to the real fan-out, which is small.** The loop that used to
drive this box dispatched 3-4 candidates per round with rounds run sequentially —
peak concurrency of 4, not the 16-32 a synthetic benchmark invites. Matching the
server to that measured 18% faster end-to-end (71.1s -> 58.6s per n=3 rounds=2
session, generate phase 30.6s -> 24.5s).

That loop was removed in 2026-08, so **the `parallel: 4` on the qwen instance is
now sized to a shape nothing currently dispatches.** It is a reasonable default
for interactive use and small fan-outs, but re-measure with `fanoutsim.py -n <your
real fan-out>` before treating it as tuned.

**Run-to-run variance is high.** The same 8-worker configuration returned 63.0
and 99.9 tok/s on separate runs, and that gap is unexplained. Treat differences
under ~10% as noise; re-run before acting on one.

`agentsim.py` prints a TTFT comparison for the orchestrator that is not
trustworthy — prompt-cache reuse across the repeated system prompt makes the
"under load" figure lower than "alone". Ignore that row.
