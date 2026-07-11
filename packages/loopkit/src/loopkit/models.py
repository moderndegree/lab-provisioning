"""Registry of the lab's warm base models.

Two-base-model policy (mirrors `ollama_base_models` in
mini/ansible/group_vars/all.yml): exactly two models stay resident on mini,
with no baked system prompts — roles live in the prompts loopkit sends. Both
run at their full native 262144-token window (set globally on mini). Keep this
table in sync with group_vars when the policy changes.

Aliases:
  general — 35B MoE (3B active), fast; judging, reflection, general reasoning
  coder   — 27B dense; complex coding and deep reasoning
  scout   — tiny model for cheap smoke tests. CAUTION: mini pins only the warm
            pair (max_loaded_models=2), so using scout evicts one of them and
            the next call pays a reload. Fine for a smoke test, not for loops.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ModelSpec:
    name: str            # Ollama model tag on mini
    role: str            # general | coder | scout
    context: int         # window (budgeting hint; num_ctx is global on mini)
    output: int          # sane max_tokens for loop calls
    reasoning: bool      # whether the model thinks by default


MODELS: dict[str, ModelSpec] = {
    "general": ModelSpec("qwen3.6:35b-a3b-mtp-q4_K_M", "general", 262144, 8192, True),
    "coder": ModelSpec("qwen3.6:27b-mtp-q4_K_M", "coder", 262144, 8192, True),
    "scout": ModelSpec("nemotron-3-nano:4b", "scout", 262144, 8192, False),
}

# Sensible defaults for the loop machinery: the fast MoE generates and judges;
# route coding/deep-reasoning suites to "coder" explicitly.
DEFAULT_WORKER = "general"   # produces candidate answers
DEFAULT_JUDGE = "general"    # critiques / selects / reflects


def resolve_model(alias_or_tag: str) -> str:
    """Map a role alias (e.g. 'oracle') to its Ollama tag; pass tags through."""
    spec = MODELS.get(alias_or_tag)
    return spec.name if spec else alias_or_tag


def spec_for(alias_or_tag: str) -> ModelSpec | None:
    if alias_or_tag in MODELS:
        return MODELS[alias_or_tag]
    for spec in MODELS.values():
        if spec.name == alias_or_tag:
            return spec
    return None
