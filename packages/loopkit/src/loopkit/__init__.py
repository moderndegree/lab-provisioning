"""loopkit — AI loop strategies for the moderndegree home lab.

Runs against mini's Ollama OpenAI-compatible endpoint (http://mini:11434/v1).
Loop primitives (refine, best_of_n), ACE-style evolving playbooks, an eval
runner with SQLite tracking, and STaR-style trace bootstrapping.
"""

from loopkit.client import ChatClient, ChatResult
from loopkit.models import MODELS, ModelSpec, resolve_model

__version__ = "0.1.0"

__all__ = [
    "ChatClient",
    "ChatResult",
    "MODELS",
    "ModelSpec",
    "resolve_model",
    "__version__",
]
