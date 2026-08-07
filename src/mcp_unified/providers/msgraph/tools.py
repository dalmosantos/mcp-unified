"""Tools do Microsoft Graph: SharePoint (post-mortems, runbooks) e Teams (conversas)."""

from __future__ import annotations

from typing import Annotated, Any

from pydantic import Field

from ...toolsets import MSGRAPH
from ..registry import ServerContext
from .client import MSGraphClient


def register(server: Any, ctx: ServerContext) -> None:
    settings = ctx.settings.msgraph
    if not settings.configured:
        ctx.disable(
            "msgraph",
            "MSGRAPH_TENANT_ID/CLIENT_ID/CLIENT_SECRET não configurados — "
            "SharePoint e Teams indisponíveis",
        )
        return

    client = MSGraphClient(
        settings,
        timeout=ctx.settings.server.http_timeout_seconds,
        max_retries=ctx.settings.server.http_max_retries,
    )
    ctx.add_client("msgraph", client)

    if not ctx.enabled(MSGRAPH):
        return

    async def msgraph_search_sharepoint(
        query: Annotated[str, Field(description="Termo de busca, ex 'post-mortem pagamentos'")],
        limit: Annotated[int, Field(description="Máximo de resultados", ge=1, le=100)] = 25,
    ) -> Any:
        """Busca documentos no SharePoint — post-mortems, runbooks, procedimentos.

        Use quando precisar do conhecimento já documentado sobre um tipo de
        incidente, em vez de reconstruir a análise do zero.
        """
        return await client.search_content(query, ["driveItem"], size=limit)

    async def msgraph_get_document(
        drive_id: Annotated[str, Field(description="ID do drive (vem da busca)")],
        item_id: Annotated[str, Field(description="ID do item (vem da busca)")],
        include_content: Annotated[
            bool, Field(description="Se true, tenta baixar o conteúdo além dos metadados")
        ] = False,
    ) -> Any:
        """Metadados — e opcionalmente o conteúdo — de um documento do SharePoint."""
        metadata = await client.get_drive_item(drive_id, item_id)
        if not include_content:
            return metadata
        try:
            content = await client.get_drive_item_content(drive_id, item_id)
        except Exception as exc:  # noqa: BLE001 — binário ou sem permissão
            content = {"error": f"conteúdo indisponível: {exc}"}
        return {"metadata": metadata, "content": content}

    async def msgraph_search_teams_messages(
        query: Annotated[str, Field(description="Termo de busca nas conversas")],
        limit: Annotated[int, Field(description="Máximo de mensagens", ge=1, le=100)] = 25,
    ) -> Any:
        """Busca mensagens no Teams — decisões tomadas durante incidentes.

        Exige `ChannelMessage.Read.All`. Se retornar 403, é permissão do
        Azure AD, não credencial errada.
        """
        return await client.search_content(query, ["chatMessage"], size=limit)

    async def msgraph_get_channel_messages(
        team_id: Annotated[str, Field(description="ID do time")],
        channel_id: Annotated[str, Field(description="ID do canal")],
        limit: Annotated[int, Field(description="Máximo de mensagens", ge=1, le=100)] = 50,
    ) -> Any:
        """Mensagens recentes de um canal específico do Teams."""
        return await client.list_channel_messages(team_id, channel_id, top=limit)

    async def msgraph_list_sites(
        search: Annotated[str | None, Field(description="Filtro por nome do site")] = None,
    ) -> Any:
        """Lista sites do SharePoint. Use para descobrir onde a documentação vive."""
        return await client.list_sites(search)

    for fn in (
        msgraph_search_sharepoint,
        msgraph_get_document,
        msgraph_search_teams_messages,
        msgraph_get_channel_messages,
        msgraph_list_sites,
    ):
        server.add_tool(fn, name=fn.__name__)
        ctx.registered_tools.append(fn.__name__)
