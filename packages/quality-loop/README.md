# quality-loop

Measured quality loops for the lab: spend test-time compute on mini's local
models, keep ACE playbooks, and record scores in SQLite. Pure-stdlib Python
(≥3.10) — no dependency chain to break on a freshly provisioned box.

CLI: **`qloop`** (compat alias `loopkit` for one release).

Deployed onto ser5 by the `agentlab` Ansible role
(`ser5/ansible/roles/agentlab`); runs anywhere that can reach
`http://mini:11434/v1` on the tailnet. See [`../../docs/ai-loops.md`](../../docs/ai-loops.md)
for architecture and the leverage map, and [`../../docs/operating-manual.md`](../../docs/operating-manual.md)
for the live model/tier policy.

## What it does

| Piece | Idea |
|-------|------|
| `loops.single` | One-shot baseline every strategy must beat |
| `loops.refine` | Generate → check alignment to the ask → revise only on a mismatch, until `answered` |
| `loops.best_of_n` | Sample N candidates at temperature, judge picks the winner |
| `gate` | Interactive quality gate (JSON + exit code); warm models only |
| `playbook.Playbook` | ACE-style evolving context: numbered tactic bullets, ADD/UPDATE/REMOVE deltas |
| `evals.run_suite` | JSONL task suites, scored, tracked in SQLite + JSONL traces |
| `star.bootstrap` | STaR-style: keep correct reasoning traces as SFT-ready JSONL |

### Lab leverage (how to use it)

| Surface | Use qloop for | Do **not** use for |
|---------|---------------|---------------------|
| **agentlab / offline** | eval matrix, bake-offs, `summary`, playbook reflect | — |
| **Hermes free-text** | `quality-gate.sh` / `qloop gate` on multi-constraint answers | Client-confidential over TG/Discord |
| **OpenCode** | Optional prose/extraction polish only | **Code diffs** (tests + reviewer gate those) |

Model aliases map to mini's resident pair and scheduled evictions (`qloop
models` to list):

| Alias | Model | Use |
|-------|-------|-----|
| `general` | `qwen3.6:35b-a3b-mtp-q4_K_M` | Default worker and judge; warm |
| `coder` | `qwen3-coder-next:latest` | Complex coding / deep reasoning; warm |
| `heavy` | `gpt-oss:120b` | Off-hours only; **evicts** warm model |
| `judge` | `nemotron-cascade-2:latest` | Independent BoN judge; **evicts** |
| `scout` | `nemotron-3-nano:4b` | Smoke only; **evicts** |

Interactive `qloop gate` allows **only** `general` and `coder`.

## Quickstart

```bash
qloop ask "question" --model scout                 # smoke test (evicts — careful)
qloop refine "hard task" --worker coder            # self-refinement loop
qloop bestof "hard task" -n 4                      # test-time compute scaling
qloop gate --task "hard free-text" --strategy refine --json
qloop eval suites/smoke.jsonl --strategy refine \
        --playbook pb.md --reflect                   # eval + evolving playbook
qloop star suites/smoke.jsonl --out sft.jsonl      # bootstrap training data
qloop stats                                        # compare runs
qloop stats --matrix                               # one row per suite x strategy x worker
qloop summary                                      # one-page markdown quality summary
```

Environment: `QUALITY_LOOP_BASE_URL` (default `http://mini:11434/v1`),
`QUALITY_LOOP_DATA` (default `~/.quality-loop`; `/data/agentlab` on ser5).
`LOOPKIT_*` env vars are accepted as a one-release fallback.

### Gate decision contract

`refine`'s critique step classifies the draft against the original ask —
`answered` / `partial` / `off_target` / `unsafe_or_invalid` — and only
iterates on a mismatch: `answered` stops immediately, `unsafe_or_invalid`
fails fast without a revise round, and persistent `off_target` fails rather
than keeping a wrong-question draft. A second, orthogonal `SCOPE:
in_scope|exceeded` check blocks delivery the same way `unsafe_or_invalid`
does whenever the draft acted on an unconfirmed assumption or added
something the ask never requested — even if it otherwise answered the ask.
FAIL results carry the judge's own explanation in `extra.critique`.

Stdout is one JSON object. Exit codes: `0` ACCEPT/KEEP_BASELINE, `2` SKIP,
`1` FAIL.

```bash
qloop gate --task "…" [--baseline "…"] --strategy refine --json
```

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
`make qloop-matrix` / `make qloop-summary` from the repo root — see
[`../../docs/ai-loops.md`](../../docs/ai-loops.md) for the bake-off ritual.

## The method that matters

Never trust a loop strategy without the baseline: run `--strategy single`
first, then the loop, on the same suite — `qloop stats` shows mean score
*and* token cost side by side. A loop that doesn't beat single on your suite
is burning watts.

## Development

```bash
uv venv .venv && uv pip install -p .venv/bin/python -e "packages/quality-loop[dev]"
.venv/bin/pytest packages/quality-loop/tests   # unit tests, no network needed
```
