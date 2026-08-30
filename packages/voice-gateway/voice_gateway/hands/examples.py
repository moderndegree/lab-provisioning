"""Worked examples of irreversible actions. NOT REGISTERED, on purpose.

Copy one into `default_registry()` when you have decided that action is worth a
voice trigger. Reading it here costs nothing; registering it is the decision, and
the decision is yours — the strategy lists "which actions count as irreversible
enough to require spoken confirmation" as one of the things it will not answer
for you.

Three rules the examples follow, and any new action should:

  reversible is DECLARED, and pessimistically. If undoing it needs a human, a
  password, or a backup, it is not reversible. `systemctl restart` on a service
  that reloads 45 GB of weights is reversible in principle and irreversible in
  practice — the restart is fine, the twenty minutes are not.

  `describe` is a QUESTION A PERSON WOULD ANSWER, spoken aloud. Not "run
  systemctl restart openwebui" — "Restart Open WebUI? It'll be down for a few
  seconds."

  the command is FIXED. Nothing transcribed reaches argv. The phrase patterns
  decide WHICH action runs; they never supply its arguments. An action that needs
  a parameter from speech is an action that needs a much more careful design than
  a regex.
"""

from __future__ import annotations

import re

from .registry import Action, shell

# Reversible in principle, irreversible in the way that matters: mini reloads
# ~45 GB of weights on restart, so this is a twenty-minute outage of everything
# the presence needs to speak at all. Declared False for that reason, which is
# exactly the judgment call the flag exists to record.
RESTART_INFERENCE = Action(
    name="restart-inference",
    phrases=(re.compile(r"^\s*restart (?:the )?(?:inference|llama|mini)\b", re.I),),
    run=lambda: shell("ssh", "mini", "systemctl", "--user", "restart", "llama-servers.target"),
    describe="Restart inference on mini? It reloads about 45 gigabytes, so you'd lose me for a while.",
    reversible=False,
)

RESTART_OPENWEBUI = Action(
    name="restart-openwebui",
    phrases=(re.compile(r"^\s*restart (?:the )?(?:open ?web ?ui|web ui)\b", re.I),),
    run=lambda: shell("systemctl", "--user", "restart", "openwebui.service"),
    describe="Restart Open WebUI? It'll be down for a few seconds.",
    reversible=False,
)

# The one that most obviously wants confirming, and the reason the whole
# mechanism exists: a transcription error here is not recoverable by saying
# "no, sorry, I meant the other thing".
RUN_BACKUP = Action(
    name="run-backup",
    phrases=(re.compile(r"^\s*(?:run|start|take) (?:a |the )?backup\b", re.I),),
    run=lambda: shell("systemctl", "start", "restic-backup.service"),
    describe="Start a restic backup now? It'll run for a while and hit the disk hard.",
    reversible=False,
)
