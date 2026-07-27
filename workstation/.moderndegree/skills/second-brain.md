# second-brain — vault memory (files on ser5)

## Role

The second brain is a **markdown vault** at `/data/brain` on ser5 (seeded by the
`brain` Ansible role). Use it to **learn from mistakes** after failures.

This skill does **not** replace TASK PACKAGE or quality-gate. This repo does
**not** ship a vault UI or MCP — write and read **files**. Optional external
tools (Obsidian, other apps) may open the same folder; they are not required
for lab-provisioning agents.

## When to write a postmortem

**Draft** when:

- Wrong answer from thin context / bad question
- Repeated `BLOCKED` on the same missing package fields
- Painful gate or user-visible miss worth not repeating

**Skip** pure typos, one-shot plumbing, clean successes.

## How to draft a postmortem

On ser5 (or when `/data/brain` is mounted):

```bash
STAMP=$(date -u +%Y-%m-%d)
DEST="/data/brain/notes/postmortems/${STAMP}-short-title.md"
cp /data/brain/templates/postmortem.md "$DEST"
# fill: symptom, bad question, package gaps, fixes
```

Keep frontmatter `status: draft` until a human accepts. Prefer short, concrete
bullets over essays.

## Playbook promotion

Reusable rules → ACE playbooks (agent-facing tactics):

```bash
qloop playbook reflect /data/agentlab/playbooks/infra.md \
  --task "…" --trace "…" --outcome "FAIL …"
```

`/data/brain/playbooks` is often a symlink to agentlab playbooks.

## Governance

- Personal/lab vault — no client-confidential dumps
- Draft ≠ ground truth until reviewed
- Packaging first; quality-gate for free-text polish; postmortems after misses

## Related

- `task-package.md` — prevent thin handoffs
- `quality-gate.md` — free-text polish
- lab docs: `docs/brain.md`
