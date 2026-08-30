"""Kept as a shim while the bench takes over. Nothing new belongs here.

`WebSearch` and `HermesDelegate` moved to `voice_gateway.bench` when the
presence stopped being allowed to reach either of them. The session still
imports them from here for the transitional `presence_network` path, which is
the last caller and dies with that flag.
"""

from ..bench.hermes import HermesDelegate
from ..bench.web_search import WebSearch

__all__ = ["HermesDelegate", "WebSearch"]
