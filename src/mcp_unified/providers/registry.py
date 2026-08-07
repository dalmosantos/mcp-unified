"""Registro central de provedores, clientes e fontes de correlação.

O `ServerContext` é o que circula entre os módulos de provedor. Ele guarda os
clientes já instanciados e, principalmente, as listas de `TimelineSource` e
`SubjectResolver` — que é como a correlação descobre com quem falar sem
conhecer ninguém pelo nome.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ..config import Settings
from ..protocols import SessionProvider, SubjectResolver, TimelineSource

if TYPE_CHECKING:  # pragma: no cover
    from ..http import BaseApiClient

logger = logging.getLogger(__name__)


@dataclass
class ServerContext:
    settings: Settings
    enabled_toolsets: set[str]
    clients: dict[str, Any] = field(default_factory=dict)
    timeline_sources: list[TimelineSource] = field(default_factory=list)
    subject_resolvers: list[SubjectResolver] = field(default_factory=list)
    session_provider: SessionProvider | None = None
    disabled: dict[str, str] = field(default_factory=dict)
    registered_tools: list[str] = field(default_factory=list)

    # ------------------------------------------------------------- registro

    def enabled(self, toolset: str) -> bool:
        return toolset in self.enabled_toolsets

    def add_client(self, name: str, client: BaseApiClient) -> None:
        self.clients[name] = client

    def add_timeline_source(self, source: TimelineSource) -> None:
        self.timeline_sources.append(source)
        logger.debug("fonte de timeline registrada: %s", source.source_name)

    def add_subject_resolver(self, resolver: SubjectResolver) -> None:
        self.subject_resolvers.append(resolver)
        logger.debug("resolvedor de identidade registrado: %s", resolver.source_name)

    def set_session_provider(self, provider: SessionProvider) -> None:
        """Registra quem sabe resolver sessões.

        Só um por servidor: sessão é um conceito de um produto de análise de
        frontend, e ter dois seria ambiguidade, não redundância.
        """
        if self.session_provider is not None:
            logger.warning(
                "provedor de sessão já registrado (%s); ignorando %s",
                self.session_provider.source_name,
                provider.source_name,
            )
            return
        self.session_provider = provider
        logger.debug("provedor de sessão registrado: %s", provider.source_name)

    def disable(self, provider: str, reason: str) -> None:
        """Marca um provedor como indisponível, com o motivo legível.

        O motivo aparece na resposta das tools de correlação, para que quem
        consome saiba que a timeline está incompleta e por quê.
        """
        self.disabled[provider] = reason
        logger.info("provedor %s desabilitado: %s", provider, reason)

    # -------------------------------------------------------------- consulta

    def timeline_source_names(self) -> list[str]:
        return [s.source_name for s in self.timeline_sources]

    def sources_for(self, names: list[str] | None) -> list[TimelineSource]:
        """Filtra as fontes registradas pelos nomes pedidos.

        `None` significa "todas". Nome desconhecido é ignorado em silêncio —
        pedir uma fonte que não está configurada não deve quebrar a chamada.
        """
        if not names:
            return list(self.timeline_sources)
        wanted = {n.strip().lower() for n in names}
        return [s for s in self.timeline_sources if s.source_name.lower() in wanted]

    async def aclose(self) -> None:
        for client in self.clients.values():
            close = getattr(client, "aclose", None)
            if close is not None:
                await close()
