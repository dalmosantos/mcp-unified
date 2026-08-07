"""Provedor FullStory: sessões, usuários, eventos, segmentos e analytics derivada."""

from .client import FullStoryClient
from .tools import FullStoryTimelineSource, register

__all__ = ["FullStoryClient", "FullStoryTimelineSource", "register"]
