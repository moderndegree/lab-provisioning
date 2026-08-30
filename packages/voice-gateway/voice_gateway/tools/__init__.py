"""Things the gateway can do besides talk to the model."""

from .hermes import HermesDelegate
from .web_search import WebSearch

__all__ = ["HermesDelegate", "WebSearch"]
