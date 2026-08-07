"""Tools da FullStory e a fonte de timeline correspondente.

As descrições são o que orienta o modelo na escolha da tool — vieram dos JSDoc
do fs-lexicon, ajustadas para dizer *quando* usar cada uma, não só o que faz.
"""

from __future__ import annotations

import logging
from typing import Annotated, Any

from pydantic import Field

from ...models import SessionWindow, TimelineEntry
from ...toolsets import FULLSTORY_CORE, FULLSTORY_WRITE
from ..registry import ServerContext
from .analytics import build_user_analytics, event_name, event_timestamp, process_session_events
from .client import FullStoryClient

logger = logging.getLogger(__name__)

# Eventos que indicam frustração ou falha — destacados na timeline porque são
# o que normalmente interessa quando se investiga um incidente.
NOTABLE_EVENTS = frozenset(
    {"exception", "crash", "console_message", "mouse_thrash", "form_abandon", "low_memory"}
)


class FullStorySessionProvider:
    """Implementa `SessionProvider`.

    Existe para que `correlation/` peça "o provedor de sessão" em vez de pedir
    "fullstory": a correlação depende do *conceito* de sessão, não deste
    produto. Se um dia outro provedor souber resolver sessões, ele entra aqui
    sem que a correlação mude.
    """

    source_name = "fullstory"

    def __init__(self, client: FullStoryClient) -> None:
        self._client = client

    async def session_events(self, user_id: str, session_id: str) -> list[dict[str, Any]]:
        payload = await self._client.get_session_events(user_id, session_id)
        return _extract_events(payload)

    async def sessions_for_user(self, uid: str, *, limit: int = 5) -> list[dict[str, Any]]:
        payload = await self._client.list_sessions(uid=uid, limit=limit)
        if isinstance(payload, list):
            return [s for s in payload if isinstance(s, dict)]
        if isinstance(payload, dict):
            for key in ("sessions", "data", "results"):
                value = payload.get(key)
                if isinstance(value, list):
                    return [s for s in value if isinstance(s, dict)]
        return []

    def session_link(self, user_id: str, session_id: str) -> str:
        return self._client.session_link(user_id, session_id)


class FullStoryTimelineSource:
    """Expõe os eventos de uma sessão como entradas de timeline."""

    source_name = "fullstory"

    def __init__(self, client: FullStoryClient) -> None:
        self._client = client

    async def events_in_window(
        self,
        window: SessionWindow,
        *,
        query: str | None = None,
        limit: int = 100,
    ) -> list[TimelineEntry]:
        if not window.uid or not window.session_id:
            # Sem sessão não há o que buscar aqui; a janela pode ter vindo de
            # um incidente. Devolver vazio em vez de erro é proposital.
            return []

        try:
            payload = await self._client.get_session_events(window.uid, window.session_id)
        except Exception as exc:  # noqa: BLE001 — timeline incompleta > timeline ausente
            logger.warning("fullstory: falha ao buscar eventos da sessão: %s", exc)
            return []

        events = _extract_events(payload)
        entries: list[TimelineEntry] = []
        for event in events:
            ts = event_timestamp(event)
            if ts is None or not (window.start <= ts <= window.end):
                continue
            name = event_name(event)
            entries.append(
                TimelineEntry(
                    ts=ts,
                    source=self.source_name,
                    kind=name,
                    summary=_summarize_event(event, name),
                    raw=event,
                )
            )
            if len(entries) >= limit:
                break
        return entries


def _extract_events(payload: Any) -> list[dict[str, Any]]:
    """A v2 já devolveu tanto lista crua quanto `{events: [...]}`; aceita os dois."""
    if isinstance(payload, list):
        return [e for e in payload if isinstance(e, dict)]
    if isinstance(payload, dict):
        for key in ("events", "data", "results"):
            value = payload.get(key)
            if isinstance(value, list):
                return [e for e in value if isinstance(e, dict)]
    return []


