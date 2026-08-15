# Lab rules — ser5

Auto-loaded into every Hermes system prompt from `HERMES_HOME`. Keep this SHORT:
it costs tokens on every turn and sits in the cache-stable block. Detail belongs
in skills; only **precedence** and **hard prohibitions** belong here — the things
that must hold even when a skill says otherwise.

## Delegating to OpenCode

The `opencode-lab` skill is **authoritative on this machine** and overrides the
generic `opencode` skill wherever the two differ. Read it before delegating.

Two rules stated here because the generic skill says the opposite, and a skill
that is merely *available* loses to one that matches the question:

- **Never pass `--model` to `opencode run`.** Models are configured in
  `~/.config/opencode/opencode.json` and served by mini over the tailnet.
  `--model openrouter/...` is wrong here and sends the work off this hardware.
- **Never run `opencode auth login`.** There is no provider to authenticate to.

Every non-trivial ask is a TASK PACKAGE with done-when criteria that can fail.
The contract is `~/.config/opencode/.moderndegree/skills/task-package.md`.

## Model routing

Inference belongs on mini: `http://mini:8090/v1` (qwen3.6-35b-a3b-mtp). Do not
introduce third-party model providers, API keys, or `--model`/`--provider`
overrides that route work off this hardware.
