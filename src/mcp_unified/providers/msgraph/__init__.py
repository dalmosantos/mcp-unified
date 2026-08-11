"""Provedor Microsoft Graph: SharePoint e Teams (read-only).

Escopo deliberado: só o que `graph.microsoft.com/v1.0` expõe destes dois
produtos. Power Platform (PowerApps, Power Automate, Dataverse),
Exchange/Outlook e Entra ID têm outro endpoint e outros escopos — entrariam
como provedor próprio, não esticando este.
"""

from .client import MSGraphClient
from .tools import register

__all__ = ["MSGraphClient", "register"]
