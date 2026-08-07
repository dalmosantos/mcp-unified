"""Resolução de perfis/toolsets e as tools de análise via modelo."""

from __future__ import annotations

import pytest
import respx
from mcp import Client

from mcp_unified.llm.schemas import IncidentAnalysis, QueryTranslation
from mcp_unified.server import build_server, get_context
from mcp_unified.toolsets import (
    ALL_TOOLSETS,
    CORRELATION,
    FULLSTORY_WRITE,
    PROFILES,
    ToolsetResolutionError,
    resolve,
)

from .conftest import FakeLLMProvider, mock_pix_incident_stack

# ------------------------------------------------------------- toolsets


def test_perfil_ide_e_enxuto():
    selected = resolve(profile="ide")
    assert selected == {"fullstory-core", "datadog-core", CORRELATION}
    assert FULLSTORY_WRITE not in selected


def test_perfil_sre_agent_inclui_fontes_historicas():
    selected = resolve(profile="sre-agent")
    assert {"servicenow", "msgraph", "datadog-rum"} <= selected


def test_toolsets_explicito_tem_precedencia_sobre_perfil():
    selected = resolve(profile="all", toolsets="correlation")
    assert selected == {CORRELATION}


def test_all_expande_para_tudo():
    assert resolve(toolsets="all") == set(ALL_TOOLSETS)


def test_safe_mode_remove_grupos_de_escrita():
    selected = resolve(profile="all", safe_mode=True)
    assert FULLSTORY_WRITE not in selected
    assert "datadog-rum" not in selected  # contém update de issue
    assert CORRELATION in selected


def test_toolset_desconhecido_lista_as_opcoes():
    with pytest.raises(ToolsetResolutionError) as exc:
        resolve(toolsets="nao-existe")
    assert "nao-existe" in str(exc.value)
    assert "fullstory-core" in str(exc.value)


def test_perfil_desconhecido_lista_as_opcoes():
    with pytest.raises(ToolsetResolutionError) as exc:
        resolve(profile="nao-existe")
    assert set(PROFILES) <= {p.strip() for p in str(exc.value).split(":")[-1].split(",")}


async def test_contagem_de_tools_por_perfil(fake_env, monkeypatch):
    """Fixa os números que o README promete."""
    for key, value in {
        "SNOW_INSTANCE": "dev",
        "SNOW_USERNAME": "u",
        "SNOW_PASSWORD": "p",
        "MSGRAPH_TENANT_ID": "t",
        "MSGRAPH_CLIENT_ID": "c",
        "MSGRAPH_CLIENT_SECRET": "s",
        "MCP_LLM_PROVIDER": "openai-compat",
        "MCP_LLM_BASE_URL": "http://fake/v1",
    }.items():
        monkeypatch.setenv(key, value)

    contagens = {}
    for perfil in ("ide", "sre-agent", "all"):
        async with Client(build_server(profile=perfil)) as client:
            contagens[perfil] = len((await client.list_tools()).tools)

    assert contagens == {"ide": 32, "sre-agent": 51, "all": 73}


async def test_provedor_ausente_nao_registra_suas_tools(fake_env):
    """ServiceNow no perfil, mas sem credencial: as tools não aparecem."""
    server = build_server(toolsets="servicenow,correlation")
    ctx = get_context(server)

    assert "servicenow" in ctx.disabled
    async with Client(server) as client:
        names = {t.name for t in (await client.list_tools()).tools}
    assert not any(n.startswith("servicenow_") for n in names)


# ------------------------------------------------------------------ LLM


@respx.mock
async def test_analise_devolve_veredito_estruturado(fake_env, monkeypatch):
    mock_pix_incident_stack(respx.mock)
    monkeypatch.setenv("MCP_LLM_PROVIDER", "openai-compat")
    monkeypatch.setenv("MCP_LLM_BASE_URL", "http://fake/v1")

    fake = FakeLLMProvider(
        IncidentAnalysis(
            root_cause_hypothesis="Timeout no SPI durante a confirmação",
            confidence="alta",
            evidence=[],
            blast_radius="1 sessão observada",
            recommended_next_step="Verificar disponibilidade do parceiro",
        )
    )
    monkeypatch.setattr("mcp_unified.llm.tools.build_provider", lambda _s: fake)

    server = build_server(toolsets="correlation,llm,fullstory-core,datadog-core,datadog-apm")
    async with Client(server) as client:
        result = await client.call_tool(
            "analyze_incident_timeline", {"user_id": "dev-77", "session_id": "sess-pix-1"}
        )
    out = (result.structured_content or {}).get("result", {})

    assert out["analysis"]["confidence"] == "alta"
    assert "SPI" in out["analysis"]["root_cause_hypothesis"]
    assert out["timeline_entries_analyzed"] > 0
    assert out["model"] == "fake:fake-model"

    # A timeline chegou ao prompt, não só o pedido.
    assert "timeline" in (fake.last_prompt or "")


