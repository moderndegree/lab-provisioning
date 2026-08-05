# Agent chain — what is measured, and what only looked true

Method: `workstation/bin/agent-probe.sh` (run on ser5, where opencode lives)
drives `opencode run` headlessly and reads opencode's own sqlite db. The metric that matters is **task calls grouped by
assistant turn** — a four-way fan-out means four `task` calls sharing one
`message_id`, not four calls spread over four turns.

Success = orchestrator's own tools are only task/read/grep/glob/todowrite, the
dispatch tree contains all four critics, `qa` runs after them, the run exits 0,
and no tool is left `running`.

Those last two were added 2026-08-05 after a run scored PASS on all the dispatch
criteria while having been **killed by the stall watchdog** at 34 minutes with a
`tester` still holding a backgrounded server. A chain that delegates perfectly
and then hangs has passed nothing; that case now reports `VERDICT: STALLED`.

## What makes the critic fan-out fire

Measured 2026-08-05. Baseline never reached 4/4 critics in six runs; with the fix
in place **10 of 11 valid runs** did. The lever is not in the orchestrator prompt
at all:

- **`coder`'s `handoff` line must name the fan-out.** The orchestrator decides
  what to do next by reading the `@@RESULT` it just received. Rules sitting in
  its system prompt lose to the text in front of it. `coder.md` therefore
  requires every PASS to hand back "fan out the critics against these paths",
  and the batch now fires in ONE turn: `[4] reviewer, security-auditor, tester,
  doc-writer`.
