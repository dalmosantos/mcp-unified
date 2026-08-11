"""Esqueleto do pipeline reativo: alerta → contexto → classificação → rota.

O fluxo é determinístico e a ordem é fixa. A única chamada ao modelo é
`Classifier.classify`; tudo antes é coleta e tudo depois é decisão em código.
Essa separação é o que permite medir contra o golden dataset: você reexecuta a
rota sobre classificações gravadas, sem gastar token nenhum.

Os colaboradores são `Protocol` — mesma escolha do `protocols.py` do servidor.
O pipeline não conhece FAISS, nem cliente MCP, nem canal do Teams; conhece
quatro contratos. Dá para testar tudo com dublês antes de qualquer um existir.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Protocol

from schemas import AlertClassification, Channel, Notification

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# SEGURANÇA: allowlist de tools do mcp-unified.
#
# A tarefa do agente é ler e decidir. Nenhuma tool que altere estado em sistema
# de terceiro está registrada — não porque o prompt pede, mas porque o cliente
# MCP não as expõe. `FORBIDDEN_TOOLS` não é usada em código: existe para que um
# `grep` prove a fronteira em dez segundos.
#
# Esta é a camada do cliente. A do servidor é `--safe-mode` (toolsets.py:119),
# e é a mais forte das duas: o cliente não consegue contorná-la. Use as duas.
# ─────────────────────────────────────────────────────────────────────────────

ALLOWED_TOOLS: tuple[str, ...] = (
    # correlação — o produto do servidor, e o que o agente não consegue refazer
    "build_unified_timeline",
    "correlate_session_with_logs",
    "find_sessions_for_incident",
    # o que disparou
    "datadog_get_monitor",
    "datadog_get_monitors",
    "datadog_search_logs",
    "datadog_aggregate_logs",
    "datadog_get_events",
    "datadog_error_tracking_search_issues",
    "datadog_error_tracking_get_issue",
    # quem foi afetado
    "datadog_rum_search_events",
    "datadog_rum_aggregate_events",
    "fullstory_get_session_events",
    "fullstory_get_session_link",
    "fullstory_get_user_events",
    # já aconteceu antes?
    "servicenow_search_incidents",
    "servicenow_get_incident",
    "servicenow_search_change_requests",
    "servicenow_search_knowledge",
    "msgraph_search_sharepoint",
    "msgraph_search_teams_messages",
)

FORBIDDEN_TOOLS: tuple[str, ...] = (
    "datadog_error_tracking_update_issue_state",  # não fecha issue
    "datadog_error_tracking_update_issue_assignee",  # não redistribui trabalho humano
)


class AlertSource(Protocol):
    """De onde vêm os alertas. Polling do Datadog na Semana 3."""

    async def poll(self) -> list[dict[str, Any]]: ...


class IncidentMemory(Protocol):
    """Histórico pesquisável — o índice FAISS das Semanas 1–2."""

    async def similar(self, alert: dict[str, Any], *, limit: int = 5) -> list[str]: ...


class ContextGatherer(Protocol):
    """Impacto ao vivo, via mcp-unified. Só tools de `ALLOWED_TOOLS`."""

    async def gather(self, alert: dict[str, Any]) -> dict[str, Any]: ...


class Classifier(Protocol):
    """A única chamada ao modelo. Devolve o tipo, não texto."""

    async def classify(
        self,
        alert: dict[str, Any],
        *,
        history: list[str],
        live_context: dict[str, Any],
    ) -> AlertClassification: ...


class Notifier(Protocol):
    """Entrega. Falhar aqui não derruba nada: o alerta cru segue em paralelo."""

    async def send(
        self, classification: AlertClassification, *, channel: Channel
    ) -> Notification: ...


def should_page(
    classification: AlertClassification,
    *,
    confidence_threshold: float = 0.85,
    already_notified: bool = False,
) -> tuple[Channel, str | None]:
    """Decide a rota. Função pura: sem rede, sem modelo, sem relógio.

    Devolve `(canal, motivo_do_adiamento)`. É a peça mais testável do pipeline
    e a que mais erra na prática, então ela existe separada de propósito —
    reexecutar a rota sobre classificações gravadas custa zero token.

    A ordem das checagens importa. Duplicata vence tudo: um alerta já
    notificado não vira digest, vira silêncio. Confiança vence severidade: um
    `critical` que o modelo não sustenta é exatamente o caso em que interromper
    o time custa mais que esperar.
    """
    if already_notified:
        return "suppressed", "duplicata de alerta já notificado"

    if classification.confidence < confidence_threshold:
        return "daily_digest", (
            f"confiança {classification.confidence:.2f} abaixo do limiar {confidence_threshold:.2f}"
        )

    if not classification.should_notify_immediately:
        return "daily_digest", classification.deferral_reason

    if classification.severity in ("critical", "high"):
        return "teams_immediate", None

    return "daily_digest", f"severidade {classification.severity} não justifica interrupção"


class ReactiveAgent:
    """Compõe as cinco etapas. Sem laço, sem estado entre alertas.

    Cada alerta atravessa o pipeline uma vez e produz uma `Notification`. Não há
    conversa, não há memória de turno — o que o agente precisa lembrar está no
    índice de histórico, não no processo.
    """

    def __init__(
        self,
        *,
        source: AlertSource,
        memory: IncidentMemory,
        context: ContextGatherer,
        classifier: Classifier,
        notifier: Notifier,
        confidence_threshold: float = 0.85,
    ) -> None:
        self.source = source
        self.memory = memory
        self.context = context
        self.classifier = classifier
        self.notifier = notifier
        self.confidence_threshold = confidence_threshold
        self._seen: dict[str, datetime] = {}

    async def handle(self, alert: dict[str, Any]) -> Notification:
        """Um alerta, do polling à entrega."""
        alert_id = str(alert.get("id") or alert.get("alert_id") or "")

        history = await self.memory.similar(alert)
        live_context = await self.context.gather(alert)

        classification = await self.classifier.classify(
            alert, history=history, live_context=live_context
        )

        channel, deferral = should_page(
            classification,
            confidence_threshold=self.confidence_threshold,
            already_notified=alert_id in self._seen,
        )
        logger.info("alerta %s → %s%s", alert_id, channel, f" ({deferral})" if deferral else "")

        try:
            notification = await self.notifier.send(classification, channel=channel)
        except Exception as exc:  # noqa: BLE001
            # Entrega que falha não derruba o ciclo: o alerta cru do Datadog
            # continua chegando ao Teams em paralelo. O agente é aditivo.
            logger.warning("entrega falhou para %s: %s", alert_id, exc)
            return Notification(
                alert_id=alert_id,
                channel=channel,
                headline=classification.title,
                body_markdown=classification.summary,
                delivery_error=str(exc),
            )

        if channel == "teams_immediate":
            self._seen[alert_id] = datetime.now(tz=timezone.utc)
        return notification

    async def tick(self) -> list[Notification]:
        """Um ciclo de polling. O agendamento é de quem chama."""
        return [await self.handle(alert) for alert in await self.source.poll()]
