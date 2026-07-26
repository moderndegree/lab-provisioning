"""Registry of the lab's warm base models.

Two-base-model policy (mirrors `ollama_base_models` in
mini/ansible/group_vars/all.yml): exactly two models stay resident on mini,
with no baked system prompts — roles live in the prompts loopkit sends. Both
run at the 131072-token global window set on mini. Keep this table in sync with
group_vars when the policy changes.

WHY BOTH WARM SLOTS ARE MoE
mini (Strix Halo, gfx1151, 128 GB unified) is memory-bandwidth-bound, not
compute-bound. Decode speed tracks the *active* parameters read per token, not
the total parameter count. Measured anchor on this node:
qwen3.6:35b-a3b-mtp-q4_K_M (22 GB, 3B active) runs 70-80 t/s, implying
~185 GB/s of effective bandwidth (~86% of the ~215 GB/s ceiling). The rest
follows from that number:

    qwen3.6:35b-a3b q4   22 GB,  3B active   ->  70-80 t/s  (measured)
    qwen3-coder-next     51 GB,  3B active   ->  ~35-50 t/s (est.)
    gpt-oss:120b         65 GB,  5.1B active ->  ~30 t/s    (community-measured)
    qwen3.6:27b q4       17 GB, 27B DENSE    ->  ~11-15 t/s (185/17 ~= 11)

The dense 27B was the previous `coder` model: ~6x slower than `general` while
scoring ~11-15 points lower on SWE-bench Verified than qwen3-coder-next
(70.6-74.2 vs ~59). Never put a dense model in a warm slot on this hardware.

Aliases:
  general — 35B-A3B MoE (3B active), 70-80 t/s. Judging, reflection, general
            reasoning, orchestration. Default worker AND default judge.
  coder   — qwen3-coder-next, 80B-A3B MoE (3B active). Complex coding and deep
            reasoning; the quality escalation.
  heavy   — gpt-oss:120b, the "hard problem, take an hour" tier. Best general
            reasoning that fits 128 GB.
  judge   — nemotron-cascade-2, Mamba2-MoE. Math/algorithm escalation and an
            INDEPENDENT judge for best_of_n — a different model family judging
            beats a model grading its own samples.
  scout   — tiny model for cheap smoke tests.

CAUTION: mini pins only the warm pair (max_loaded_models=2), so `heavy`,
`judge` and `scout` each EVICT one of the pair and the next call pays a
reload. Fine for a smoke test or a scheduled off-hours job; never inside an
interactive loop. Use `evicts_warm_pair()` to check before a long run.

Speed figures marked (est.) stay marked until `make loopkit-bakeoff` measures
them on this box. Adopt a model change only on a measured win.
"""

from __future__ import annotations

from dataclasses import dataclass

# Global context window set on mini (OLLAMA_CONTEXT_LENGTH). The /v1 endpoint
# cannot set num_ctx per request and a Modelfile num_ctx below a model's native
# window is ignored, so this single value is the window every model gets.
# Paired with OLLAMA_NUM_PARALLEL=2 — the two multiply into the KV budget.
CONTEXT = 131072


@dataclass(frozen=True)
class ModelSpec:
    name: str            # Ollama model tag on mini
    role: str            # general | coder | heavy | judge | scout
    context: int         # window (budgeting hint; num_ctx is global on mini)
    output: int          # sane max_tokens for loop calls
    reasoning: bool      # whether the model thinks by default
    resident: bool       # True for the warm pair; False evicts one on load


MODELS: dict[str, ModelSpec] = {
    "general": ModelSpec("qwen3.6:35b-a3b-mtp-q4_K_M", "general", CONTEXT, 8192, True, True),
    "coder": ModelSpec("qwen3-coder-next:latest", "coder", CONTEXT, 8192, True, True),
    "heavy": ModelSpec("gpt-oss:120b", "heavy", CONTEXT, 8192, True, False),
    "judge": ModelSpec("nemotron-cascade-2:latest", "judge", CONTEXT, 8192, True, False),
    "scout": ModelSpec("nemotron-3-nano:4b", "scout", CONTEXT, 8192, False, False),
}

# Sensible defaults for the loop machinery: the fast MoE generates and judges;
# route coding/deep-reasoning suites to "coder" explicitly.
DEFAULT_WORKER = "general"   # produces candidate answers
DEFAULT_JUDGE = "general"    # critiques / selects / reflects


def resolve_model(alias_or_tag: str) -> str:
    """Map a role alias (e.g. 'coder') to its Ollama tag; pass tags through."""
    spec = MODELS.get(alias_or_tag)
    return spec.name if spec else alias_or_tag


def spec_for(alias_or_tag: str) -> ModelSpec | None:
    if alias_or_tag in MODELS:
        return MODELS[alias_or_tag]
    for spec in MODELS.values():
        if spec.name == alias_or_tag:
            return spec
    return None


def evicts_warm_pair(alias_or_tag: str) -> bool:
    """True when loading this model displaces a resident model on mini.

    mini pins exactly two models (OLLAMA_MAX_LOADED_MODELS=2), so anything
    outside the warm pair costs a reload on the next call to whichever model it
    displaced. Unknown tags are assumed non-resident.
    """
    spec = spec_for(alias_or_tag)
    return not spec.resident if spec else True
