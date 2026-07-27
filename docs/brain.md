# Second brain

Plain-markdown vault on ser5 at **`/data/brain`**. Files are sacred; any UI or
index is optional and lives outside this repo’s runtime.

## What this repo provides

| Piece | Role |
|-------|------|
| Ansible `brain` role (`enable_brain`) | Seeds vault layout, postmortem/decision templates, restic path |
| ACE playbooks | Often symlinked from `/data/agentlab/playbooks` for `qloop` |
| Agent skill | `workstation/.moderndegree/skills/second-brain.md` — file-based postmortems |

## Provision

```yaml
# ser5 group_vars
enable_brain: true
```

```bash
make ser5-provision
```

Layout:

```text
/data/brain/
  inbox/
  notes/          # incl. postmortems/, decisions/
  sources/
  journal/
  mocs/
  playbooks/      # → agentlab when present
  templates/
```

## Learn from mistakes

| Step | Action |
|------|--------|
| 1 | After a miss, copy `templates/postmortem.md` → `notes/postmortems/YYYY-MM-DD-….md` |
| 2 | Capture bad question + TASK PACKAGE gaps + fix |
| 3 | Promote durable rules into `playbooks/*.md` or `qloop playbook reflect` |
| 4 | Measured strategy quality stays in `/data/agentlab` + `qloop summary` |

Agents draft notes; humans accept. No silent vault authority.

## Optional viewers

Open `/data/brain` in **Obsidian** (or any editor). Other products may also
point at this path; lab-provisioning does not depend on them.

## Backups

`enable_backups` includes `{{ data_mount }}/brain` in restic paths.

## Related

- [`roadmap.md`](roadmap.md) Phase 3 (full cortex retrieval loops — future)
- [`ai-loops.md`](ai-loops.md)
- Skills: task-package, quality-gate, second-brain
