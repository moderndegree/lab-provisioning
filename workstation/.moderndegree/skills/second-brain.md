# second-brain — vault memory (lab + ai-workstation)

## Role

The second brain is a **markdown vault** at `/data/brain` on ser5.  
**UI + agent MCP** live in the sibling **ai-workstation** project (not this repo):

- App: `/brain` graph, note pages, quick capture → `inbox/`
- MCP: `CORTEX_VAULT_DIR=/data/brain pnpm --dir <ai-workstation> mcp`

This skill covers **when/how to learn from mistakes**. It does not replace
TASK PACKAGE or quality-gate.

## Prefer MCP when available

If cortex MCP is configured in OpenCode/Hermes:

| Need | Tool |
|------|------|
| Find related lessons | `vault_search` / `vault_list_notes` |
| Read a note | `vault_get_note` |
| Dump a raw thought | `vault_capture` (inbox) |
| Neighborhood | `vault_local_graph` / `vault_backlinks` |

Pull **1–3** relevant notes into the TASK PACKAGE — never the whole vault.

## When to write a postmortem

**Draft** when:

- Wrong answer from thin context / bad question
- Repeated `BLOCKED` on the same missing package fields
- Painful gate or user-visible miss worth not repeating

**Skip** pure typos, one-shot plumbing, clean successes.

## How to draft a postmortem (files)

```bash
STAMP=$(date -u +%Y-%m-%d)
DEST="/data/brain/notes/postmortems/${STAMP}-short-title.md"
cp /data/brain/templates/postmortem.md "$DEST"
# fill: symptom, bad question, package gaps, fixes
```

Frontmatter should keep `tags` including `postmortem` and `status: draft` until
a human accepts. Wiki-link related notes when you know ids (`[[ser5]]`, etc.).

## Playbook promotion

Reusable rules → ACE playbooks (same files agents inject):

```bash
qloop playbook reflect /data/agentlab/playbooks/infra.md \
  --task "…" --trace "…" --outcome "FAIL …"
```

`/data/brain/playbooks` is usually a symlink to agentlab playbooks.

## Governance

- Personal/lab vault — no client-confidential dumps
- Draft ≠ ground truth until reviewed
- Packaging first; vault retrieval second; quality-gate for free-text polish

## Related

- `task-package.md` — prevent thin handoffs
- `quality-gate.md` — free-text polish
- lab docs: `docs/brain.md`
- app: `ai-workstation` README (Cortex MCP section)
