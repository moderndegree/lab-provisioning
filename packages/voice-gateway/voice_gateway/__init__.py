"""Streaming voice loop for the lab.

Runs on ser5. Never on mini — mini serves inference only, and this is a loop
with per-connection state, which is exactly what that rule excludes.
"""

__version__ = "0.1.0"
