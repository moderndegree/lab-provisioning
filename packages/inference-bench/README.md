# inference-bench

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

**Running both models at once is not free parallelism.** Measured with the
orchestrator working during worker fan-out: qwen -54%, gpt-oss -56%, fan-out wall
time 14.5s -> 30.8s. The two `llama-server` processes each schedule as if they own
the GPU. On a bandwidth-bound box, serialising the phases finished the same
session ~20% faster than overlapping them.

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
