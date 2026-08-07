"""Fusão da linha do tempo.

Este módulo **não conhece nenhum provedor pelo nome**. Ele recebe uma lista de
`TimelineSource` e pergunta a cada uma o que aconteceu na janela. É o que
permite plugar ServiceNow — ou qualquer coisa futura — sem tocar nas tools.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence

from ..models import SessionWindow, TimelineEntry
from ..protocols import TimelineSource

logger = logging.getLogger(__name__)


async def gather_entries(
    sources: Sequence[TimelineSource],
    window: SessionWindow,
    *,
    query: str | None = None,
    limit_per_source: int = 100,
) -> tuple[list[TimelineEntry], list[str], dict[str, str]]:
    """Consulta todas as fontes em paralelo e funde o resultado.

    Devolve `(entradas ordenadas, fontes usadas, fontes com falha)`. Uma fonte
    que falha não derruba a chamada — a timeline sai incompleta e o motivo vai
    no envelope, o que é melhor que não ter timeline.
    """
    if not sources:
        return [], [], {}

    async def _one(source: TimelineSource) -> tuple[str, list[TimelineEntry] | Exception]:
        try:
            entries = await source.events_in_window(
                window, query=query, limit=limit_per_source
            )
            return source.source_name, entries
        except Exception as exc:  # noqa: BLE001
            logger.warning("fonte %s falhou: %s", source.source_name, exc)
            return source.source_name, exc

    results = await asyncio.gather(*(_one(s) for s in sources))

    merged: list[TimelineEntry] = []
    used: list[str] = []
    failed: dict[str, str] = {}

    for name, outcome in results:
        if isinstance(outcome, Exception):
            failed[name] = str(outcome)
            continue
        used.append(name)
        merged.extend(outcome)

    merged.sort(key=lambda e: e.sort_key())
    return merged, used, failed


def render(entries: Sequence[TimelineEntry], *, max_entries: int | None = None) -> list[dict]:
    """Serializa para a resposta da tool.

    `raw` é omitido de propósito: numa timeline de 300 entradas ele domina o
    contexto sem agregar. Quem precisar do payload chama a tool do provedor.
    """
    selected = entries[:max_entries] if max_entries else entries
    return [
        {
            "ts": entry.ts.isoformat(),
            "source": entry.source,
            "kind": entry.kind,
            "summary": entry.summary,
        }
        for entry in selected
    ]


def summarize(entries: Sequence[TimelineEntry]) -> dict[str, object]:
    """Agregado barato para dar dimensão sem ler a timeline inteira."""
    by_source: dict[str, int] = {}
    notable = 0
    for entry in entries:
        by_source[entry.source] = by_source.get(entry.source, 0) + 1
        if entry.summary.startswith(("⚠", "🔧", "🎫")):
            notable += 1
    return {
        "total_entries": len(entries),
        "entries_by_source": by_source,
        "notable_entries": notable,
        "first_ts": entries[0].ts.isoformat() if entries else None,
        "last_ts": entries[-1].ts.isoformat() if entries else None,
    }
