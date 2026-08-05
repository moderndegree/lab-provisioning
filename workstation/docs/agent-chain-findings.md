# Agent chain — what is measured, and what only looked true

Method: `workstation/bin/agent-probe.sh` (run on ser5, where opencode lives)
drives `opencode run` headlessly and reads opencode's own sqlite db. The metric that matters is **task calls grouped by
assistant turn** — a four-way fan-out means four `task` calls sharing one
`message_id`, not four calls spread over four turns.

Success = orchestrator's own tools are only task/read/grep/glob/todowrite, the
dispatch tree contains all four critics, and `qa` runs after them.

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
