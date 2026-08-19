---
name: opencode-lab
description: "Delegate coding to OpenCode CLI — the REQUIRED procedure on this machine. Use for every OpenCode delegation: features, refactoring, PR review, audits, one-shot runs. OVERRIDES the generic `opencode` skill, whose model and auth guidance is wrong here: never pass --model or run `opencode auth login`, and every ask must be a TASK PACKAGE with executable done-when checks."
version: 1.1.0
author: agent
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [opencode, delegation, coding-agent, task-package, local-models, tier-l, ser5]
    related_skills: [opencode, grok, hermes-agent]
---

# OpenCode on ser5 — how to ask

> **Precedence.** If this skill and the generic `opencode` skill disagree, THIS
> ONE WINS. That skill is upstream boilerplate written for a stock install; the
> two places it is actively wrong here are called out below. Use it only for
> mechanics it does not contradict (`process` polling, exiting with Ctrl+C).

The builtin `opencode` skill tells you how to *drive the CLI*. It does not know
this lab, and following it unmodified produces two concrete failures:

- it documents `--agent build|plan` as OpenCode's stock pair. Here, `build` is an
  **orchestrator that dispatches 13 specialised agents**. Asking it for "a fix"
  gets you one; asking it properly gets you a plan, an implementation, four
  parallel critics and an acceptance gate.
- it suggests `opencode auth login` and `--model openrouter/...`. Do not. Models
  here come from mini over the tailnet. See "Never override the model".

Use the builtin skill for mechanics (`run` vs background TUI, `-f`, `process`
polling, exiting with Ctrl+C not `/exit`). Use this one for the ask itself.

## Read this first — the spec is a file, not this skill

The orchestrator's own contract for what a good ask contains lives at:

    ~/.config/opencode/.moderndegree/skills/task-package.md

**Read it before writing a non-trivial ask.** It is deliberately not copied here:
it is loaded into every OpenCode session by `opencode.json`, it changes as the
lab learns, and a second copy would be the next thing to go stale. (A local
`quality-gate` skill was retired from this box for exactly that — it pointed at a
binary that no longer existed.)

The two things from it that decide whether your ask works:

**Everything that matters goes in `Done-when`.** A requirement in prose but
absent from the acceptance list gets dropped — not maliciously, just
deprioritised against what is explicitly graded.

**Every criterion must be able to FAIL.** Before writing one, ask what a
completely broken implementation would print, and whether your criterion rejects
it. "has unit tests" is decoration; "tests cover <named cases>, paste the summary
line" is a criterion. Existence-shaped items ("X exists") are satisfied by
creating a file. Execution-shaped items ("X was run, here is the output") are not.

Measured here 2026-08-05: a correct, specific security requirement placed in
`PROCESS` rather than `Done-when` produced an agent that thought about the risk
and shipped an unauthenticated server action anyway. **`PROCESS` schedules a
role; only `DONE WHEN` proves anything.** If a role's contribution is necessary,
name it as a gate ("`architect` MUST produce the interface before code is
written"), because a role mentioned as advice does not get invoked.

## Never override the model

    # WRONG — routes this lab's coding work to a third party
    opencode run '...' --model openrouter/anthropic/claude-sonnet-4

    # RIGHT — let opencode.json route it
    opencode run '...'

`~/.config/opencode/opencode.json` points at mini's llama-server over the
tailnet: `quality/qwen3.6-35b-a3b-mtp` on `:8090` for general work, and
`deep/qwen3.8-27b` on `:8091` for `architect`, `coder`, and `deep`. Overriding
`--model` both leaves controlled hardware and sends the work to a model the
prompts were not written for.

`:8090` has **four slots**, which is why the critic fan-out is four wide. `:8091`
has **one** and is ~4x slower — never fan out against it.

## Dispatching

    terminal(command="opencode run '<TASK PACKAGE>'", workdir="<repo>")

Defaults to the `build` orchestrator, which is what you want. Do not pass
`--agent` unless you deliberately want a single specialist and no fan-out.

Scope every session to one repo. **Never point it at a working checkout you care
about** — `coder` has edit rights. Clone to a throwaway directory first.

## Long runs: dispatch detached, then check back

A full chain is **10–20 minutes**, and real work has taken 30+. Your terminal
tool cannot supervise that: `terminal.timeout` and `TERMINAL_TIMEOUT` cap a
single call at seconds, and the persistent shell is reaped at
`lifetime_seconds`. Measured 2026-08-15: a foreground attempt gave up at ~421s
and told the user it would "wait for the completion notification" — the chain
was fine and finished normally, but the result was never collected.

Use the terminal tool's OWN backgrounding. Do not reach for `setsid`, `nohup` or
a trailing `&`: `approvals.mode` is `manual` with a 60s timeout, so a
shell-backgrounding command sits waiting for a human, is auto-DENIED when none
answers, and the run never starts (measured 2026-08-15 — the denial then got
reported to the user as "OpenCode is running in the background", which it was
not).

1. **Launch in the background, redirecting into a log file in the work dir:**

       terminal(command="opencode run '<TASK PACKAGE>' > opencode.log 2>&1",
                workdir="<dir>", background=true)

   The redirect is load-bearing. `process(action="log", …)` handles are scoped to
   ONE chat session: ask about a handle in a later turn and you get "no process
   with that ID exists", even though the run finished perfectly (measured
   2026-08-15). A file in the work directory survives; a handle does not.

