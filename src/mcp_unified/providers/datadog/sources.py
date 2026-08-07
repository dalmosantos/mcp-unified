"""Fontes de timeline e resolvedor de identidade do Datadog.

São quatro fontes separadas em vez de uma só para que `build_unified_timeline`
possa filtrar por granularidade — pedir só logs, ou logs + spans, sem trazer
tudo. Cada uma implementa `TimelineSource`; a de logs também implementa
`SubjectResolver`.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from ...models import SessionWindow, Subject, TimelineEntry
from .client import DatadogClient

logger = logging.getLogger(__name__)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_ts(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        seconds = value / 1000 if value > 1e11 else value
        return datetime.fromtimestamp(seconds, tz=timezone.utc)
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _items(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        data = payload.get("data")
        if isinstance(data, list):
            return [d for d in data if isinstance(d, dict)]
    if isinstance(payload, list):
        return [d for d in payload if isinstance(d, dict)]
    return []


class _Base:
    def __init__(self, client: DatadogClient) -> None:
        self._client = client

    async def _safe(self, coro: Any, label: str) -> Any:
        try:
            return await coro
        except Exception as exc:  # noqa: BLE001 — timeline incompleta > ausente
            logger.warning("datadog/%s indisponível: %s", label, exc)
            return None

    @staticmethod
    def _within(ts: datetime | None, window: SessionWindow) -> bool:
        """Confere a janela do lado do cliente.

        As consultas já mandam `from`/`to`, mas confiar só nisso é frágil: um
        endpoint que ignore o filtro (proxy, gateway, ambiente de teste) faria
        entradas de outro horário entrarem na timeline como se fossem do
        incidente. Uma entrada fora de janela é pior que uma faltando.
        """
        return ts is not None and window.start <= ts <= window.end


class DatadogLogsSource(_Base):
    """Logs. Também resolve identidades, agregando por facet de usuário."""

    source_name = "datadog-logs"

    def __init__(self, client: DatadogClient, user_attr: str) -> None:
        super().__init__(client)
        self.user_attr = user_attr

    async def events_in_window(
        self, window: SessionWindow, *, query: str | None = None, limit: int = 100
    ) -> list[TimelineEntry]:
        body = {
            "filter": {"query": query or "*", "from": _iso(window.start), "to": _iso(window.end)},
            "sort": "timestamp",
            "page": {"limit": min(limit, 1000)},
        }
        payload = await self._safe(self._client.search_logs(body), "logs")
        entries: list[TimelineEntry] = []
        for item in _items(payload):
            attrs = item.get("attributes") or {}
            ts = _parse_ts(attrs.get("timestamp"))
            if not self._within(ts, window):
                continue
            inner = attrs.get("attributes") or {}
            service = attrs.get("service") or inner.get("service") or "?"
            status = attrs.get("status") or inner.get("status") or "info"
            message = (attrs.get("message") or "")[:200]
            marker = "⚠ " if str(status).lower() in ("error", "critical", "emergency") else ""
            entries.append(
                TimelineEntry(
                    ts=ts,
                    source=self.source_name,
                    kind=f"log.{status}",
                    summary=f"{marker}[{service}] {message}",
                    raw=item,
                )
            )
        return entries

    async def subjects_in_window(
        self, window: SessionWindow, *, query: str, max_subjects: int = 10
    ) -> list[Subject]:
        """Agrega logs por facet de usuário para descobrir quem foi afetado."""
        body = {
            "filter": {"query": query, "from": _iso(window.start), "to": _iso(window.end)},
            "compute": [{"aggregation": "count", "type": "total"}],
            "group_by": [{"facet": self.user_attr, "limit": max_subjects, "total": False}],
        }
        payload = await self._safe(self._client.aggregate_logs(body), "logs/aggregate")
        subjects: list[Subject] = []
        for bucket in _items(payload):
            by = bucket.get("by") or {}
            value = by.get(self.user_attr)
            if not value:
                continue
            computes = bucket.get("computes") or {}
            count = next((v for v in computes.values() if isinstance(v, (int, float))), 1)
            subjects.append(Subject(id=str(value), source=self.source_name, occurrences=int(count)))
        subjects.sort(key=lambda s: s.occurrences, reverse=True)
        return subjects[:max_subjects]


class DatadogRUMSource(_Base):
    """Eventos de RUM — erros de frontend vistos do lado do Datadog."""

    source_name = "datadog-rum"

    async def events_in_window(
        self, window: SessionWindow, *, query: str | None = None, limit: int = 100
    ) -> list[TimelineEntry]:
        body = {
            "filter": {"query": query or "*", "from": _iso(window.start), "to": _iso(window.end)},
            "sort": "timestamp",
            "page": {"limit": min(limit, 1000)},
        }
        payload = await self._safe(self._client.search_rum_events(body), "rum")
        entries: list[TimelineEntry] = []
        for item in _items(payload):
            attrs = item.get("attributes") or {}
            ts = _parse_ts(attrs.get("timestamp"))
            if not self._within(ts, window):
                continue
            inner = attrs.get("attributes") or {}
            event_type = (inner.get("type") or attrs.get("type") or "rum") if inner else "rum"
            view = (inner.get("view") or {}).get("url", "") if isinstance(inner, dict) else ""
            error = (inner.get("error") or {}).get("message", "") if isinstance(inner, dict) else ""
            summary = f"⚠ {error}" if error else f"{event_type} {view}"
            entries.append(
                TimelineEntry(
                    ts=ts,
                    source=self.source_name,
                    kind=f"rum.{event_type}",
                    summary=summary[:200],
                    raw=item,
                )
            )
        return entries


class DatadogSpansSource(_Base):
    """Spans de APM — onde o tempo foi gasto no backend."""

    source_name = "datadog-spans"

    async def events_in_window(
        self, window: SessionWindow, *, query: str | None = None, limit: int = 100
    ) -> list[TimelineEntry]:
        body = {
            "data": {
                "attributes": {
                    "filter": {
                        "query": query or "*",
                        "from": _iso(window.start),
                        "to": _iso(window.end),
                    },
                    "sort": "timestamp",
                    "page": {"limit": min(limit, 1000)},
                },
                "type": "search_request",
            }
        }
        payload = await self._safe(self._client.search_spans(body), "spans")
        entries: list[TimelineEntry] = []
        for item in _items(payload):
            attrs = item.get("attributes") or {}
            ts = _parse_ts(attrs.get("start_timestamp") or attrs.get("timestamp"))
            if not self._within(ts, window):
                continue
            resource = attrs.get("resource_name") or attrs.get("resource") or "?"
            service = attrs.get("service") or "?"
            duration_ns = attrs.get("duration") or 0
            duration_ms = round(duration_ns / 1_000_000) if duration_ns else 0
            slow = "⚠ " if duration_ms >= 1000 else ""
            entries.append(
                TimelineEntry(
                    ts=ts,
                    source=self.source_name,
                    kind="span",
                    summary=f"{slow}[{service}] {resource} ({duration_ms}ms)",
                    raw=item,
                )
            )
        return entries


class DatadogEventsSource(_Base):
    """Eventos da plataforma — deploys, alertas de monitor, mudanças."""

    source_name = "datadog-events"

    async def events_in_window(
        self, window: SessionWindow, *, query: str | None = None, limit: int = 100
    ) -> list[TimelineEntry]:
        payload = await self._safe(
            self._client.list_events(
                start=int(window.start.timestamp()),
                end=int(window.end.timestamp()),
                tags=query if query and ":" in query else None,
            ),
            "events",
        )
        raw_events = []
        if isinstance(payload, dict):
            raw_events = payload.get("events") or []
        entries: list[TimelineEntry] = []
        for event in raw_events[:limit]:
            if not isinstance(event, dict):
                continue
            ts = _parse_ts(event.get("date_happened"))
            if not self._within(ts, window):
                continue
            title = event.get("title") or "(sem título)"
            alert_type = event.get("alert_type") or "info"
            marker = "⚠ " if alert_type in ("error", "warning") else ""
            entries.append(
                TimelineEntry(
                    ts=ts,
                    source=self.source_name,
                    kind=f"event.{alert_type}",
                    summary=f"{marker}{title}"[:200],
                    raw=event,
                )
            )
        return entries
