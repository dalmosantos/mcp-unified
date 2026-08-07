"""Tools de análise via modelo.

Duas tools, e ambas existem para resolver problema concreto:

1. `analyze_incident_timeline` — a timeline unificada pode ter centenas de
   entradas. Jogar isso no contexto de quem chamou é caro e ruim de ler.
2. `nl_to_datadog_query` — fecha parcialmente a lacuna de "métricas por
   linguagem natural" que a FullStory só oferece no MCP hospedado dela.
"""

from __future__ import annotations

import json
import logging
from typing import Annotated, Any

from pydantic import Field

from ..correlation import identity, timeline
from ..correlation.window import derive_session_window
from ..models import CorrelationMode
from ..providers.registry import ServerContext
from ..toolsets import LLM
from .base import build_provider
from .redact import redact
from .schemas import IncidentAnalysis, QueryTranslation

logger = logging.getLogger(__name__)

ANALYSIS_SYSTEM = """\
Você é um analista de confiabilidade experiente. Recebe uma linha do tempo já \
correlacionada entre o comportamento do usuário no frontend e a telemetria de \
backend, e produz um veredito estruturado.

Regras:
- Cite entradas específicas da timeline como evidência. Não afirme sem lastro.
- Se a timeline não sustentar uma conclusão, diga confiança baixa. É melhor que \
inventar causa.
- Aponte sinais que contradizem sua hipótese, quando existirem.
- Correlação temporal não é causalidade: um deploy antes da falha é indício, \
não prova.
"""

QUERY_SYSTEM = """\
Você traduz perguntas em português para a sintaxe de consulta do Datadog.

Regras:
- Produza apenas a consulta; não invente nomes de serviço que não foram citados.
- Quando precisar assumir um nome de atributo ou serviço, registre em `caveats`.
- Sintaxe: `chave:valor`, `-chave:valor` para negar, `AND`/`OR`, `*` como curinga.
- Atributos customizados levam `@`, ex: `@usr.id:12345`.
"""


def register(server: Any, ctx: ServerContext) -> None:
    if not ctx.enabled(LLM):
        return

    settings = ctx.settings.llm
    if not settings.configured:
        ctx.disable(
            "llm",
            "MCP_LLM_PROVIDER não configurado — tools de análise indisponíveis. "
            "Use 'openai-compat' com um modelo local para não enviar dado para fora.",
        )
        return

    try:
        provider = build_provider(settings)
    except Exception as exc:  # noqa: BLE001
        ctx.disable("llm", f"provedor de modelo indisponível: {exc}")
        return

    corr = ctx.settings.correlation

    async def analyze_incident_timeline(
        user_id: Annotated[str, Field(description="ID do usuário (device id) da sessão")],
        session_id: Annotated[str, Field(description="ID da sessão")],
        question: Annotated[
            str | None,
            Field(description="Pergunta específica; omita para análise de causa raiz"),
        ] = None,
        query: Annotated[str | None, Field(description="Filtro adicional na telemetria")] = None,
        correlation_mode: Annotated[
            CorrelationMode, Field(description="'time', 'identity' ou 'both'")
        ] = "both",
        sources: Annotated[list[str] | None, Field(description="Fontes a incluir")] = None,
    ) -> Any:
        """Analisa a timeline de uma sessão e devolve um veredito estruturado.

        Monta a timeline unificada internamente e submete ao modelo, devolvendo
        hipótese de causa, evidências citadas, confiança e próximo passo — em
        vez de centenas de linhas de eventos.

        Dados sensíveis são redigidos antes de o prompt sair da máquina.
        """
        window = await derive_session_window(
            ctx.clients.get("fullstory"),
            user_id,
            session_id,
            padding_seconds=corr.window_padding_seconds,
        )
        resolved = identity.resolve(
            correlation_mode, base_query=query, user_attr=corr.user_attr, uid=window.uid
        )
        entries, used, failed = await timeline.gather_entries(
            ctx.sources_for(sources), window, query=resolved.query, limit_per_source=100
        )

        if not entries:
            return {
                "analysis": None,
                "reason": "a timeline veio vazia — não há o que analisar",
                "sources_used": used,
                "sources_failed": failed or None,
            }

        rendered = timeline.render(entries, max_entries=settings.max_timeline_entries)
        truncated = len(entries) > settings.max_timeline_entries

        # A redação acontece aqui, antes de compor o prompt.
        payload = redact(
            {
                "janela": {
                    "inicio": window.start.isoformat(),
                    "fim": window.end.isoformat(),
                    "duracao_s": round(window.duration_seconds),
                },
                "modo_correlacao": resolved.mode,
                "ressalva": resolved.fallback_reason,
                "resumo": timeline.summarize(entries),
                "timeline": rendered,
            }
        )

        prompt = (
            f"{question or 'Qual a causa raiz mais provável do que aconteceu nesta sessão?'}\n\n"
            f"Timeline correlacionada"
            f"{' (truncada)' if truncated else ''}:\n"
            f"{json.dumps(payload, ensure_ascii=False, indent=2)}"
        )

        analysis = await provider.complete_structured(
            system=ANALYSIS_SYSTEM,
            prompt=prompt,
            schema=IncidentAnalysis,
            effort=settings.effort,
        )

        return {
            "analysis": analysis.model_dump(),
            "model": f"{provider.name}:{provider.model}",
            "timeline_entries_analyzed": len(rendered),
            "timeline_truncated": truncated,
            "correlation_mode": resolved.mode,
            "correlation_caveat": resolved.fallback_reason,
            "sources_used": used,
            "sources_failed": failed or None,
        }

    async def nl_to_datadog_query(
        question: Annotated[
            str, Field(description="A pergunta em português, ex 'erros de pagamento na última hora'")
        ],
        target: Annotated[
            str, Field(description="Onde a consulta será usada: logs, rum ou spans")
        ] = "logs",
    ) -> Any:
        """Traduz uma pergunta em português para a sintaxe de consulta do Datadog.

        **Não executa nada.** Devolve a consulta para revisão; quem chamou
        decide se a usa numa tool de busca. A separação é proposital: consulta
        gerada por modelo deve ser lida antes de rodar.
        """
        translation = await provider.complete_structured(
            system=QUERY_SYSTEM,
            prompt=f"Alvo da consulta: {target}\nPergunta: {redact(question)}",
            schema=QueryTranslation,
            effort="low",  # tradução é tarefa curta; esforço alto seria desperdício
        )
        return {
            **translation.model_dump(),
            "target": target,
            "model": f"{provider.name}:{provider.model}",
            "next_step": (
                f"Revise a consulta e passe para datadog_search_logs "
                f"(ou a tool correspondente a '{target}')."
            ),
        }

    for fn in (analyze_incident_timeline, nl_to_datadog_query):
        server.add_tool(fn, name=fn.__name__)
        ctx.registered_tools.append(fn.__name__)
