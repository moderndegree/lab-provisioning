# loopkit

AI loop strategies for the lab: spend test-time compute on mini's local models
to buy frontier-shaped quality. Pure-stdlib Python (≥3.10) — no dependency
chain to break on a freshly provisioned box.

Deployed onto ser5 by the `agentlab` Ansible role
(`ser5/ansible/roles/agentlab`); runs anywhere that can reach
`http://mini:11434/v1` on the tailnet. See [`../../docs/ai-loops.md`](../../docs/ai-loops.md)
for the architecture and runbook, and [`../../docs/operating-manual.md`](../../docs/operating-manual.md)
for the live model/tier policy.

## What it does

| Piece | Idea |
|-------|------|
| `loops.single` | One-shot baseline every strategy must beat |
| `loops.refine` | Generate → critique → revise until the judge says ACCEPT |
| `loops.best_of_n` | Sample N candidates at temperature, judge picks the winner |
| `playbook.Playbook` | ACE-style evolving context: numbered tactic bullets, updated by a reflector model via incremental ADD/UPDATE/REMOVE deltas (no full rewrites → no context collapse) |
| `evals.run_suite` | JSONL task suites, scored (exact/contains/regex/numeric), tracked in SQLite + JSONL traces |
| `star.bootstrap` | STaR-style: keep only correct reasoning traces (rationalizing failures with an answer hint) as SFT-ready JSONL |

Model aliases map to mini's resident pair and scheduled evictions (`loopkit
models` to list):

| Alias | Model | Use |
|-------|-------|-----|
| `general` | `qwen3.6:35b-a3b-mtp-q4_K_M` | 70–80 t/s (measured); candidate generation, judging, reflection, general reasoning. Default worker and judge |
| `coder` | `qwen3-coder-next:latest` | ~35–50 t/s (est.); complex coding, deep reasoning, quality escalation |
| `heavy` | `gpt-oss:120b` | ~30 t/s (community-measured); off-hours best general reasoning. Evicts a warm model |
| `judge` | `nemotron-cascade-2:latest` | ~60–80 t/s (est.); math/algorithm escalation and independent best-of-N judge. Evicts a warm model |
| `scout` | `nemotron-3-nano:4b` | 150+ t/s; throwaway smoke tests only. Evicts a warm model |

Speed figures are estimates until the bake-off measures them, except where
marked measured.

## Quickstart

```bash
loopkit ask "question" --model scout                 # smoke test
loopkit refine "hard task" --worker coder            # self-refinement loop
loopkit bestof "hard task" -n 4                      # test-time compute scaling
loopkit bestof "hard task" -n 8 --judge judge        # independent judge, evicts warm model
loopkit eval suites/smoke.jsonl --strategy refine \
        --playbook pb.md --reflect                   # eval + evolving playbook
loopkit star suites/smoke.jsonl --out sft.jsonl      # bootstrap training data
loopkit stats                                        # compare runs
loopkit stats --matrix                               # one row per suite x strategy x worker
loopkit summary                                      # one-page markdown quality summary
```

Environment: `LOOPKIT_BASE_URL` (default `http://mini:11434/v1`),
`LOOPKIT_DATA` (default `~/.loopkit`; `/data/agentlab` on ser5).

## Phase 1 suites and match modes

`suites/extraction.jsonl`, `citation-qa.jsonl`, `structured-output.jsonl`, and
`coding.jsonl` are the Phase 1 SMB-shaped benchmark set (`smoke.jsonl` stays a
wiring check, not a benchmark). Beyond `exact`/`contains`/`regex`/`numeric`,
two match modes support them:

- `json_schema` — validates the answer's JSON against the task's `schema`
  field (minimal stdlib validator: type/required/properties/items/enum).
- `test` — executes the answer's fenced ```python``` code against the task's
  `tests` field (asserts) in a sandboxed subprocess with a timeout.

Run the full baseline matrix and generate the quality summary with
`make loopkit-matrix` / `make loopkit-summary` from the repo root — see
[`../../docs/ai-loops.md`](../../docs/ai-loops.md) for the bake-off ritual.

## The method that matters

Never trust a loop strategy without the baseline: run `--strategy single`
first, then the loop, on the same suite — `loopkit stats` shows mean score
*and* token cost side by side. A loop that doesn't beat single on your suite
is burning watts.

## Development

```bash
uv venv .venv && uv pip install -p .venv/bin/python -e "packages/loopkit[dev]"
.venv/bin/pytest packages/loopkit/tests   # unit tests, no network needed
```
