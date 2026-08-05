# bin — measuring whether the agent chain still delegates

These three scripts read **opencode's own sqlite db**
(`~/.local/share/opencode/opencode.db`) rather than its console output. That is
the whole point: the console shows what the orchestrator *said*, and the failure
mode being watched for is an orchestrator that narrates delegation while doing
the work itself. The db records which sessions actually spawned.

They run **on ser5**, where opencode lives. Nothing copies them there yet — scp
them over, or run them through ssh. Paths (`$HOME/.npm-global/bin`, the db
location) assume that host.

| Script | Use it for |
|---|---|
| `agent-probe.sh "<prompt>" [timeout] [label]` | A controlled run against a fixed prompt. Emits `VERDICT: PASS\|FAIL`. Run after any change to `opencode.json` or `.moderndegree/prompts/*`. |
| `agent-history.sh` | The last 15 real orchestrator sessions from the past 24h — tools and children per session. No prompt needed. |
| `agent-session.sh <session_id>` | One session in detail, scored on the same criteria. Ids come from `agent-history.sh`. |

## What PASS means

```
=== SCORE  distinct_critics=4/4  max_task_batch=4  qa=1  forbidden_tool_calls=0 ...
=== VERDICT: PASS
```

- **`distinct_critics=4/4`** — `reviewer`, `security-auditor`, `tester` and
  `doc-writer` all ran.
- **`max_task_batch`** — the largest number of `task` calls sharing one
  `message_id`. This is the metric that matters and the one console output cannot
  give you: four dispatches in one assistant turn run *in parallel*, four spread
  over four turns run serially and waste the throughput endpoint entirely. Same
  critic count, completely different machine behaviour.
- **`qa=1`** — the acceptance gate ran after the critics.
- **`forbidden_tool_calls=0`** — the orchestrator called no `bash`/`edit`/`write`/
  `patch`. Non-zero means it did the work itself.

`agent-probe.sh` appends one row per run to `~/agentprobe/results.tsv`, so a
tally across runs is a `sort | uniq -c`. Earlier versions printed the score to
stdout only, which is why the run counts in
[../docs/agent-chain-findings.md](../docs/agent-chain-findings.md) had to be kept
by hand and could not be re-derived from the run directories afterwards.

## Traps these encode

Each of these cost a wasted run before it went in the script. They are described
at length in [../docs/agent-chain-findings.md](../docs/agent-chain-findings.md).

- **`pgrep -f 'opencode run'` matches itself** and reports a probe that isn't
  there. Every match here uses the bracket trick — `opencode ru[n]`.
- **The newest session is usually a subagent's.** The orchestrator is the newest
  session with `parent_id is null`, *and* newer than a `$PREV` timestamp taken
  before the run — without the timestamp a failed run silently scores the
  previous one.
- **A hang looks exactly like a skipped fan-out.** A backgrounded server holds
  opencode's `bash` tool open forever. The stall watchdog kills the run when no
  new db rows land for 6 minutes, and the `tools left RUNNING` section names the
  culprit.
- **Killing a batch of probes kills the one that just started.** Check before
  starting a new run.