def _summarize_event(event: dict[str, Any], name: str) -> str:
    props = event.get("event_properties") or event.get("properties") or {}
    detail = ""
    if isinstance(props, dict):
        for key in ("event_name", "target_text", "text", "message", "url", "path"):
            value = props.get(key)
            if value:
                detail = f": {str(value)[:120]}"
                break
    marker = "⚠ " if name in NOTABLE_EVENTS else ""
    return f"{marker}{name}{detail}"


# --------------------------------------------------------------------- registro


def register(server: Any, ctx: ServerContext) -> None:
    settings = ctx.settings.fullstory
    if not settings.configured:
        ctx.disable(
            "fullstory",
            "FULLSTORY_API_KEY não configurada — tools e correlação de sessão indisponíveis",
        )
        return

    client = FullStoryClient(
        settings,
        timeout=ctx.settings.server.http_timeout_seconds,
        max_retries=ctx.settings.server.http_max_retries,
    )
    ctx.add_client("fullstory", client)
    ctx.add_timeline_source(FullStoryTimelineSource(client))
    ctx.set_session_provider(FullStorySessionProvider(client))

    if ctx.enabled(FULLSTORY_CORE):
        _register_core(server, client, ctx)
    if ctx.enabled(FULLSTORY_WRITE):
        _register_write(server, client, ctx)


def _add(server: Any, ctx: ServerContext, fn: Any, name: str) -> None:
    server.add_tool(fn, name=name)
    ctx.registered_tools.append(name)


# ------------------------------------------------------------------- leitura


