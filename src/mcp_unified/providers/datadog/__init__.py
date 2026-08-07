"""Provedor Datadog: monitors, logs, RUM, Error Tracking, APM e Product Analytics."""

from .client import DatadogClient
from .tools import register

__all__ = ["DatadogClient", "register"]