2. **Tell the user the work directory** and that a full chain takes 10–20
   minutes. The directory is the durable reference, not the handle.
3. **End your turn.** Do not poll in a loop; you will exhaust your turn budget
   long before the chain finishes.
4. **Read the result back** when you next act or are asked:

       tail -80 <dir>/opencode.log

   Find the `@@RESULT` block and report its `status` and `evidence` verbatim.

## Never conclude "no result" from a glance

When the same run above was checked back, the report was "there are no other
files beyond those two" and "status: unknown/no result available". Both were
false: `app.py` and `test_app.py` were already on disk, and the chain had run a
complete two-round critic cycle. A complete success was reported as a failure.

So before saying a run produced nothing:

- **Run `ls -la <dir>` and paste the real output.** Not your recollection of it.
- **Check whether it is still running** — `pgrep -f "opencode ru[n]"`. Still
  running is not failure.
- **Treat the deliverable as the evidence.** The `@@RESULT` block is
  self-reported by an agent; the files, and the tests actually passing, are not.
  If the package had a done-when list, run those checks yourself and paste the
  output. That is the difference between reporting a result and repeating a
  claim.

**Never report a delegation as failed because your terminal call timed out.**
A timed-out call says nothing about the run. Check `pgrep` first: still running
means still working.

## Read the result back

Every agent closes with one block. Do not summarise past it — read it:

    @@RESULT
    status: PASS | FAIL | BLOCKED
    summary: <one line>
    evidence: <what was OBSERVED — command + actual output, path:line, or a test
               summary. "looks correct" is not evidence.>
    handoff: <what should happen next>
    @@END

`status: PASS` with an `evidence` line that names no command and no output is a
claim, not a result — say so when you report back rather than passing it on.

On `BLOCKED`, the `handoff` names the exact gap. Enrich the package and
re-dispatch; do not retry the same thin prompt.

## Checking the chain still delegates

If results look like the orchestrator did everything itself, measure it — do not
guess from the transcript, which shows what it *said*:

    agent-probe.sh "<prompt>" 2400 <label>

Reads OpenCode's sqlite db and scores the real dispatch tree. `PASS` needs four
distinct critics, `max_task_batch=4` (the fan-out landing in ONE assistant turn,
not spread over four), `qa` having run, and zero `bash`/`edit`/`write` calls by
the orchestrator. Results append to `~/agentprobe/results.tsv`; prior findings
are in the repo at `workstation/docs/agent-chain-findings.md`.

## Pitfalls

- **A thin ask is about missing intent, not missing volume.** Pointing at the
  right file is a complete handoff; pasting a file into a package that has no
  `Done-when` fixes nothing.
- **Anything crossing a boundary needs one live check** in `Done-when` — network,
  another process, a service. Offline tests pass happily while the wiring is
  wrong.
- **Do not ask OpenCode questions you can answer.** It has the repo; you have the
  user. Ask the user only for blocking unknowns (intent, priority, risk,
  consent), and label everything else as an assumption in the package.
- **Backgrounded servers hang the run.** If a package asks for a service to be
  started, require it be started with a timeout and stopped in the same step.
