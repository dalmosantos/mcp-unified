"""Cliente do Microsoft Graph — SharePoint e Teams num só cliente.

As duas fontes são a mesma API, o mesmo fluxo de auth (client credentials via
Azure AD), os mesmos rate limits e o mesmo tratamento de throttling. Separá-las
em dois clientes seria duplicar o token e a lógica de retry sem ganho.

⚠️ Ler mensagens de Teams exige `ChannelMessage.Read.All` — permissão de
aplicação ampla, com consentimento de administrador. Historicamente é o item
mais lento de projetos assim; peça antes de precisar.
"""

from __future__ import annotations

import contextlib
import time
from typing import Any

import httpx

from ...config import MSGraphSettings
from ...errors import ProviderError
from ...http import BaseApiClient


class MSGraphClient(BaseApiClient):
    provider_name = "msgraph"

    def __init__(self, settings: MSGraphSettings, **kwargs: Any) -> None:
        self.settings = settings
        self._token: str | None = None
        self._token_expiry: float = 0.0
        super().__init__(
            settings.base_url,
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            **kwargs,
        )

    def _forbidden_hint(self) -> str:
        return (
            "acesso negado (403) — verifique as permissões de aplicação no Azure AD. "
            "SharePoint exige Sites.Read.All; mensagens de Teams exigem "
            "ChannelMessage.Read.All, com consentimento de administrador."
        )

    async def _ensure_token(self) -> None:
        if self._token and time.monotonic() < self._token_expiry:
            return

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                self.settings.token_url,
                data={
                    "grant_type": "client_credentials",
                    "client_id": self.settings.client_id,
                    "client_secret": self.settings.client_secret,
                    "scope": "https://graph.microsoft.com/.default",
                },
            )
        if response.status_code != 200:
            detail = ""
            with contextlib.suppress(ValueError):
                detail = response.json().get("error_description", "")[:200]
            raise ProviderError(
                self.provider_name,
                f"falha ao obter token do Azure AD ({response.status_code}) {detail}",
                status_code=response.status_code,
            )
        payload = response.json()
        self._token = payload.get("access_token")
        self._token_expiry = time.monotonic() + float(payload.get("expires_in", 3600)) - 60
        self._client.headers["Authorization"] = f"Bearer {self._token}"

    async def graph_get(self, path: str, **kwargs: Any) -> Any:
        await self._ensure_token()
        return await self.get(path, **kwargs)

    async def graph_post(self, path: str, **kwargs: Any) -> Any:
        await self._ensure_token()
        return await self.post(path, **kwargs)

    # ------------------------------------------------------------- SharePoint

    async def list_sites(self, search: str | None = None) -> Any:
        return await self.graph_get("/sites", params={"search": search or "*"})

    async def search_content(self, query: str, entity_types: list[str], size: int = 25) -> Any:
        """Busca unificada do Graph. Serve tanto para arquivos quanto para mensagens."""
        return await self.graph_post(
            "/search/query",
            json={
                "requests": [
                    {
                        "entityTypes": entity_types,
                        "query": {"queryString": query},
                        "from": 0,
                        "size": size,
                    }
                ]
            },
        )

    async def get_drive_item_content(self, drive_id: str, item_id: str) -> Any:
        return await self.graph_get(f"/drives/{drive_id}/items/{item_id}/content")

    async def get_drive_item(self, drive_id: str, item_id: str) -> Any:
        return await self.graph_get(f"/drives/{drive_id}/items/{item_id}")

    # ------------------------------------------------------------------ Teams

    async def list_channel_messages(self, team_id: str, channel_id: str, *, top: int = 50) -> Any:
        return await self.graph_get(
            f"/teams/{team_id}/channels/{channel_id}/messages", params={"$top": top}
        )
