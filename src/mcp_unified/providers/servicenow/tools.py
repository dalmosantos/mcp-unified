"""Tools do ServiceNow e a fonte de timeline de mudanças.

A fonte de mudanças é o que responde *"teve change request aprovado nessa
janela?"* — a pergunta que, num incidente, mais frequentemente aponta a causa.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Annotated, Any

from pydantic import Field

from ...models import SessionWindow, Subject, TimelineEntry
from ...toolsets import SERVICENOW
from ..registry import ServerContext
from .client import (
    TABLE_CHANGE,
    TABLE_INCIDENT,
    TABLE_KB,
    TABLE_PROBLEM,
    ServiceNowClient,
)

logger = logging.getLogger(__name__)


def _parse_snow_ts(value: Any) -> datetime | None:
    """`YYYY-MM-DD HH:MM:SS` em UTC — o formato que a Table API devolve."""
    if not value:
        return None
    try:
        return datetime.strptime(str(value), "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


class ServiceNowTimelineSource:
    """Change requests e incidentes abertos dentro da janela."""

    source_name = "servicenow"

    def __init__(self, client: ServiceNowClient) -> None:
        self._client = client

    async def events_in_window(
        self, window: SessionWindow, *, query: str | None = None, limit: int = 100
    ) -> list[TimelineEntry]:
        entries: list[TimelineEntry] = []

        # Mudanças são o sinal mais valioso: um deploy aprovado minutos antes
        # da falha costuma ser a resposta.
        try:
            changes = await self._client.query_table(
                TABLE_CHANGE,
                query=self._client.window_query(window.start, window.end, "start_date"),
                limit=min(limit, 50),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("servicenow: change requests indisponíveis: %s", exc)
            changes = []

        for row in changes:
            ts = _parse_snow_ts(row.get("start_date") or row.get("sys_created_on"))
            if ts is None:
                continue
            entries.append(
                TimelineEntry(
                    ts=ts,
                    source=self.source_name,
                    kind="change_request",
                    summary=(
                        f"🔧 {row.get('number', '?')} "
                        f"{row.get('short_description', '')}"[:200]
                    ),
                    raw=row,
                )
            )

        try:
            incidents = await self._client.query_table(
                TABLE_INCIDENT,
                query=self._client.window_query(window.start, window.end),
                limit=min(limit, 50),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("servicenow: incidentes indisponíveis: %s", exc)
            incidents = []

        for row in incidents:
            ts = _parse_snow_ts(row.get("sys_created_on"))
            if ts is None:
                continue
            entries.append(
                TimelineEntry(
                    ts=ts,
                    source=self.source_name,
                    kind="incident",
                    summary=(
                        f"🎫 {row.get('number', '?')} "
                        f"{row.get('short_description', '')}"[:200]
                    ),
                    raw=row,
                )
            )

        return entries[:limit]

    async def subjects_in_window(
        self, window: SessionWindow, *, query: str, max_subjects: int = 10
    ) -> list[Subject]:
        """Usuários declarados como afetados nos chamados da janela."""
        try:
            rows = await self._client.query_table(
                TABLE_INCIDENT,
                query=self._client.window_query(window.start, window.end),
                limit=max_subjects * 5,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("servicenow: identidades indisponíveis: %s", exc)
            return []

        seen: dict[str, int] = {}
        for row in rows:
            caller = row.get("caller_id") or row.get("opened_by")
            if isinstance(caller, dict):
                caller = caller.get("display_value") or caller.get("value")
            if caller:
                seen[str(caller)] = seen.get(str(caller), 0) + 1

        subjects = [
            Subject(id=k, source=self.source_name, occurrences=v) for k, v in seen.items()
        ]
        subjects.sort(key=lambda s: s.occurrences, reverse=True)
        return subjects[:max_subjects]


def register(server: Any, ctx: ServerContext) -> None:
    settings = ctx.settings.servicenow
    if not settings.configured:
        ctx.disable(
            "servicenow",
            "SNOW_INSTANCE e credenciais não configuradas — mudanças e chamados "
            "não entram na timeline",
        )
        return

    client = ServiceNowClient(
        settings,
        timeout=ctx.settings.server.http_timeout_seconds,
        max_retries=ctx.settings.server.http_max_retries,
    )
    ctx.add_client("servicenow", client)

    source = ServiceNowTimelineSource(client)
    ctx.add_timeline_source(source)
    ctx.add_subject_resolver(source)

    if not ctx.enabled(SERVICENOW):
        return

    async def servicenow_search_incidents(
        query: Annotated[
            str | None,
            Field(description="Encoded query, ex 'priority=1^state!=6'"),
        ] = None,
        created_after: Annotated[
            str | None, Field(description="ISO8601; filtra por sys_created_on")
        ] = None,
        created_before: Annotated[str | None, Field(description="ISO8601")] = None,
        limit: Annotated[int, Field(description="Máximo de chamados", ge=1, le=200)] = 50,
    ) -> Any:
        """Busca incidentes no ServiceNow.

        Aceita encoded query nativa. Para janela temporal prefira
        `created_after`/`created_before`, que já monta o filtro correto.
        """
        parts = [p for p in [query] if p]
        if created_after or created_before:
            lo = _parse_snow_ts(created_after) or datetime.min.replace(tzinfo=timezone.utc)
            hi = _parse_snow_ts(created_before) or datetime.now(timezone.utc)
            parts.append(client.window_query(lo, hi))
        return await client.query_table(
            TABLE_INCIDENT, query="^".join(parts) if parts else None, limit=limit
        )

    async def servicenow_get_incident(
        identifier: Annotated[str, Field(description="Número (INC…) ou sys_id")],
    ) -> Any:
        """Busca um incidente pelo número visível ou pelo sys_id."""
        if identifier.upper().startswith("INC"):
            return await client.get_by_number(TABLE_INCIDENT, identifier)
        return await client.get_record(TABLE_INCIDENT, identifier)

    async def servicenow_search_change_requests(
        query: Annotated[str | None, Field(description="Encoded query")] = None,
        start_after: Annotated[str | None, Field(description="ISO8601; filtra start_date")] = None,
        start_before: Annotated[str | None, Field(description="ISO8601")] = None,
        limit: Annotated[int, Field(description="Máximo de mudanças", ge=1, le=200)] = 50,
    ) -> Any:
        """Busca change requests.

        É a tool que responde "teve mudança aprovada nessa janela?" — a
        pergunta que mais frequentemente aponta a causa de um incidente.
        """
        parts = [p for p in [query] if p]
        if start_after or start_before:
            lo = _parse_snow_ts(start_after) or datetime.min.replace(tzinfo=timezone.utc)
            hi = _parse_snow_ts(start_before) or datetime.now(timezone.utc)
            parts.append(client.window_query(lo, hi, "start_date"))
        return await client.query_table(
            TABLE_CHANGE, query="^".join(parts) if parts else None, limit=limit
        )

    async def servicenow_get_change_request(
        identifier: Annotated[str, Field(description="Número (CHG…) ou sys_id")],
    ) -> Any:
        """Busca uma change request pelo número visível ou pelo sys_id."""
        if identifier.upper().startswith("CHG"):
            return await client.get_by_number(TABLE_CHANGE, identifier)
        return await client.get_record(TABLE_CHANGE, identifier)

    async def servicenow_search_problems(
        query: Annotated[str | None, Field(description="Encoded query")] = None,
        limit: Annotated[int, Field(description="Máximo de problemas", ge=1, le=200)] = 50,
    ) -> Any:
        """Busca registros de problema — causas raiz recorrentes já documentadas."""
        return await client.query_table(TABLE_PROBLEM, query=query, limit=limit)

    async def servicenow_search_knowledge(
        search: Annotated[str, Field(description="Termo a buscar no texto do artigo")],
        limit: Annotated[int, Field(description="Máximo de artigos", ge=1, le=100)] = 20,
    ) -> Any:
        """Busca artigos da base de conhecimento — runbooks e procedimentos."""
        escaped = search.replace("^", " ")
        return await client.query_table(
            TABLE_KB,
            query=f"short_descriptionLIKE{escaped}^ORtextLIKE{escaped}",
            limit=limit,
            fields=["number", "short_description", "sys_id", "sys_updated_on"],
        )

    for fn in (
        servicenow_search_incidents,
        servicenow_get_incident,
        servicenow_search_change_requests,
        servicenow_get_change_request,
        servicenow_search_problems,
        servicenow_search_knowledge,
    ):
        server.add_tool(fn, name=fn.__name__)
        ctx.registered_tools.append(fn.__name__)
