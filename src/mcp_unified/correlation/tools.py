"""As três tools de correlação — o núcleo do projeto.

Nenhuma delas conhece provedor pelo nome: todas iteram sobre o que estiver
registrado no `ServerContext` implementando os protocolos.
"""

from __future__ import annotations

from typing import Annotated, Any

from pydantic import Field

from ..errors import CorrelationError
from ..models import CorrelationMode
from ..providers.registry import ServerContext
from ..toolsets import CORRELATION
from . import identity, timeline
from .window import derive_session_window, parse_window


def register(server: Any, ctx: ServerContext) -> None:
    if not ctx.enabled(CORRELATION):
        return

    corr = ctx.settings.correlation

    async def _window_for(
        user_id: str, session_id: str, padding_seconds: int | None
    ) -> Any:
        return await derive_session_window(
            ctx.clients.get("fullstory"),
            user_id,
            session_id,
            padding_seconds=(
                corr.window_padding_seconds if padding_seconds is None else padding_seconds
            ),
        )

    def _envelope(window: Any, requested: str, resolved: Any) -> dict[str, Any]:
        return {
            "window": {
                "start": window.start.isoformat(),
                "end": window.end.isoformat(),
                "duration_seconds": round(window.duration_seconds),
                "uid": window.uid,
                "session_id": window.session_id,
            },
            "requested_mode": requested,
            "effective_mode": resolved.mode,
            "fallback_reason": resolved.fallback_reason,
            "providers_unavailable": dict(ctx.disabled) or None,
        }

    # ------------------------------------------------------------------ 1

    async def correlate_session_with_logs(
        user_id: Annotated[str, Field(description="ID do usuário (device id) da sessão")],
        session_id: Annotated[str, Field(description="ID da sessão")],
        query: Annotated[
            str | None, Field(description="Filtro adicional, ex 'service:pagamentos'")
        ] = None,
        correlation_mode: Annotated[
            CorrelationMode,
            Field(description="'time', 'identity' ou 'both' (padrão)"),
        ] = "both",
        padding_seconds: Annotated[
            int | None, Field(description="Segundos de folga antes e depois da sessão")
        ] = None,
        limit: Annotated[int, Field(description="Máximo de logs", ge=1, le=1000)] = 100,
    ) -> Any:
        """Busca os logs de backend registrados durante uma sessão de usuário.

        Deriva a janela temporal a partir dos eventos da sessão e consulta os
        logs nesse intervalo. Responde: "o que o backend registrou enquanto
        esse usuário estava na tela?"
        """
        window = await _window_for(user_id, session_id, padding_seconds)
        resolved = identity.resolve(
            correlation_mode,
            base_query=query,
            user_attr=corr.user_attr,
            uid=window.uid,
        )

        log_sources = [
            s for s in ctx.timeline_sources if s.source_name.endswith("-logs")
        ]
        if not log_sources:
            raise CorrelationError(
                "Nenhuma fonte de logs registrada — configure o Datadog "
                "(DD_API_KEY + DD_APP_KEY) para usar esta tool."
            )

        entries, used, failed = await timeline.gather_entries(
            log_sources, window, query=resolved.query, limit_per_source=limit
        )

        if not entries and correlation_mode == "both" and resolved.filtered_by_identity:
            resolved = identity.downgrade_to_time(resolved, query, requested=correlation_mode)
            entries, used, failed = await timeline.gather_entries(
                log_sources, window, query=resolved.query, limit_per_source=limit
            )

        return {
            **_envelope(window, correlation_mode, resolved),
            "query_used": resolved.query,
            "sources_used": used,
            "sources_failed": failed or None,
            "summary": timeline.summarize(entries),
            "logs": timeline.render(entries, max_entries=limit),
        }

    # ------------------------------------------------------------------ 2

    async def build_unified_timeline(
        user_id: Annotated[str, Field(description="ID do usuário (device id) da sessão")],
        session_id: Annotated[str, Field(description="ID da sessão")],
        query: Annotated[str | None, Field(description="Filtro adicional")] = None,
        correlation_mode: Annotated[
            CorrelationMode, Field(description="'time', 'identity' ou 'both'")
        ] = "both",
        sources: Annotated[
            list[str] | None,
            Field(description="Fontes a incluir; omita para todas as disponíveis"),
        ] = None,
        padding_seconds: Annotated[int | None, Field(description="Folga na janela")] = None,
        limit_per_source: Annotated[
            int, Field(description="Máximo por fonte", ge=1, le=500)
        ] = 100,
    ) -> Any:
        """Conta a história completa do que aconteceu, intercalando todas as fontes.

        Funde numa única linha do tempo ordenada: eventos da sessão do usuário,
        logs, RUM, spans de APM, deploys e mudanças aprovadas — cada entrada
        marcada com sua origem.

        É a tool a usar quando a pergunta é "o que aconteceu?" em vez de
        "quanto foi?". Use `sources` para restringir se a timeline vier grande.
        """
        window = await _window_for(user_id, session_id, padding_seconds)
        resolved = identity.resolve(
            correlation_mode,
            base_query=query,
            user_attr=corr.user_attr,
            uid=window.uid,
        )

        selected = ctx.sources_for(sources)
        if not selected:
            available = ctx.timeline_source_names()
            raise CorrelationError(
                f"Nenhuma fonte disponível para {sources or 'a seleção padrão'}. "
                f"Registradas: {', '.join(available) if available else 'nenhuma'}."
            )

        entries, used, failed = await timeline.gather_entries(
            selected, window, query=resolved.query, limit_per_source=limit_per_source
        )

        return {
            **_envelope(window, correlation_mode, resolved),
            "query_used": resolved.query,
            "sources_available": ctx.timeline_source_names(),
            "sources_used": used,
            "sources_failed": failed or None,
            "summary": timeline.summarize(entries),
            "timeline": timeline.render(entries),
        }

    # ------------------------------------------------------------------ 3

    async def find_sessions_for_incident(
        query: Annotated[
            str, Field(description="Filtro do incidente, ex 'service:pagamentos status:error'")
        ],
        from_: Annotated[str, Field(description="Início da janela, ISO8601 ou epoch")],
        to: Annotated[str | None, Field(description="Fim da janela; padrão agora")] = None,
        max_users: Annotated[
            int, Field(description="Máximo de usuários a resolver", ge=1, le=50)
        ] = 10,
        sessions_per_user: Annotated[
            int, Field(description="Sessões por usuário", ge=1, le=20)
        ] = 5,
    ) -> Any:
        """Descobre quais usuários reais foram afetados por um incidente.

        Direção inversa das outras duas: parte de uma janela e de um filtro de
        incidente, agrega as identidades afetadas e busca as sessões de cada
        uma — devolvendo o link do replay.

        Exige que os logs carreguem o atributo de identidade configurado em
        `FS_DD_USER_ATTR`. Sem ele não há como saber quem foi afetado.
        """
        window = parse_window(from_, to)

        if not ctx.subject_resolvers:
            raise CorrelationError(
                "Nenhum resolvedor de identidade registrado — configure o Datadog "
                "(DD_API_KEY + DD_APP_KEY) para usar esta tool."
            )

        subjects: list[Any] = []
        resolver_errors: dict[str, str] = {}
        for resolver in ctx.subject_resolvers:
            try:
                subjects.extend(
                    await resolver.subjects_in_window(
                        window, query=query, max_subjects=max_users
                    )
                )
            except Exception as exc:  # noqa: BLE001
                resolver_errors[resolver.source_name] = str(exc)

        # Consolida por id, somando ocorrências entre resolvedores.
        merged: dict[str, Any] = {}
        for subject in subjects:
            existing = merged.get(subject.id)
            if existing:
                existing.occurrences += subject.occurrences
            else:
                merged[subject.id] = subject.model_copy()
        ranked = sorted(merged.values(), key=lambda s: s.occurrences, reverse=True)[:max_users]

        if not ranked:
            return {
                "window": {"start": window.start.isoformat(), "end": window.end.isoformat()},
                "query": query,
                "user_attribute": corr.user_attr,
                "affected_users": [],
                "sessions": [],
                "resolver_errors": resolver_errors or None,
                "hint": (
                    f"Nenhuma identidade encontrada. Verifique se os logs carregam o "
                    f"atributo '{corr.user_attr}'; se o seu app usa outro nome, ajuste "
                    f"FS_DD_USER_ATTR."
                ),
            }

        fullstory = ctx.clients.get("fullstory")
        sessions: list[dict[str, Any]] = []
        session_errors: dict[str, str] = {}

        if fullstory is not None:
            for subject in ranked:
                try:
                    found = await fullstory.list_sessions(
                        uid=subject.id, limit=sessions_per_user
                    )
                except Exception as exc:  # noqa: BLE001
                    session_errors[subject.id] = str(exc)
                    continue
                for session in _as_sessions(found):
                    session_id = session.get("sessionId") or session.get("session_id")
                    device_id = (
                        session.get("userId")
                        or session.get("user_id")
                        or session.get("deviceId")
                        or subject.id
                    )
                    sessions.append(
                        {
                            "uid": subject.id,
                            "occurrences_in_logs": subject.occurrences,
                            "session_id": session_id,
                            "device_id": device_id,
                            "replay_url": (
                                fullstory.session_link(str(device_id), str(session_id))
                                if session_id
                                else None
                            ),
                            "raw": session,
                        }
                    )

        return {
            "window": {"start": window.start.isoformat(), "end": window.end.isoformat()},
            "query": query,
            "user_attribute": corr.user_attr,
            "affected_users": [
                {"uid": s.id, "occurrences": s.occurrences, "source": s.source} for s in ranked
            ],
            "sessions": sessions,
            "resolver_errors": resolver_errors or None,
            "session_errors": session_errors or None,
            "note": (
                None
                if fullstory is not None
                else "FullStory não configurada — identidades resolvidas, mas sem sessões nem replay."
            ),
        }

    for fn in (
        correlate_session_with_logs,
        build_unified_timeline,
        find_sessions_for_incident,
    ):
        server.add_tool(fn, name=fn.__name__)
        ctx.registered_tools.append(fn.__name__)


def _as_sessions(payload: Any) -> list[dict[str, Any]]:
    """A v1 de sessões já devolveu lista crua e envelope; aceita os dois."""
    if isinstance(payload, list):
        return [s for s in payload if isinstance(s, dict)]
    if isinstance(payload, dict):
        for key in ("sessions", "data", "results"):
            value = payload.get(key)
            if isinstance(value, list):
                return [s for s in value if isinstance(s, dict)]
    return []
