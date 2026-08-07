"""Provedor Microsoft Graph: SharePoint e Teams (read-only)."""

from .client import MSGraphClient
from .tools import register

__all__ = ["MSGraphClient", "register"]
