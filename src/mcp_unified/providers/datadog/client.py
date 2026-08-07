"""Cliente da API do Datadog (v1 e v2), sem SDK proprietário.

Duas particularidades vindas do servidor original em TS e mantidas:

- **Site por serviço:** logs e metrics podem viver em sites diferentes do
  padrão (`DD_LOGS_SITE`, `DD_METRICS_SITE`).
- **Mensagem de 403 explícita:** é de longe a falha mais comum do Datadog
  (chave de app sem escopo) e a mensagem genérica não ajuda ninguém.
"""

from __future__ import annotations

from typing import Any, Literal
from urllib.parse import quote

from ...config import DatadogSettings
from ...http import BaseApiClient

Service = Literal["default", "logs", "metrics"]


class DatadogClient(BaseApiClient):
    provider_name = "datadog"

    def __init__(self, settings: DatadogSettings, **kwargs: Any) -> None:
        self.settings = settings
        super().__init__(
            (settings.base_url_override or f"https://api.{settings.site}").rstrip("/"),
            headers={
                "DD-API-KEY": settings.api_key or "",
                "DD-APPLICATION-KEY": settings.app_key or "",
                "Content-Type": "application/json",
            },
            **kwargs,
        )

    def _forbidden_hint(self) -> str:
        return (
            "autorização negada (403) — verifique se a Application Key tem os escopos "
            "necessários. É a causa mais comum de falha no Datadog: a API Key sozinha "
            "permite envio, mas leitura exige App Key com escopo de leitura."
        )

    def _url_for(self, service: Service) -> str:
        if self.settings.base_url_override:
            return self.settings.base_url_override.rstrip("/")
        return f"https://api.{self.settings.host_for(service)}"

    async def _call(
        self, method: str, path: str, *, service: Service = "default", **kwargs: Any
    ) -> Any:
        return await self.request(method, path, base_url=self._url_for(service), **kwargs)

    # ------------------------------------------------------------- monitors

    async def list_monitors(
        self,
        *,
        group_states: str | None = None,
        tags: str | None = None,
        monitor_tags: str | None = None,
    ) -> Any:
        return await self._call(
            "GET",
            "/api/v1/monitor",
            params={
                "group_states": group_states,
                "tags": tags,
                "monitor_tags": monitor_tags,
            },
        )

    async def get_monitor(self, monitor_id: int) -> Any:
        return await self._call("GET", f"/api/v1/monitor/{monitor_id}")

    # ------------------------------------------------------------ dashboards

    async def list_dashboards(self, *, filter_configured: bool | None = None) -> Any:
        params = (
            {"filter[configured]": str(filter_configured).lower()}
            if filter_configured is not None
            else None
        )
        return await self._call("GET", "/api/v1/dashboard", params=params)

    async def get_dashboard(self, dashboard_id: str) -> Any:
        return await self._call("GET", f"/api/v1/dashboard/{quote(dashboard_id, safe='')}")

    # --------------------------------------------------------------- metrics

    async def search_metrics(self, query: str) -> Any:
        # `/api/v1/search` é o endpoint de busca de métricas — é o que o
        # `listMetrics` do SDK em TS chama por baixo.
        return await self._call(
            "GET", "/api/v1/search", service="metrics", params={"q": query}
        )

    async def get_metric_metadata(self, metric_name: str) -> Any:
        return await self._call(
            "GET",
            f"/api/v1/metrics/{quote(metric_name, safe='')}",
            service="metrics",
        )

    # ---------------------------------------------------------------- events

    async def list_events(
        self,
        *,
        start: int,
        end: int,
        priority: str | None = None,
        sources: str | None = None,
        tags: str | None = None,
        unaggregated: bool | None = None,
        exclude_aggregate: bool | None = None,
    ) -> Any:
        return await self._call(
            "GET",
            "/api/v1/events",
            params={
                "start": start,
                "end": end,
                "priority": priority,
                "sources": sources,
                "tags": tags,
                "unaggregated": unaggregated,
                "exclude_aggregate": exclude_aggregate,
            },
        )

    # ------------------------------------------------------------- incidents

    async def list_incidents(
        self,
        *,
        include_archived: bool | None = None,
        page_size: int | None = None,
        page_offset: int | None = None,
    ) -> Any:
        return await self._call(
            "GET",
            "/api/v2/incidents",
            params={
                "include_archived": include_archived,
                "page[size]": page_size,
                "page[offset]": page_offset,
            },
        )

    # ------------------------------------------------------------------ logs

    async def search_logs(self, body: dict[str, Any]) -> Any:
        return await self._call(
            "POST", "/api/v2/logs/events/search", service="logs", json=body
        )

    async def aggregate_logs(self, body: dict[str, Any]) -> Any:
        return await self._call(
            "POST", "/api/v2/logs/analytics/aggregate", service="logs", json=body
        )

    # ------------------------------------------------------------------- RUM

    async def search_rum_events(self, body: dict[str, Any]) -> Any:
        return await self._call("POST", "/api/v2/rum/events/search", json=body)

    async def aggregate_rum_events(self, body: dict[str, Any]) -> Any:
        return await self._call("POST", "/api/v2/rum/analytics/aggregate", json=body)

    async def list_rum_applications(self) -> Any:
        return await self._call("GET", "/api/v2/rum/applications")

    async def get_rum_application(self, app_id: str) -> Any:
        return await self._call("GET", f"/api/v2/rum/applications/{quote(app_id, safe='')}")

    # -------------------------------------------------------- error tracking

    async def search_error_tracking_issues(self, body: dict[str, Any]) -> Any:
        return await self._call("POST", "/api/v2/error-tracking/issues/search", json=body)

    async def get_error_tracking_issue(self, issue_id: str) -> Any:
        return await self._call(
            "GET", f"/api/v2/error-tracking/issues/{quote(issue_id, safe='')}"
        )

    async def update_error_tracking_issue_state(self, issue_id: str, state: str) -> Any:
        return await self._call(
            "PUT",
            f"/api/v2/error-tracking/issues/{quote(issue_id, safe='')}/state",
            json={"data": {"type": "issue_state", "attributes": {"state": state}}},
        )

    async def update_error_tracking_issue_assignee(self, issue_id: str, user_id: str) -> Any:
        return await self._call(
            "PUT",
            f"/api/v2/error-tracking/issues/{quote(issue_id, safe='')}/assignee",
            json={"data": {"type": "user", "id": user_id}},
        )

    # ----------------------------------------------------------- spans / APM

    async def search_spans(self, body: dict[str, Any]) -> Any:
        return await self._call("POST", "/api/v2/spans/events/search", json=body)

    async def aggregate_spans(self, body: dict[str, Any]) -> Any:
        return await self._call("POST", "/api/v2/spans/analytics/aggregate", json=body)

    async def list_spans(self, **params: Any) -> Any:
        return await self._call("GET", "/api/v2/spans/events", params=params)

    # ----------------------------------------------------- product analytics

    async def product_analytics_scalar(self, body: dict[str, Any]) -> Any:
        return await self._call(
            "POST", "/api/v2/product-analytics/analytics/scalar", json=body
        )

    async def product_analytics_timeseries(self, body: dict[str, Any]) -> Any:
        return await self._call(
            "POST", "/api/v2/product-analytics/analytics/timeseries", json=body
        )

    async def product_analytics_query_users(self, body: dict[str, Any]) -> Any:
        return await self._call("POST", "/api/v2/product-analytics/users/query", json=body)
