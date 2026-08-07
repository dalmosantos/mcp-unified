"""Montagem do servidor MCP.

A ordem importa: os provedores registram tools **e** se registram como fontes
de correlação; a correlação é montada depois, para já enxergar todas as fontes
disponíveis.
"""

from __future__ import annotations

import logging
import sys

from mcp.server import MCPServer

from . import __version__
from .config import Settings
from .correlation import tools as correlation_tools
from .llm import tools as llm_tools
from .providers import datadog, fullstory, msgraph, servicenow
from .providers.registry import ServerContext
from .security.middleware import SecurityMiddleware
from .security.oauth import build_auth
from .toolsets import resolve

logger = logging.getLogger(__name__)

INSTRUCTIONS = """\
Servidor unificado de dados operacionais. Combina o comportamento do usuário no \
frontend (FullStory) com a telemetria de backend (Datadog), o histórico de \
chamados (ServiceNow) e a documentação (SharePoint/Teams).

Para investigar um incidente, prefira as tools de correlação: elas derivam a \
janela temporal da sessão e cruzam as fontes automaticamente, em vez de exigir \
que você copie timestamps de uma tool para outra.

- `build_unified_timeline` responde "o que aconteceu?"
- `correlate_session_with_logs` responde "o que o backend registrou durante a sessão?"
- `find_sessions_for_incident` responde "quem foi afetado?"

Provedores sem credencial são omitidos silenciosamente; o campo \
`providers_unavailable` nas respostas de correlação diz o que faltou.
"""


def configure_logging(level: str = "INFO") -> None:
    """Log sempre em stderr.

    Isto não é preferência: no transporte stdio, o stdout é o canal do
    protocolo. Um `print()` para stdout corrompe a sessão MCP.
    """
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        stream=sys.stderr,
        force=True,
    )


def build_server(
    *,
    profile: str | None = None,
    toolsets: str | list[str] | None = None,
    safe_mode: bool | None = None,
    settings: Settings | None = None,
) -> MCPServer:
    """Constrói o servidor com os toolsets resolvidos."""
    settings = settings or Settings()

    effective_safe_mode = (
        settings.security.safe_mode if safe_mode is None else safe_mode
    )
    enabled = resolve(
        profile=profile or settings.server.profile,
        toolsets=toolsets if toolsets is not None else settings.server.toolsets,
        safe_mode=effective_safe_mode,
    )

    auth_settings, token_verifier = build_auth(settings.security)

    server = MCPServer(
        name="mcp-unified",
        version=__version__,
        instructions=INSTRUCTIONS,
        middleware=[SecurityMiddleware(settings.security)],
        auth=auth_settings,
        token_verifier=token_verifier,
    )

    ctx = ServerContext(settings=settings, enabled_toolsets=enabled)

    # Provedores primeiro: registram tools e se anunciam como fontes.
    for module in (fullstory, datadog, servicenow, msgraph):
        try:
            module.register(server, ctx)
        except Exception as exc:  # noqa: BLE001 — um provedor ruim não derruba o resto
            name = module.__name__.rsplit(".", 1)[-1]
            logger.exception("falha ao registrar o provedor %s", name)
            ctx.disable(name, f"erro no registro: {exc}")

    # Correlação e LLM depois, já enxergando todas as fontes registradas.
    correlation_tools.register(server, ctx)
    llm_tools.register(server, ctx)

    server.__dict__["_mcp_unified_context"] = ctx

    logger.info(
        "%d tools registradas | toolsets: %s | fontes de timeline: %s",
        len(ctx.registered_tools),
        ", ".join(sorted(enabled)) or "nenhum",
        ", ".join(ctx.timeline_source_names()) or "nenhuma",
    )
    for provider, reason in ctx.disabled.items():
        logger.info("indisponível — %s: %s", provider, reason)

    return server


def get_context(server: MCPServer) -> ServerContext:
    """Recupera o contexto anexado ao servidor (usado em testes e no --list-tools)."""
    return server.__dict__["_mcp_unified_context"]