@respx.mock
async def test_analise_de_timeline_vazia_explica_em_vez_de_chamar_o_modelo(fake_env, monkeypatch):
    """Sem entradas não há o que analisar — e gastar token seria desperdício."""
    respx.mock.get(url__regex=r".*/v2/sessions/.+/events.*").mock(
        return_value=respx.MockResponse(
            200, json={"events": [{"event_type": "click", "event_time": "2026-08-07T14:00:00Z"}]}
        )
    )
    respx.mock.post(url__regex=r".*/api/v2/logs/events/search").mock(
        return_value=respx.MockResponse(200, json={"data": []})
    )
    respx.mock.get(url__regex=r".*/api/v1/events.*").mock(
        return_value=respx.MockResponse(200, json={"events": []})
    )
    monkeypatch.setenv("MCP_LLM_PROVIDER", "openai-compat")
    monkeypatch.setenv("MCP_LLM_BASE_URL", "http://fake/v1")

    fake = FakeLLMProvider()
    monkeypatch.setattr("mcp_unified.llm.tools.build_provider", lambda _s: fake)

    server = build_server(toolsets="correlation,llm,fullstory-core,datadog-core")
    async with Client(server) as client:
        result = await client.call_tool(
            "analyze_incident_timeline",
            {"user_id": "d", "session_id": "s", "sources": ["datadog-logs"]},
        )
    out = (result.structured_content or {}).get("result", {})

    assert out["analysis"] is None
    assert "vazia" in out["reason"]
    assert fake.calls == 0, "não deveria ter chamado o modelo"


async def test_traducao_de_query_nao_executa_nada(fake_env, monkeypatch):
    """A separação é proposital: consulta gerada por modelo se lê antes de rodar."""
    monkeypatch.setenv("MCP_LLM_PROVIDER", "openai-compat")
    monkeypatch.setenv("MCP_LLM_BASE_URL", "http://fake/v1")

    fake = FakeLLMProvider(
        QueryTranslation(
            query="service:servico-transferencia status:error",
            explanation="Erros no serviço de transferência",
            confidence="alta",
            caveats=["assumi que o serviço se chama servico-transferencia"],
        )
    )
    monkeypatch.setattr("mcp_unified.llm.tools.build_provider", lambda _s: fake)

    server = build_server(toolsets="llm,datadog-core")
    async with Client(server) as client:
        result = await client.call_tool(
            "nl_to_datadog_query", {"question": "erros de transferência na última hora"}
        )
    out = (result.structured_content or {}).get("result", {})

    assert out["query"] == "service:servico-transferencia status:error"
    assert out["caveats"], "suposições precisam ser declaradas"
    assert "Revise" in out["next_step"]


async def test_llm_sem_provedor_e_desabilitado_com_orientacao(fake_env):
    server = build_server(toolsets="llm,correlation")
    ctx = get_context(server)

    assert "llm" in ctx.disabled
    assert "openai-compat" in ctx.disabled["llm"], "a mensagem deve sugerir o modo local"

    async with Client(server) as client:
        names = {t.name for t in (await client.list_tools()).tools}
    assert "analyze_incident_timeline" not in names


# ------------------------------------------- o protocolo não vaza nome de provedor


def test_correlacao_nao_nomeia_nenhum_provedor():
    """Guarda-corpo do invariante declarado no AGENTS.md.

    A correlação depende do *conceito* de sessão, não do produto que a fornece.
    No dia em que alguém escrever `clients.get("fullstory")` dentro de
    `correlation/`, este teste falha — e a conversa acontece no code review, não
    seis meses depois quando plugar outra fonte virar refatoração.
    """
    import re
    from pathlib import Path

    correlation = Path(__file__).parent.parent / "src" / "mcp_unified" / "correlation"
    nomes = re.compile(r'"(fullstory|datadog|servicenow|msgraph)"')

    ofensores = {
        arquivo.name: sorted(set(nomes.findall(arquivo.read_text(encoding="utf-8"))))
        for arquivo in correlation.glob("*.py")
        if nomes.search(arquivo.read_text(encoding="utf-8"))
    }
    assert not ofensores, (
        f"correlation/ referencia provedor pelo nome: {ofensores}. "
        "Use os protocolos de protocols.py."
    )


async def test_provedor_de_sessao_e_registrado_pelo_protocolo(fake_env):
    from mcp_unified.protocols import SessionProvider

    ctx = get_context(build_server(profile="ide"))
    assert ctx.session_provider is not None
    assert isinstance(ctx.session_provider, SessionProvider)
    assert ctx.session_provider.source_name == "fullstory"


async def test_sem_provedor_de_sessao_a_correlacao_orienta(monkeypatch):
    """Sem FullStory, derivar janela de sessão é impossível — e a mensagem diz isso."""
    for key in ("FULLSTORY_API_KEY", "DD_API_KEY", "DD_APP_KEY"):
        monkeypatch.delenv(key, raising=False)

    ctx = get_context(build_server(profile="ide"))
    assert ctx.session_provider is None

    from mcp_unified.correlation.window import derive_session_window
    from mcp_unified.errors import CorrelationError

    with pytest.raises(CorrelationError) as exc:
        await derive_session_window(None, "u", "s")
    assert "FULLSTORY_API_KEY" in str(exc.value)