def _register_core(server: Any, client: FullStoryClient, ctx: ServerContext) -> None:
    async def fullstory_get_profile(
        profile_id: Annotated[str, Field(description="ID do perfil de sessão")],
    ) -> Any:
        """Busca um perfil de sessão (visit profile) da FullStory pelo ID."""
        return await client.get_session_profile(profile_id)

    async def fullstory_list_session_profiles(
        query: Annotated[str | None, Field(description="Filtro por nome ou ID")] = None,
        limit: Annotated[int, Field(description="Máximo de perfis", ge=1, le=500)] = 100,
        offset: Annotated[int, Field(description="Deslocamento para paginação", ge=0)] = 0,
        sort: Annotated[str | None, Field(description="Ordenação, ex: created_time")] = None,
    ) -> Any:
        """Lista perfis de sessão. Use para descobrir quais perfis existem antes de aplicar um."""
        return await client.list_session_profiles(
            query=query, limit=limit, offset=offset, sort=sort
        )

    async def fullstory_get_session_events(
        user_id: Annotated[str, Field(description="ID do usuário (device id) da sessão")],
        session_id: Annotated[str, Field(description="ID da sessão")],
    ) -> Any:
        """Transcript completo de eventos de uma sessão, com timestamps.

        É a base de toda investigação de sessão: cliques, navegação, erros de
        UI e rage clicks em ordem cronológica.
        """
        return await client.get_session_events(user_id, session_id)

    async def fullstory_generate_session_summary(
        user_id: Annotated[str, Field(description="ID do usuário (device id)")],
        session_id: Annotated[str, Field(description="ID da sessão")],
        config_profile: Annotated[str | None, Field(description="Perfil de configuração")] = None,
    ) -> Any:
        """Resumo da sessão gerado pela FullStory. Mais barato que ler o transcript inteiro."""
        return await client.get_session_summary(user_id, session_id, config_profile)

    async def fullstory_get_session_insights(
        user_id: Annotated[str, Field(description="ID do usuário (device id)")],
        session_id: Annotated[str, Field(description="ID da sessão")],
        output_mode: Annotated[
            str, Field(description="'default' para tudo, 'summary' para só o agregado")
        ] = "default",
    ) -> Any:
        """Análise derivada da sessão: clusters de comportamento, fluxo, duração e pontos de abandono.

        Processado localmente a partir dos eventos — não é uma chamada de API
        adicional da FullStory além da busca de eventos.
        """
        payload = await client.get_session_events(user_id, session_id)
        processed = process_session_events(_extract_events(payload))
        if output_mode == "summary":
            clustering = processed["behavioralClustering"]
            return {
                "eventCount": processed["eventCount"],
                "uniqueEventTypes": processed["uniqueEventTypes"],
                "sessionDuration": clustering.get("sessionDuration", 0),
                "behavioralInsights": clustering.get("behavioralInsights", {}),
            }
        # `sortedEvents` sai do retorno: é grande e o chamador já tem a tool de eventos.
        return {k: v for k, v in processed.items() if k != "sortedEvents"}

    async def fullstory_list_sessions(
        uid: Annotated[str | None, Field(description="ID do usuário")] = None,
        email: Annotated[str | None, Field(description="E-mail do usuário")] = None,
        limit: Annotated[int, Field(description="Máximo de sessões", ge=1, le=100)] = 20,
    ) -> Any:
        """Lista as sessões de um usuário. Informe uid ou email."""
        return await client.list_sessions(uid=uid, email=email, limit=limit)

    async def fullstory_get_user(
        user_id: Annotated[str, Field(description="ID do usuário na FullStory")],
    ) -> Any:
        """Busca um usuário pelo ID interno da FullStory."""
        return await client.get_user(user_id)

    async def fullstory_get_user_events(
        uid: Annotated[str, Field(description="uid da aplicação (não o ID interno)")],
        limit: Annotated[int | None, Field(description="Máximo de eventos")] = None,
    ) -> Any:
        """Eventos de um usuário ao longo de várias sessões (API v1)."""
        return await client.get_user_events(uid, {"limit": limit} if limit else None)

    async def fullstory_get_user_pages(
        uid: Annotated[str, Field(description="uid da aplicação")],
        limit: Annotated[int | None, Field(description="Máximo de páginas")] = None,
    ) -> Any:
        """Páginas visitadas por um usuário (API v1)."""
        return await client.get_user_pages(uid, {"limit": limit} if limit else None)

    async def fullstory_get_user_profile(
        user_identifier: Annotated[str, Field(description="uid ou e-mail do usuário")],
    ) -> Any:
        """Perfil consolidado: dados do usuário mais suas sessões recentes.

        Se o identificador contiver ``@`` é tratado como e-mail. Caso contrário,
        tenta primeiro como uid e cai para e-mail caso não retorne nada — alguns
        sistemas usam identificadores que parecem e-mail.
        """
        if "@" in user_identifier:
            sessions = await client.list_sessions(email=user_identifier, limit=20)
        else:
            sessions = await client.list_sessions(uid=user_identifier, limit=20)
            if not sessions:
                sessions = await client.list_sessions(email=user_identifier, limit=20)
        return {"identifier": user_identifier, "sessions": sessions}

    async def fullstory_get_user_analytics(
        user_identifier: Annotated[str, Field(description="uid do usuário")],
        limit: Annotated[int, Field(description="Eventos a considerar", ge=1, le=1000)] = 500,
    ) -> Any:
        """Métricas derivadas do comportamento do usuário.

        Contagem de sessões, duração média, eventos mais frequentes, funil de
        conversão, score de engajamento e padrão de comportamento.
        """
        payload = await client.get_user_events(user_identifier, {"limit": limit})
        return build_user_analytics(_extract_events(payload))

    async def fullstory_get_batch_job_status(
        job_id: Annotated[str, Field(description="ID do job de batch")],
    ) -> Any:
        """Status de um job de importação em lote."""
        return await client.get_batch_job_status(job_id)

    async def fullstory_get_batch_job_errors(
        job_id: Annotated[str, Field(description="ID do job de batch")],
    ) -> Any:
        """Erros de um job de importação em lote."""
        return await client.get_batch_job_errors(job_id)

    async def fullstory_get_segment_export_status(
        export_id: Annotated[str, Field(description="ID do export")],
    ) -> Any:
        """Status de um export de segmento."""
        return await client.get_segment_export_status(export_id)

    async def fullstory_list_segments(
        limit: Annotated[int, Field(description="Máximo de segmentos", ge=1, le=200)] = 50,
    ) -> Any:
        """Lista os segmentos definidos na conta.

        Use antes de criar um export: normalmente o segmento que você quer já existe.
        """
        return await client.list_segments(limit=limit)

    async def fullstory_get_segment(
        segment_id: Annotated[str, Field(description="ID do segmento")],
    ) -> Any:
        """Detalhes de um segmento, incluindo a definição de filtros."""
        return await client.get_segment(segment_id)

    async def fullstory_get_recording_block_rules() -> Any:
        """Regras de bloqueio de gravação da conta. Útil para explicar dados ausentes."""
        return await client.get_recording_block_rules()

    async def fullstory_get_session_link(
        user_id: Annotated[str, Field(description="ID do usuário (device id)")],
        session_id: Annotated[str, Field(description="ID da sessão")],
    ) -> str:
        """URL do replay da sessão, para abrir no navegador.

        A FullStory não expõe API pública de screenshot; este link é o caminho
        para inspeção visual.
        """
        return client.session_link(user_id, session_id)

    async def fullstory_health_check() -> Any:
        """Verifica conectividade e credencial da FullStory."""
        return await client.health_check()

    for fn in (
        fullstory_get_profile,
        fullstory_list_session_profiles,
        fullstory_get_session_events,
        fullstory_generate_session_summary,
        fullstory_get_session_insights,
        fullstory_list_sessions,
        fullstory_get_user,
        fullstory_get_user_events,
        fullstory_get_user_pages,
        fullstory_get_user_profile,
        fullstory_get_user_analytics,
        fullstory_get_batch_job_status,
        fullstory_get_batch_job_errors,
        fullstory_get_segment_export_status,
        fullstory_list_segments,
        fullstory_get_segment,
        fullstory_get_recording_block_rules,
        fullstory_get_session_link,
        fullstory_health_check,
    ):
        _add(server, ctx, fn, fn.__name__)


