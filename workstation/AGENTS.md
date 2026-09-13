# How to use OpenCode on Yoga

One model on mini:8090. One window.

If you are **plan**: write a short slice list, then stop. Do not dispatch.
If you are **orchestrator**: you already switched. Call `task` with `worker` for
the next slice. Do not tell the user to Tab. Do not implement.

User loop: Plan until the design is right → Tab to Orchestrator → it launches
one worker. Do not open a second OpenCode. Build is disabled.

## Slice the work (worker context)

The orchestrator breaks the plan into **small serial slices**. One worker, one
slice, then the next. A slice is one package, one feature, or one failing test
— not "implement the whole plan."

Each `task()` to a worker is a **package**, not a dump:

- goal
- done-when (observable)
- constraints
- **paths** (and line ranges if known) — never file contents, never the tree

The worker has `read`/`grep`/`glob`. It opens what it needs. If the package is
missing a path or a done-when, the worker returns BLOCKED with the exact gaps
instead of guessing.

## Protect the orchestrator window

The orchestrator's context lasts the whole session. A worker's is discarded
when it finishes. Keep the orchestrator thin:

- Do not read files in full or grep the repo "to understand." Dispatch `explore`
  for search; it returns **paths + one line each**, not file bodies.
- Do not paste a worker transcript, diff, or file into the next `task()` or
  into the orchestrator's own replies.
- Keep: the plan bullets, the slice list, the last `@@RESULT`. Drop the rest.
- Workers (and explore) reply **only** with:

@@RESULT
status: PASS | FAIL | BLOCKED
summary: <one line>
evidence: <command+output or path:line. "looks correct" is FAIL>
handoff: <next slice or done>
@@END

Plan → Orchestrator in the same session costs a cold prefill (~45–50s at 60k).
Expected. Two writers at once is the 70–120s failure.
