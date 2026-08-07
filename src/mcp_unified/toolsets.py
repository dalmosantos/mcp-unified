"""Grupos de tools e perfis de consumo.

72 tools num só servidor consomem contexto demais numa IDE. Os grupos existem
para que cada consumidor carregue só o recorte de que precisa: a IDE trabalha
com 29, o agente de SRE com 48.
"""

from __future__ import annotations

from dataclasses import dataclass

FULLSTORY_CORE = "fullstory-core"
FULLSTORY_WRITE = "fullstory-write"
DATADOG_CORE = "datadog-core"
DATADOG_RUM = "datadog-rum"
DATADOG_APM = "datadog-apm"
DATADOG_PA = "datadog-product-analytics"
SERVICENOW = "servicenow"
MSGRAPH = "msgraph"
CORRELATION = "correlation"
LLM = "llm"


@dataclass(frozen=True)
class ToolsetInfo:
    name: str
    provider: str
    read_only: bool
    description: str


ALL_TOOLSETS: dict[str, ToolsetInfo] = {
    FULLSTORY_CORE: ToolsetInfo(
        FULLSTORY_CORE, "fullstory", True, "Leitura: sessões, users, analytics, segments"
    ),
    FULLSTORY_WRITE: ToolsetInfo(
        FULLSTORY_WRITE, "fullstory", False, "Escrita: create/update/delete, batch, exports"
    ),
    DATADOG_CORE: ToolsetInfo(
        DATADOG_CORE, "datadog", True, "Monitors, dashboards, metrics, events, incidents, logs"
    ),
    DATADOG_RUM: ToolsetInfo(
        DATADOG_RUM, "datadog", False, "RUM e Error Tracking (inclui atualização de issue)"
    ),
    DATADOG_APM: ToolsetInfo(DATADOG_APM, "datadog", True, "Spans / APM"),
    DATADOG_PA: ToolsetInfo(DATADOG_PA, "datadog", True, "Product Analytics"),
    SERVICENOW: ToolsetInfo(
        SERVICENOW, "servicenow", True, "Incidents, changes, problems, knowledge (read-only)"
    ),
    MSGRAPH: ToolsetInfo(
        MSGRAPH, "msgraph", True, "SharePoint e mensagens do Teams (read-only)"
    ),
    CORRELATION: ToolsetInfo(
        CORRELATION, "correlation", True, "Correlação entre sessão e telemetria"
    ),
    LLM: ToolsetInfo(LLM, "llm", True, "Análise e tradução de query via modelo"),
}


PROFILES: dict[str, list[str]] = {
    # Investigação interativa: superfície enxuta, contexto é escasso.
    "ide": [FULLSTORY_CORE, DATADOG_CORE, CORRELATION],
    # O agente precisa das fontes históricas e do impacto ao vivo.
    "sre-agent": [
        DATADOG_CORE,
        DATADOG_RUM,
        SERVICENOW,
        MSGRAPH,
        FULLSTORY_CORE,
        CORRELATION,
    ],
    "all": list(ALL_TOOLSETS),
}


class ToolsetResolutionError(ValueError):
    """Perfil ou toolset desconhecido."""


def resolve(
    *,
    profile: str | None = None,
    toolsets: str | list[str] | None = None,
    safe_mode: bool = False,
) -> set[str]:
    """Resolve a seleção final de toolsets.

    `toolsets` explícito tem precedência sobre `profile`. Com `safe_mode`,
    os grupos de escrita são removidos independente da seleção.
    """
    if toolsets:
        names = (
            [t.strip() for t in toolsets.split(",") if t.strip()]
            if isinstance(toolsets, str)
            else list(toolsets)
        )
        if names == ["all"]:
            selected = set(ALL_TOOLSETS)
        else:
            unknown = sorted(set(names) - set(ALL_TOOLSETS))
            if unknown:
                raise ToolsetResolutionError(
                    f"toolset desconhecido: {', '.join(unknown)}. "
                    f"Disponíveis: {', '.join(sorted(ALL_TOOLSETS))}"
                )
            selected = set(names)
    else:
        key = profile or "ide"
        if key not in PROFILES:
            raise ToolsetResolutionError(
                f"perfil desconhecido: {key}. Disponíveis: {', '.join(PROFILES)}"
            )
        selected = set(PROFILES[key])

    if safe_mode:
        selected = {n for n in selected if ALL_TOOLSETS[n].read_only}

    return selected
