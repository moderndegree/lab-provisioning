# second-brain — remember and learn from mistakes

## Role

The lab second brain is a **markdown vault** on ser5 at `/data/brain` (Obsidian-
compatible). It stores postmortems and decisions. ACE **playbooks** live under
`/data/brain/playbooks` (usually symlinked to `/data/agentlab/playbooks`).

This skill does **not** replace TASK PACKAGE or quality-gate. It captures lessons
**after** failures so the next package and playbooks improve.

## When to write a postmortem

**Must draft** a postmortem note (or hand off clearly for the human) when:

- User-facing answer was wrong or unsafe because of thin context / bad question
- Subagent `BLOCKED` loops more than once on the same missing package fields
- `qloop gate` KEEP_BASELINE / FAIL and the user still got a bad outcome
- You repeated a known class of mistake (invented API, ignored non-goal, etc.)

**Skip** for pure typos, one-shot plumbing, or successes with nothing to learn.

## How to draft (orchestrator or devops)

On ser5 (or when the vault is mounted):

```bash
STAMP=$(date -u +%Y-%m-%d)
SLUG="short-title"   # kebab-case
DEST="/data/brain/notes/postmortems/${STAMP}-${SLUG}.md"
cp /data/brain/templates/postmortem.md "$DEST"
# edit DEST: symptom, bad question, package gaps, fixes
```

Fill at minimum:

1. Symptom  
2. Bad/wrong/incomplete question or handoff  
3. Missing TASK PACKAGE context  
4. 1–3 fixes for next time  

Tag drafts with `status: draft` until a human accepts.

## Playbook promotion

If the fix is a **reusable rule** (not a one-off):

```bash
qloop playbook reflect /data/agentlab/playbooks/infra.md \
  --task "…" --trace "…" --outcome "FAIL …"
```

or hand-edit `/data/brain/playbooks/*.md` (same files when symlinked).

Do **not** auto-rewrite the vault as ground truth without human review.

## Governance

- Default vault is **personal/lab** — no client-confidential dumps.
- Prefer tools + TASK PACKAGE over stuffing the whole vault into a prompt.
- Later cortex retrieval may pull 1–3 notes; until then, playbooks + explicit paths.

## Related skills

- `task-package.md` — prevent thin handoffs *before* work
- `quality-gate.md` — polish free-text *after* a solid package
