# Second brain

Plain-markdown vault on ser5 at **`/data/brain`**. Files are sacred; indexes and
apps are disposable.

## Two repos, one vault

| Repo | Role |
|------|------|
| **lab-provisioning** (`brain` Ansible role) | Seeds `/data/brain` layout, templates, playbook link, restic path |
| **[ai-workstation](https://github.com/moderndegree/ai-workstation)** | UI (`/brain` graph, capture, notes), SQLite FTS/embeddings for *app* knowledge, **cortex MCP** for agents |

Do **not** reimplement the graph UI or MCP here. Point the app at the vault:

```bash
# ai-workstation/.env.local
CORTEX_VAULT_DIR=/data/brain
```

```bash
# Agents (OpenCode / Hermes / Claude Desktop) — stdio MCP
CORTEX_VAULT_DIR=/data/brain pnpm --dir ~/projects/ai-workstation mcp
```

MCP tool/prompt list lives in ai-workstation's own README — don't duplicate it
here, it will drift.

## Provision

```yaml
# ser5 group_vars
enable_brain: true   # role: brain
```

```bash
make ser5-provision   # or limit to brain role
```

Layout matches `ai-workstation` `VAULT_FOLDERS`: `inbox/`, `notes/`, `sources/`,
`journal/`, `mocs/`, `playbooks/` (+ `templates/`, nested `notes/postmortems/`).


## Learn from mistakes

| Step | Where |
|------|--------|
| Capture raw thought | ai-workstation quick capture → `inbox/`, or drop a file |
| Postmortem after a miss | `notes/postmortems/YYYY-MM-DD-….md` from `templates/postmortem.md` |
| Durable agent tactic | `playbooks/*.md` |
| Serving benchmarks | `packages/inference-bench` (not the vault) |

Agents: see `workstation/.moderndegree/skills/second-brain.md`. Prefer MCP
search/capture when ai-workstation MCP is configured; otherwise write files
under `/data/brain` with human review.

## Obsidian (optional)

Open `/data/brain` as a vault for offline editing. Same files as the app.
Syncthing ser5 ↔ workstation is recommended later; not automated in this role.

## Backups

`enable_backups` includes `{{ data_mount }}/brain` in restic paths.

## Not here (use ai-workstation)

- 3D graph UI, wiki-link rendering
- Inbox triage, digest, contradiction-sweep, or resurfacing loops
- Hybrid embedding retrieval for the vault (app has embeddings path for its own DB)

## Related

- Sibling: `../ai-workstation` README (Second Brain + Cortex MCP)
- Skills: task-package, second-brain