# -------------------------------------------------------------------- escrita


def _register_write(server: Any, client: FullStoryClient, ctx: ServerContext) -> None:
    async def fullstory_create_profile(
        profile_id: Annotated[str, Field(description="ID do novo perfil")],
        name: Annotated[str | None, Field(description="Nome de exibição")] = None,
        config: Annotated[
            dict[str, Any] | None,
            Field(description="Configuração: slice, context, events, cache, llm"),
        ] = None,
    ) -> Any:
        """Cria um perfil de sessão."""
        payload: dict[str, Any] = {"profile_id": profile_id, **(config or {})}
        if name:
            payload["name"] = name
        return await client.create_session_profile(payload)

    async def fullstory_update_profile(
        profile_id: Annotated[str, Field(description="ID do perfil")],
        name: Annotated[str | None, Field(description="Novo nome de exibição")] = None,
        config: Annotated[dict[str, Any] | None, Field(description="Campos a atualizar")] = None,
    ) -> Any:
        """Atualiza um perfil de sessão existente."""
        payload: dict[str, Any] = dict(config or {})
        if name:
            payload["name"] = name
        return await client.update_session_profile(profile_id, payload)

    async def fullstory_delete_profile(
        profile_id: Annotated[str, Field(description="ID do perfil a remover")],
    ) -> Any:
        """Remove um perfil de sessão. Ação destrutiva."""
        return await client.delete_session_profile(profile_id)

    async def fullstory_generate_session_context(
        user_id: Annotated[str, Field(description="ID do usuário (device id)")],
        session_id: Annotated[str, Field(description="ID da sessão")],
        options: Annotated[dict[str, Any] | None, Field(description="Opções de geração")] = None,
    ) -> Any:
        """Gera a representação contextual de uma sessão. Consome quota da FullStory."""
        return await client.generate_session_context(user_id, session_id, options)

    async def fullstory_create_user(
        uid: Annotated[str, Field(description="uid da aplicação")],
        display_name: Annotated[str | None, Field(description="Nome de exibição")] = None,
        email: Annotated[str | None, Field(description="E-mail")] = None,
        properties: Annotated[dict[str, Any] | None, Field(description="Propriedades")] = None,
    ) -> Any:
        """Cria ou atualiza um usuário (API v2)."""
        payload: dict[str, Any] = {"uid": uid}
        if display_name:
            payload["display_name"] = display_name
        if email:
            payload["email"] = email
        if properties:
            payload["properties"] = properties
        return await client.create_user(payload)

    async def fullstory_update_user(
        user_id: Annotated[str, Field(description="ID interno do usuário")],
        updates: Annotated[dict[str, Any], Field(description="Campos a atualizar")],
    ) -> Any:
        """Atualiza um usuário existente."""
        return await client.update_user(user_id, updates)

    async def fullstory_delete_user(
        user_id: Annotated[str, Field(description="ID interno do usuário")],
    ) -> Any:
        """Remove um usuário. Ação destrutiva e relevante para LGPD."""
        return await client.delete_user(user_id)

    async def fullstory_create_users_batch(
        users: Annotated[list[dict[str, Any]], Field(description="Lista de usuários")],
    ) -> Any:
        """Cria ou atualiza usuários em lote."""
        return await client.create_users_batch(users)

    async def fullstory_set_user_properties_v1(
        uid: Annotated[str, Field(description="uid da aplicação")],
        properties: Annotated[dict[str, Any], Field(description="Propriedades customizadas")],
    ) -> Any:
        """Define propriedades customizadas de um usuário (API v1 legada)."""
        return await client.set_user_properties_v1(uid, properties)

    async def fullstory_set_user_events_v1(
        uid: Annotated[str, Field(description="uid da aplicação")],
        events: Annotated[dict[str, Any], Field(description="Payload de evento customizado")],
    ) -> Any:
        """Envia um evento customizado para um usuário (API v1 legada)."""
        return await client.set_user_events_v1(uid, events)

    async def fullstory_create_event(
        name: Annotated[str, Field(description="Nome do evento")],
        uid: Annotated[str | None, Field(description="uid do usuário")] = None,
        session_id: Annotated[str | None, Field(description="ID da sessão")] = None,
        properties: Annotated[dict[str, Any] | None, Field(description="Propriedades")] = None,
    ) -> Any:
        """Cria um evento customizado (API v2)."""
        payload: dict[str, Any] = {"name": name}
        if uid:
            payload["user"] = {"uid": uid}
        if session_id:
            payload["session"] = {"id": session_id}
        if properties:
            payload["properties"] = properties
        return await client.create_event(payload)

    async def fullstory_create_events_batch(
        events: Annotated[list[dict[str, Any]], Field(description="Lista de eventos")],
    ) -> Any:
        """Cria eventos em lote."""
        return await client.create_events_batch(events)

    async def fullstory_create_annotation(
        text: Annotated[str, Field(description="Texto da anotação")],
        timestamp: Annotated[str | None, Field(description="Momento em ISO8601")] = None,
        properties: Annotated[dict[str, Any] | None, Field(description="Propriedades")] = None,
    ) -> Any:
        """Cria uma anotação na linha do tempo da conta (ex: marcar um deploy)."""
        payload: dict[str, Any] = {"text": text}
        if timestamp:
            payload["timestamp"] = timestamp
        if properties:
            payload["properties"] = properties
        return await client.create_annotation(payload)

    async def fullstory_create_segment_export(
        segment_id: Annotated[str, Field(description="ID do segmento a exportar")],
        export_type: Annotated[
            str, Field(description="Tipo do export, ex: TYPE_EVENT")
        ] = "TYPE_EVENT",
        options: Annotated[dict[str, Any] | None, Field(description="Opções adicionais")] = None,
    ) -> Any:
        """Inicia um export de segmento. Assíncrono — acompanhe com get_segment_export_status."""
        payload: dict[str, Any] = {"segmentId": segment_id, "type": export_type, **(options or {})}
        return await client.create_segment_export(payload)

    for fn in (
        fullstory_create_profile,
        fullstory_update_profile,
        fullstory_delete_profile,
        fullstory_generate_session_context,
        fullstory_create_user,
        fullstory_update_user,
        fullstory_delete_user,
        fullstory_create_users_batch,
        fullstory_set_user_properties_v1,
        fullstory_set_user_events_v1,
        fullstory_create_event,
        fullstory_create_events_batch,
        fullstory_create_annotation,
        fullstory_create_segment_export,
    ):
        _add(server, ctx, fn, fn.__name__)
