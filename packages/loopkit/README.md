# loopkit

AI loop strategies for the lab: spend test-time compute on mini's local models
to buy frontier-shaped quality. Pure-stdlib Python (≥3.10) — no dependency
chain to break on a freshly provisioned box.

Deployed onto ser5 by the `agentlab` Ansible role
(`ser5/ansible/roles/agentlab`); runs anywhere that can reach
`http://mini:11434/v1` on the tailnet. See [`../../docs/ai-loops.md`](../../docs/ai-loops.md)
for the architecture and runbook.

## What it does

| Piece | Idea |
|-------|------|
| `loops.single` | One-shot baseline every strategy must beat |
| `loops.refine` | Generate → critique → revise until the judge says ACCEPT |
| `loops.best_of_n` | Sample N candidates at temperature, judge picks the winner |
| `playbook.Playbook` | ACE-style evolving context: numbered tactic bullets, updated by a reflector model via incremental ADD/UPDATE/REMOVE deltas (no full rewrites → no context collapse) |
| `evals.run_suite` | JSONL task suites, scored (exact/contains/regex/numeric), tracked in SQLite + JSONL traces |
| `star.bootstrap` | STaR-style: keep only correct reasoning traces (rationalizing failures with an answer hint) as SFT-ready JSONL |

Model aliases map to the two warm base models on mini (`loopkit models` to
list): `general` (qwen3.6 35B-A3B MoE — fast, judging/general reasoning) and
`coder` (qwen3.6 27B dense — complex coding, deep reasoning). `scout` (nano 4B)
exists for throwaway smoke tests only — loading it evicts one of the warm pair.

## Quickstart

```bash
loopkit ask "question" --model scout                 # smoke test
loopkit refine "hard task" --worker coder            # self-refinement loop
loopkit bestof "hard task" -n 4                      # test-time compute scaling
loopkit eval suites/smoke.jsonl --strategy refine \
        --playbook pb.md --reflect                   # eval + evolving playbook
loopkit star suites/smoke.jsonl --out sft.jsonl      # bootstrap training data
loopkit stats                                        # compare runs
```

Environment: `LOOPKIT_BASE_URL` (default `http://mini:11434/v1`),
`LOOPKIT_DATA` (default `~/.loopkit`; `/data/agentlab` on ser5).

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
