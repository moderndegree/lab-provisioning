---
name: opencode-lab
description: "How to hand coding work to THIS lab's OpenCode agent team on ser5 — task packaging, model routing, and reading results back. Use whenever delegating implementation, review, refactoring or audit work to OpenCode on this box. Complements the builtin `opencode` skill, which covers CLI mechanics only."
version: 1.0.0
author: agent
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [opencode, delegation, task-package, local-models, tier-l, ser5]
    related_skills: [opencode, grok, hermes-agent]
---

# OpenCode on ser5 — how to ask

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
tailnet: `quality/qwen3.6-35b-a3b-mtp` on `:8090` for everything, and
`deep/qwen3.8-27b` on `:8091` for the `architect` and `deep` agents. Overriding
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

Long runs are normal: a full chain on a small task is ~10 minutes, and real work
has taken 30+. Poll with `process(action="log")` rather than assuming a hang.

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
