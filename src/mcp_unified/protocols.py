"""Contratos de extensão.

Este é o módulo que mantém o projeto extensível. A correlação **não** conhece
FullStory nem Datadog pelo nome — ela itera sobre o que estiver registrado
implementando estes protocolos. Plugar um provedor novo na linha do tempo é
implementar `events_in_window` e registrar; nenhuma tool de correlação muda.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from .models import SessionWindow, Subject, TimelineEntry


@runtime_checkable
class TimelineSource(Protocol):
    """Qualquer provedor que saiba produzir eventos numa janela temporal."""

    source_name: str

    async def events_in_window(
        self,
        window: SessionWindow,
        *,
        query: str | None = None,
        limit: int = 100,
    ) -> list[TimelineEntry]:
        """Devolve entradas normalizadas dentro da janela.

        Implementações devem falhar suave: se a consulta não fizer sentido
        para este provedor, devolver lista vazia em vez de levantar erro —
        a timeline unificada é melhor incompleta do que ausente.
        """
        ...


@runtime_checkable
class SubjectResolver(Protocol):
    """Qualquer provedor que saiba mapear uma janela/consulta em identidades afetadas."""

    source_name: str

    async def subjects_in_window(
        self,
        window: SessionWindow,
        *,
        query: str,
        max_subjects: int = 10,
    ) -> list[Subject]:
        """Devolve as identidades afetadas, mais frequentes primeiro."""
        ...
