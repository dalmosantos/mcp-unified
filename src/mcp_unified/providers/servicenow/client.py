"""Cliente da Table API do ServiceNow.

Read-only por decisão de projeto: no nível L1 do agente de SRE nada é escrito
em sistema de chamado. Escrita fica fora do escopo até que exista revisão
específica para isso.
"""

from __future__ import annotations

import base64
import time
from datetime import datetime, timezone
from typing import Any

import httpx

from ...config import ServiceNowSettings, reveal
from ...errors import ProviderError
from ...http import BaseApiClient

TABLE_INCIDENT = "incident"
TABLE_CHANGE = "change_request"
TABLE_PROBLEM = "problem"
TABLE_KB = "kb_knowledge"


class ServiceNowClient(BaseApiClient):
    provider_name = "servicenow"

    def __init__(self, settings: ServiceNowSettings, **kwargs: Any) -> None:
        self.settings = settings
        self._token: str | None = None
        self._token_expiry: float = 0.0
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if settings.auth_mode == "basic":
            raw = f"{settings.username}:{reveal(settings.password)}".encode()
            headers["Authorization"] = f"Basic {base64.b64encode(raw).decode()}"
        super().__init__(settings.base_url, headers=headers, **kwargs)

    def _forbidden_hint(self) -> str:
        return (
            "acesso negado (403) — o usuário do ServiceNow precisa de leitura nas "
            "tabelas consultadas. Verifique as ACLs de incident/change_request."
        )

    async def _ensure_token(self) -> None:
        """OAuth2 client credentials, com cache até a expiração."""
        if self.settings.auth_mode != "oauth2":
            return
        if self._token and time.monotonic() < self._token_expiry:
            return

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{self.settings.base_url}/oauth_token.do",
                data={
                    "grant_type": "client_credentials",
                    "client_id": self.settings.client_id,
                    "client_secret": reveal(self.settings.client_secret),
                },
            )
        if response.status_code != 200:
            raise ProviderError(
                self.provider_name,
                f"falha ao obter token OAuth2 ({response.status_code})",
                status_code=response.status_code,
            )
        payload = response.json()
        self._token = payload.get("access_token")
        # Margem de 60s para não usar token na borda da expiração.
        self._token_expiry = time.monotonic() + float(payload.get("expires_in", 1800)) - 60
        self._client.headers["Authorization"] = f"Bearer {self._token}"

    async def query_table(
        self,
        table: str,
        *,
        query: str | None = None,
        limit: int = 50,
        offset: int = 0,
        fields: list[str] | None = None,
        display_value: bool = True,
    ) -> list[dict[str, Any]]:
        """Consulta genérica da Table API. Devolve sempre uma lista."""
        await self._ensure_token()
        params: dict[str, Any] = {
            "sysparm_limit": limit,
            "sysparm_offset": offset,
            "sysparm_display_value": "true" if display_value else "false",
            "sysparm_exclude_reference_link": "true",
        }
        if query:
            params["sysparm_query"] = query
        if fields:
            params["sysparm_fields"] = ",".join(fields)

        payload = await self.get(f"/api/now/table/{table}", params=params)
        result = payload.get("result") if isinstance(payload, dict) else None
        return result if isinstance(result, list) else []

    async def get_record(self, table: str, sys_id: str) -> dict[str, Any] | None:
        await self._ensure_token()
        payload = await self.get(
            f"/api/now/table/{table}/{sys_id}",
            params={"sysparm_display_value": "true"},
        )
        result = payload.get("result") if isinstance(payload, dict) else None
        return result if isinstance(result, dict) else None

    async def get_by_number(self, table: str, number: str) -> dict[str, Any] | None:
        """Busca por número visível (`INC0000123`) em vez de `sys_id`."""
        rows = await self.query_table(table, query=f"number={number}", limit=1)
        return rows[0] if rows else None

    @staticmethod
    def window_query(start: datetime, end: datetime, field: str = "sys_created_on") -> str:
        """Monta o trecho de encoded query para uma janela temporal.

        O ServiceNow espera UTC no formato `YYYY-MM-DD HH:MM:SS`.
        """
        fmt = "%Y-%m-%d %H:%M:%S"
        lo = start.astimezone(timezone.utc).strftime(fmt)
        hi = end.astimezone(timezone.utc).strftime(fmt)
        return f"{field}>={lo}^{field}<={hi}"
