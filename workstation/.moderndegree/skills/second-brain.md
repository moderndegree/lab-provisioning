# second-brain — vault memory via cortex MCP (+ files)

## Role

The second brain is a **markdown vault** at `/data/brain` (ser5). OpenCode is
wired to the **cortex** MCP server from ai-workstation (`opencode.json`).

This skill is **required** for: (1) learning after misses, (2) MCP-first capture.
It does **not** replace TASK PACKAGE (which already requires a cortex search
pass) or quality-gate.

## Cortex tools (use these names)

| Tool | Use |
|------|-----|
| `vault_search` | Find related notes / postmortems / playbooks |
| `vault_list_notes` | Browse by kind |
| `vault_get_note` | Read full note by id |
| `vault_backlinks` / `vault_local_graph` | Neighborhood of a note |
| `vault_stats` | Empty vault / health check |
| `vault_capture` | Drop raw text into `inbox/` (preferred over shell) |

Pull **1–3** notes into a TASK PACKAGE — never the whole vault. Full search
rules live in `task-package.md`.

## When to write a postmortem

**Must** capture a lesson when:

- User-facing answer was wrong/unsafe from thin context or a bad question
- Subagent `BLOCKED` loops more than once on the same package gaps
- Painful miss you do not want to repeat

**Skip** pure typos, one-shot plumbing, clean successes.

## How to record a lesson (MCP-first)

**Preferred:**

1. `vault_capture` with a short structured body (symptom, bad question, package
   gaps, 1–3 fixes). Title line first — it becomes the inbox slug.
2. Tell the user the note landed in `inbox/` and should be triaged into
   `notes/postmortems/` (human or nightly triage).
3. If there is a **durable rule**, also promote to ACE playbooks:

```bash
qloop playbook reflect /data/agentlab/playbooks/infra.md \
  --task "…" --trace "…" --outcome "FAIL …"
```

**Fallback if MCP down:** write a file from the template:

```bash
STAMP=$(date -u +%Y-%m-%d)
DEST="/data/brain/notes/postmortems/${STAMP}-short-title.md"
cp /data/brain/templates/postmortem.md "$DEST"
# edit DEST
```

Drafts are not ground truth until a human accepts. No client-confidential dumps.

## Capture vs postmortem

| Intent | Action |
|--------|--------|
| Quick thought / link | `vault_capture` only |
| Structured failure analysis | `vault_capture` with postmortem fields, or template under `notes/postmortems/` |
| Agent operating rule | playbook reflect / edit `playbooks/*.md` |

## Governance

- Personal/lab vault only
- Packaging + cortex search **before** work; capture **after** misses
- quality-gate polishes free-text; cortex remembers lessons

## Related

- `task-package.md` — cortex search in the package loop
- `quality-gate.md` — free-text polish
- `docs/brain.md` — vault layout