- **Put the instruction at the decision point, not in the preamble.** This is the
  same lesson as `task-package.md` rule 5 ("a role named as a gate gets invoked;
  one mentioned as advice does not"), applied one level down.

## Role sequencing lives in the ASK, not in any prompt

Measured 2026-08-05, and it settles a question the dispatch counts had been
posing for a while. Across 65 `coder` dispatches lifetime, **`architect` and
`deep` had never run once**, and `planner` only 3 times. The orchestrator prompt
had asked for all of them the whole time — including a flat "your first `task`
call is `librarian` (RECALL)", which had produced 4 librarian runs, ever.

Adding four lines to the ask changed it on the first attempt:

```text
PROCESS
  - architect MUST design the module boundary and the error contract before any implementation starts.
  - planner MUST produce the implementation plan before coder is dispatched.
```

```
turn 1: [1] librarian     ← first RECALL of the series
turn 2: [1] architect     ← FIRST DISPATCH EVER
turn 3: [1] planner
turn 4: [1] coder
turn 5: [4] reviewer, security-auditor, tester, doc-writer
```

Reproduced on the next run. So `architect` and `deep` are not dead config — they
are unreachable from the orchestrator prompt and reachable from the user's
prompt. This is the same principle as the `coder` handoff, at the one point in a
run where no preceding agent exists: the last thing the orchestrator reads before
its first decision is the ask, so that is the only place a first-dispatch rule
can land. **Do not fix this by adding another orchestrator rule** — that has been
tried, in writing, and the counts above are the result.

## `qa` belongs on the quality endpoint

Measured 2026-08-05. On `throughput/gpt-oss-20b`, `qa` spent **11 of 17 tool
calls on `glob`/`read`** — auditing the codebase, which `qa.md` forbids by name —
and then **emitted no text output at all**. Its session contains the incoming
package and nothing else. The orchestrator saw an empty result, improvised an
extra `tester` dispatch, and that tester hung the run on `setsid ... python3
app.py` — the anti-pattern its own prompt warns about. Total cost: a 2041s run
killed by the watchdog.

Moved to `quality/qwen3.6-35b-a3b-mtp`. Same prompt, next run: `bash` 4, `read`
3, no globbing, six text parts, a real `@@RESULT` with per-criterion evidence,
and the run ended at `qa` with no improvised turn 12. 1078s, `VERDICT: PASS`.

The routing rule already said so — "critical-path work that runs ALONE → quality;
the split is by concurrency, not difficulty" — and `qa` was the one non-parallel
role sitting on the parallel endpoint. Worth noting the general shape: **two
agents ignored explicit prose in their own prompts, and both were on
gpt-oss-20b.** Prose constraints are worth less on the smaller model; put the
non-parallel work where the reasoning is.

Two things that looked like fixes and were not:

- **Cutting the INTAKE section** (167 → 142 lines) produced one 4/4 run that then
  failed to reproduce four times. Keep the cut — shorter is better here, and
  `0173f3f` claimed to have removed INTAKE but never did — but it is not the fix.
- **`temperature: 0.2` on `build` made it worse**, twice: the run collapsed to a
  single `coder` dispatch in ~100s. Same failure shape as `reasoningEffort:
  "none"`. **Anything that narrows the orchestrator's sampling budget kills
  delegation** — leave `temperature` unset.
- **Denying `read` to `build` made it worse**, 0/3, and one run dispatched
  nothing at all. The orchestrator needs to look at things to route them.

## Two hangs that eat whole runs (and look like skipped critics)

Both hit `tester`/`qa`, because both stand a service up to verify it:

- **A backgrounded server never lets the `bash` call return.** opencode waits on
  the process group; `nohup`, `setsid` and file redirection do not help. Start,
  probe and kill inside ONE `timeout`-wrapped command — see `tester.md`.
- **Reading a path outside the project dir hangs a subagent.** opencode raises
  `permission requested: external_directory`; a primary agent auto-rejects, a
  subagent waits forever. `coder`, `devops`, `tester` and `qa` therefore set
  `external_directory: allow`. Note bash can *write* `/tmp` with no prompt, so
  the asymmetry is easy to hit.

A stalled run looks exactly like a run that skipped its critics. `agent-probe.sh` has
a stall watchdog for this reason — if no new DB rows land for 6 minutes it kills
the run and names the tool left `running`.


## Run tally (2026-08-05)

| condition | runs | reached 4/4 |
|---|---|---|
| baseline (INTAKE present) | 6 | 0 |
| INTAKE cut | 5 | 2 |
| `temperature: 0.2` | 2 | 0 |
| `read` denied to build | 3 | 0 |
| **`coder` handoff names the fan-out** | **11** | **10** |

`qa` ran in 11/11, forbidden tool calls were 0 in 11/11, and no run hung once the
two hang fixes were in. The single miss (3/4) dropped `tester` only.

## Cortex: recall works when dispatched, capture had to move

`librarian` is the only agent with vault tools — the orchestrator is denied them,
because a prose "cortex pass" fired **zero times** across every run measured,
including after the tools were live and the step was marked mandatory.

- **RECALL is a `task` call now** and fires on roughly a third of runs. When it
  does, it is real: 8–11 `cortex_vault_*` calls feeding notes into the package.
  Naming it in "Before your FIRST dispatch" helped but did not make it reliable.
  Revisit once the vault has content worth recalling — with 5 scaffold files
  there is little for it to find, so the orchestrator skipping it is not
  obviously wrong.
- **CAPTURE moved onto `qa` itself.** Routing it through the orchestrator could
  never work: the run *ends* at qa's `@@RESULT`, so a capture the orchestrator is
  told to dispatch afterwards never happens. Zero captures reached the vault
  while it was somebody else's job. `qa` holds the cortex tools already and is
  the last thing to run, so it now writes the note before closing.

The general rule this produced: **an instruction only lands if the agent that
receives it still has a turn left in which to act.**

## Measurement traps that cost time here

- `pgrep -f 'opencode run'` matches its own command line; use `opencode ru[n]`.
  `pgrep` without `-f` matches only the process name and silently reports 0.
- "Newest session" is often a SUBAGENT, or a stray tool-check run. Filter on
  `parent_id is null` AND `time_created > <snapshot taken before the run>`.
- A stalled run and a run that skipped its critics look identical from outside.
  Check for tools left in `state.status = 'running'` before concluding anything.
- Killing a batch of probes kills the one that just started, too. Two runs were
  voided that way.
