# Second brain (lightweight)

Markdown vault on ser5 for **remembering and learning from mistakes**. Obsidian
is the recommended viewer; the files are the source of truth.

## Location

| Path | Host |
|------|------|
| `/data/brain` | ser5 (`enable_brain: true`, `brain` Ansible role) |

Provision: role runs after `agentlab` so `playbooks/` can symlink to
`/data/agentlab/playbooks` (one ACE source for humans + `qloop`).

## Open in Obsidian

1. Sync or mount `/data/brain` on the workstation (SSHFS, Syncthing, or copy).
2. Obsidian → **Open folder as vault** → select `brain/`.
3. Templates folder: `templates/` (postmortem, decision).

## Learn from mistakes

| Step | Action |
|------|--------|
| 1 | After a miss, copy `templates/postmortem.md` → `notes/postmortems/YYYY-MM-DD-….md` |
| 2 | Capture bad question + TASK PACKAGE gaps + fix for next time |
| 3 | Promote durable rules into `playbooks/*.md` or `qloop playbook reflect` |
| 4 | Measured quality stays in `/data/agentlab/runs.db` (`qloop summary`) |

Agents may **draft** postmortems; humans **accept** (no silent vault authority).

## How this fits the agent stack

```
TASK PACKAGE (session)     → enough context for small models now
playbooks/ (ACE)           → durable tactics injected into qloop / prompts
notes/postmortems/         → narrative memory for humans (+ later cortex retrieve)
runs.db                    → which strategies/models win
```

Order of operations on a task:

1. Orchestrator builds TASK PACKAGE (`task-package` skill)
2. Subagents run; free-text goes through `qloop gate` when required
3. On painful failure → postmortem note + optional playbook delta

## Backups

`enable_backups` includes `{{ data_mount }}/brain` in `restic_backup_paths`.

## Not in this MVP

- Embeddings / `cortex ask` (roadmap Phase 3)
- Syncthing role (operator can add later)
- Per-client subvaults (Phase 4)
- Auto-ingest of chat logs

## Related

- [`roadmap.md`](roadmap.md) Phase 3 (cortex)
- [`ai-loops.md`](ai-loops.md) (qloop + playbooks)
- `workstation/.moderndegree/skills/task-package.md`
- `workstation/.moderndegree/skills/second-brain.md`
