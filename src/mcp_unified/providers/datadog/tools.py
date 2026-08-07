"""Tools do Datadog: core, RUM/Error Tracking, APM e Product Analytics."""

from __future__ import annotations

from typing import Annotated, Any

from pydantic import BaseModel, Field

from ...toolsets import DATADOG_APM, DATADOG_CORE, DATADOG_PA, DATADOG_RUM
from ..registry import ServerContext
from .client import DatadogClient
from .sources import (
    DatadogEventsSource,
    DatadogLogsSource,
    DatadogRUMSource,
    DatadogSpansSource,
)

# --------------------------------------------------------------------- schemas
# Modelos aninhados viram JSON Schema automaticamente pelo SDK. Sem eles, o
# modelo teria que adivinhar a forma de `filter` / `compute` / `group_by`.


class LogFilter(BaseModel):
    query: str | None = Field(default=None, description="Sintaxe de busca do Datadog")
    from_: str | None = Field(
        default=None, alias="from", description="Início: ISO8601 ou relativo, ex 'now-15m'"
    )
    to: str | None = Field(default=None, description="Fim: ISO8601 ou relativo, ex 'now'")
    indexes: list[str] | None = Field(default=None, description="Índices a consultar")

    model_config = {"populate_by_name": True}

    def wire(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        if self.query is not None:
            out["query"] = self.query
        if self.from_ is not None:
            out["from"] = self.from_
        if self.to is not None:
            out["to"] = self.to
        if self.indexes:
            out["indexes"] = self.indexes
        return out


class Compute(BaseModel):
    aggregation: str = Field(description="count, avg, sum, min, max, pc95…")
    metric: str | None = Field(default=None, description="Faceta numérica a agregar")
    type: str | None = Field(default=None, description="'total' ou 'timeseries'")


class GroupBySort(BaseModel):
    aggregation: str
    order: str = Field(default="desc", description="asc ou desc")


class GroupBy(BaseModel):
    facet: str = Field(description="Faceta pela qual agrupar, ex '@usr.id'")
    limit: int | None = Field(default=None, description="Máximo de grupos")
    sort: GroupBySort | None = None


def _clean(model: BaseModel | None) -> dict[str, Any] | None:
    return model.model_dump(exclude_none=True) if model else None


# -------------------------------------------------------------------- registro


def register(server: Any, ctx: ServerContext) -> None:
    settings = ctx.settings.datadog
    if not settings.configured:
        ctx.disable(
            "datadog",
            "DD_API_KEY e DD_APP_KEY são ambas necessárias — a API Key sozinha "
            "só permite envio, não leitura",
        )
        return

    client = DatadogClient(
        settings,
        timeout=ctx.settings.server.http_timeout_seconds,
        max_retries=ctx.settings.server.http_max_retries,
    )
    ctx.add_client("datadog", client)

    logs_source = DatadogLogsSource(client, ctx.settings.correlation.user_attr)
    ctx.add_timeline_source(logs_source)
    ctx.add_subject_resolver(logs_source)
    ctx.add_timeline_source(DatadogEventsSource(client))
    if ctx.enabled(DATADOG_RUM):
        ctx.add_timeline_source(DatadogRUMSource(client))
    if ctx.enabled(DATADOG_APM):
        ctx.add_timeline_source(DatadogSpansSource(client))

    if ctx.enabled(DATADOG_CORE):
        _register_core(server, client, ctx)
    if ctx.enabled(DATADOG_RUM):
        _register_rum(server, client, ctx)
    if ctx.enabled(DATADOG_APM):
        _register_apm(server, client, ctx)
    if ctx.enabled(DATADOG_PA):
        _register_pa(server, client, ctx)


def _add(server: Any, ctx: ServerContext, fn: Any) -> None:
    server.add_tool(fn, name=fn.__name__)
    ctx.registered_tools.append(fn.__name__)


# ------------------------------------------------------------------------ core


def _register_core(server: Any, client: DatadogClient, ctx: ServerContext) -> None:
    async def datadog_get_monitors(
        group_states: Annotated[
            list[str] | None, Field(description="Filtra por estado: alert, warn, no data, ok")
        ] = None,
        tags: Annotated[str | None, Field(description="Filtra por tags do escopo")] = None,
        monitor_tags: Annotated[str | None, Field(description="Filtra por tags do monitor")] = None,
        limit: Annotated[int, Field(description="Máximo de monitores", ge=1, le=1000)] = 100,
    ) -> Any:
        """Lista monitores do Datadog, opcionalmente filtrados por estado ou tags.

        Use `group_states=['alert']` para ver só o que está disparando agora.
        """
        result = await client.list_monitors(
            group_states=",".join(group_states) if group_states else None,
            tags=tags,
            monitor_tags=monitor_tags,
        )
        # O limite é aplicado no cliente porque o endpoint v1 não o suporta —
        # mesmo comportamento do servidor original.
        if isinstance(result, list) and len(result) > limit:
            return result[:limit]
        return result

    async def datadog_get_monitor(
        monitor_id: Annotated[int, Field(description="ID numérico do monitor")],
    ) -> Any:
        """Configuração e estado completos de um monitor específico."""
        return await client.get_monitor(monitor_id)

    async def datadog_get_dashboards(
        filter_configured: Annotated[bool | None, Field(description="Só os configurados")] = None,
        limit: Annotated[int, Field(description="Máximo de dashboards", ge=1, le=1000)] = 100,
    ) -> Any:
        """Lista dashboards. Use para descobrir IDs antes de buscar a definição."""
        result = await client.list_dashboards(filter_configured=filter_configured)
        if isinstance(result, dict) and isinstance(result.get("dashboards"), list):
            result["dashboards"] = result["dashboards"][:limit]
        return result

    async def datadog_get_dashboard(
        dashboard_id: Annotated[str, Field(description="ID do dashboard")],
    ) -> Any:
        """Definição completa de um dashboard: widgets, layout e configuração."""
        return await client.get_dashboard(dashboard_id)

    async def datadog_get_metrics(
        query: Annotated[str, Field(description="Padrão de busca, ex 'system.cpu'")],
    ) -> Any:
        """Busca métricas disponíveis por padrão de nome."""
        return await client.search_metrics(query)

    async def datadog_get_metric_metadata(
        metric_name: Annotated[str, Field(description="Nome completo da métrica")],
    ) -> Any:
        """Metadados de uma métrica: tipo, unidade, descrição e intervalo."""
        return await client.get_metric_metadata(metric_name)

    async def datadog_get_events(
        start: Annotated[int, Field(description="Início em epoch (segundos)")],
        end: Annotated[int, Field(description="Fim em epoch (segundos)")],
        priority: Annotated[str | None, Field(description="'normal' ou 'low'")] = None,
        sources: Annotated[str | None, Field(description="Fontes separadas por vírgula")] = None,
        tags: Annotated[str | None, Field(description="Tags separadas por vírgula")] = None,
        limit: Annotated[int, Field(description="Máximo de eventos", ge=1, le=1000)] = 100,
    ) -> Any:
        """Eventos da plataforma numa janela: deploys, alertas e mudanças.

        É a tool que responde "teve deploy nesse intervalo?".
        """
        result = await client.list_events(
            start=start, end=end, priority=priority, sources=sources, tags=tags
        )
        if isinstance(result, dict) and isinstance(result.get("events"), list):
            result["events"] = result["events"][:limit]
        return result

    async def datadog_get_incidents(
        include_archived: Annotated[bool | None, Field(description="Incluir arquivados")] = None,
        page_size: Annotated[int, Field(description="Itens por página", ge=1, le=100)] = 50,
        page_offset: Annotated[int, Field(description="Deslocamento", ge=0)] = 0,
    ) -> Any:
        """Lista incidentes do Incident Management do Datadog."""
        return await client.list_incidents(
            include_archived=include_archived, page_size=page_size, page_offset=page_offset
        )

    async def datadog_search_logs(
        filter: Annotated[LogFilter | None, Field(description="Filtro de busca")] = None,
        sort: Annotated[str | None, Field(description="'timestamp' ou '-timestamp'")] = None,
        limit: Annotated[int, Field(description="Máximo de logs", ge=1, le=1000)] = 100,
        cursor: Annotated[str | None, Field(description="Cursor de paginação")] = None,
    ) -> Any:
        """Busca logs com filtro.

        Exemplo de query: `service:pagamentos status:error`. Datas aceitam
        formato relativo (`now-15m`) ou ISO8601.
        """
        page: dict[str, Any] = {"limit": limit}
        if cursor:
            page["cursor"] = cursor
        body: dict[str, Any] = {"filter": filter.wire() if filter else {}, "page": page}
        if sort:
            body["sort"] = sort
        return await client.search_logs(body)

    async def datadog_aggregate_logs(
        filter: Annotated[LogFilter | None, Field(description="Filtro de busca")] = None,
        compute: Annotated[list[Compute] | None, Field(description="Agregações")] = None,
        group_by: Annotated[list[GroupBy] | None, Field(description="Agrupamentos")] = None,
    ) -> Any:
        """Agrega logs para extrair métricas e agrupamentos.

        Use quando quiser contar ou agrupar em vez de listar — por exemplo,
        quantos erros por serviço, ou quais usuários foram afetados.
        """
        body: dict[str, Any] = {"filter": filter.wire() if filter else {}}
        if compute:
            body["compute"] = [_clean(c) for c in compute]
        if group_by:
            body["group_by"] = [_clean(g) for g in group_by]
        return await client.aggregate_logs(body)

    for fn in (
        datadog_get_monitors,
        datadog_get_monitor,
        datadog_get_dashboards,
        datadog_get_dashboard,
        datadog_get_metrics,
        datadog_get_metric_metadata,
        datadog_get_events,
        datadog_get_incidents,
        datadog_search_logs,
        datadog_aggregate_logs,
    ):
        _add(server, ctx, fn)


# ------------------------------------------------------- RUM / Error Tracking


def _register_rum(server: Any, client: DatadogClient, ctx: ServerContext) -> None:
    async def datadog_rum_search_events(
        filter: Annotated[LogFilter | None, Field(description="Filtro de busca")] = None,
        sort: Annotated[str | None, Field(description="'timestamp' ou '-timestamp'")] = None,
        limit: Annotated[int, Field(description="Máximo de eventos", ge=1, le=1000)] = 100,
    ) -> Any:
        """Busca eventos de RUM (Real User Monitoring).

        É a visão do Datadog sobre o frontend — complementar ao FullStory,
        não substituta: aqui estão métricas e erros; lá está o comportamento.
        """
        body: dict[str, Any] = {
            "filter": filter.wire() if filter else {},
            "page": {"limit": limit},
        }
        if sort:
            body["sort"] = sort
        return await client.search_rum_events(body)

    async def datadog_rum_aggregate_events(
        filter: Annotated[LogFilter | None, Field(description="Filtro de busca")] = None,
        compute: Annotated[list[Compute] | None, Field(description="Agregações")] = None,
        group_by: Annotated[list[GroupBy] | None, Field(description="Agrupamentos")] = None,
    ) -> Any:
        """Agrega eventos de RUM para métricas por visão, aplicação ou usuário."""
        body: dict[str, Any] = {"filter": filter.wire() if filter else {}}
        if compute:
            body["compute"] = [_clean(c) for c in compute]
        if group_by:
            body["group_by"] = [_clean(g) for g in group_by]
        return await client.aggregate_rum_events(body)

    async def datadog_rum_list_applications() -> Any:
        """Lista as aplicações de RUM configuradas."""
        return await client.list_rum_applications()

    async def datadog_rum_get_application(
        application_id: Annotated[str, Field(description="ID da aplicação de RUM")],
    ) -> Any:
        """Detalhes de uma aplicação de RUM."""
        return await client.get_rum_application(application_id)

    async def datadog_error_tracking_search_issues(
        query: Annotated[str | None, Field(description="Filtro, ex 'service:pagamentos'")] = None,
        from_: Annotated[str | None, Field(description="Início, ex 'now-24h'")] = None,
        to: Annotated[str | None, Field(description="Fim, ex 'now'")] = None,
        limit: Annotated[int, Field(description="Máximo de issues", ge=1, le=100)] = 25,
    ) -> Any:
        """Busca issues do Error Tracking — exceções agrupadas por assinatura.

        Mais útil que buscar logs de erro crus: aqui as ocorrências já vêm
        deduplicadas por causa, com contagem e primeira/última ocorrência.
        """
        body: dict[str, Any] = {
            "data": {
                "type": "search_request",
                "attributes": {
                    "filter": {
                        k: v
                        for k, v in (("query", query), ("from", from_), ("to", to))
                        if v is not None
                    },
                    "page": {"limit": limit},
                },
            }
        }
        return await client.search_error_tracking_issues(body)

    async def datadog_error_tracking_get_issue(
        issue_id: Annotated[str, Field(description="ID da issue")],
    ) -> Any:
        """Detalhes de uma issue do Error Tracking, incluindo stack trace."""
        return await client.get_error_tracking_issue(issue_id)

    async def datadog_error_tracking_update_issue_state(
        issue_id: Annotated[str, Field(description="ID da issue")],
        state: Annotated[str, Field(description="OPEN, RESOLVED ou IGNORED")],
    ) -> Any:
        """Altera o estado de uma issue. Escreve no Datadog."""
        return await client.update_error_tracking_issue_state(issue_id, state)

    async def datadog_error_tracking_update_issue_assignee(
        issue_id: Annotated[str, Field(description="ID da issue")],
        user_id: Annotated[str, Field(description="ID do usuário no Datadog")],
    ) -> Any:
        """Atribui uma issue a um usuário. Escreve no Datadog."""
        return await client.update_error_tracking_issue_assignee(issue_id, user_id)

    for fn in (
        datadog_rum_search_events,
        datadog_rum_aggregate_events,
        datadog_rum_list_applications,
        datadog_rum_get_application,
        datadog_error_tracking_search_issues,
        datadog_error_tracking_get_issue,
        datadog_error_tracking_update_issue_state,
        datadog_error_tracking_update_issue_assignee,
    ):
        _add(server, ctx, fn)


# ------------------------------------------------------------------- APM


def _register_apm(server: Any, client: DatadogClient, ctx: ServerContext) -> None:
    async def datadog_apm_search_spans(
        query: Annotated[str | None, Field(description="Filtro, ex 'service:pagamentos'")] = None,
        from_: Annotated[str | None, Field(description="Início, ex 'now-15m'")] = None,
        to: Annotated[str | None, Field(description="Fim, ex 'now'")] = None,
        limit: Annotated[int, Field(description="Máximo de spans", ge=1, le=1000)] = 100,
    ) -> Any:
        """Busca spans de APM — onde o tempo foi gasto no backend.

        Use quando o sintoma é lentidão: os spans mostram qual chamada
        demorou, não só que a requisição demorou.
        """
        body = {
            "data": {
                "type": "search_request",
                "attributes": {
                    "filter": {
                        k: v
                        for k, v in (("query", query), ("from", from_), ("to", to))
                        if v is not None
                    },
                    "page": {"limit": limit},
                    "sort": "timestamp",
                },
            }
        }
        return await client.search_spans(body)

    async def datadog_apm_aggregate_spans(
        query: Annotated[str | None, Field(description="Filtro de busca")] = None,
        from_: Annotated[str | None, Field(description="Início")] = None,
        to: Annotated[str | None, Field(description="Fim")] = None,
        compute: Annotated[list[Compute] | None, Field(description="Agregações")] = None,
        group_by: Annotated[list[GroupBy] | None, Field(description="Agrupamentos")] = None,
    ) -> Any:
        """Agrega spans: latência por serviço, throughput por recurso, p95 por rota."""
        attributes: dict[str, Any] = {
            "filter": {
                k: v for k, v in (("query", query), ("from", from_), ("to", to)) if v is not None
            }
        }
        if compute:
            attributes["compute"] = [_clean(c) for c in compute]
        if group_by:
            attributes["group_by"] = [_clean(g) for g in group_by]
        return await client.aggregate_spans(
            {"data": {"type": "aggregate_request", "attributes": attributes}}
        )

    async def datadog_apm_list_spans(
        query: Annotated[str | None, Field(description="Filtro de busca")] = None,
        limit: Annotated[int, Field(description="Máximo de spans", ge=1, le=1000)] = 100,
    ) -> Any:
        """Lista spans recentes (GET, mais simples que a busca com corpo)."""
        return await client.list_spans(**{"filter[query]": query, "page[limit]": limit})

    for fn in (datadog_apm_search_spans, datadog_apm_aggregate_spans, datadog_apm_list_spans):
        _add(server, ctx, fn)


# ------------------------------------------------------- Product Analytics


def _register_pa(server: Any, client: DatadogClient, ctx: ServerContext) -> None:
    async def datadog_product_analytics_scalar(
        query: Annotated[str, Field(description="Consulta de eventos de produto")],
        from_: Annotated[str | None, Field(description="Início")] = None,
        to: Annotated[str | None, Field(description="Fim")] = None,
        compute: Annotated[list[Compute] | None, Field(description="Agregações")] = None,
        group_by: Annotated[list[GroupBy] | None, Field(description="Agrupamentos")] = None,
    ) -> Any:
        """Valor escalar agregado de Product Analytics — um número por grupo."""
        attributes: dict[str, Any] = {
            "filter": {
                k: v for k, v in (("query", query), ("from", from_), ("to", to)) if v is not None
            }
        }
        if compute:
            attributes["compute"] = [_clean(c) for c in compute]
        if group_by:
            attributes["group_by"] = [_clean(g) for g in group_by]
        return await client.product_analytics_scalar({"data": {"attributes": attributes}})

    async def datadog_product_analytics_timeseries(
        query: Annotated[str, Field(description="Consulta de eventos de produto")],
        from_: Annotated[str | None, Field(description="Início")] = None,
        to: Annotated[str | None, Field(description="Fim")] = None,
        interval: Annotated[str | None, Field(description="Granularidade, ex '1h'")] = None,
        compute: Annotated[list[Compute] | None, Field(description="Agregações")] = None,
    ) -> Any:
        """Série temporal de Product Analytics — o mesmo número ao longo do tempo."""
        attributes: dict[str, Any] = {
            "filter": {
                k: v for k, v in (("query", query), ("from", from_), ("to", to)) if v is not None
            }
        }
        if interval:
            attributes["interval"] = interval
        if compute:
            attributes["compute"] = [_clean(c) for c in compute]
        return await client.product_analytics_timeseries({"data": {"attributes": attributes}})

    async def datadog_product_analytics_query_users(
        query: Annotated[str, Field(description="Filtro de usuários")],
        from_: Annotated[str | None, Field(description="Início")] = None,
        to: Annotated[str | None, Field(description="Fim")] = None,
        limit: Annotated[int, Field(description="Máximo de usuários", ge=1, le=1000)] = 100,
    ) -> Any:
        """Consulta usuários em Product Analytics por comportamento."""
        attributes: dict[str, Any] = {
            "filter": {
                k: v for k, v in (("query", query), ("from", from_), ("to", to)) if v is not None
            },
            "page": {"limit": limit},
        }
        return await client.product_analytics_query_users({"data": {"attributes": attributes}})

    for fn in (
        datadog_product_analytics_scalar,
        datadog_product_analytics_timeseries,
        datadog_product_analytics_query_users,
    ):
        _add(server, ctx, fn)
