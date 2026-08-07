"""Cobertura técnica dos clientes e da análise portada.

A análise client-side do FullStory é a parte mais arriscada do port — ~660
linhas de heurística que o original não testava. Estes testes fixam o
comportamento para que uma mudança futura não o altere em silêncio.
"""

from __future__ import annotations

import base64

import httpx
import pytest
import respx

from mcp_unified.config import DatadogSettings, FullStorySettings, ServiceNowSettings
from mcp_unified.errors import AuthorizationError, ProviderError, RateLimitError
from mcp_unified.providers.datadog.client import DatadogClient
from mcp_unified.providers.fullstory import analytics
from mcp_unified.providers.fullstory.client import FullStoryClient

from .conftest import load

# ------------------------------------------------------------ autenticação


def test_fullstory_normaliza_chave_crua_para_basic():
    client = FullStoryClient(FullStorySettings(FULLSTORY_API_KEY="minha-chave-crua"))
    header = client._client.headers["Authorization"]
    assert header.startswith("Basic ")
    assert base64.b64decode(header[6:]).decode() == "minha-chave-crua"


def test_fullstory_aceita_chave_ja_prefixada():
    encoded = base64.b64encode(b"abc").decode()
    client = FullStoryClient(FullStorySettings(FULLSTORY_API_KEY=f"Basic {encoded}"))
    assert client._client.headers["Authorization"] == f"Basic {encoded}"


def test_fullstory_datacenter_eu_muda_a_base_url():
    eu = FullStorySettings(FULLSTORY_API_KEY="k", FULLSTORY_DATACENTER="EU1")
    assert eu.base_url == "https://api.eu1.fullstory.com"


def test_fullstory_id_composto_de_sessao():
    assert FullStoryClient.format_session_id("dev-1", "sess-2") == "dev-1:sess-2"
    with pytest.raises(ValueError):
        FullStoryClient.format_session_id("", "sess-2")


def test_datadog_envia_as_duas_chaves():
    client = DatadogClient(DatadogSettings(DD_API_KEY="api", DD_APP_KEY="app"))
    assert client._client.headers["DD-API-KEY"] == "api"
    assert client._client.headers["DD-APPLICATION-KEY"] == "app"


def test_datadog_remove_esquema_do_site():
    settings = DatadogSettings(DD_API_KEY="a", DD_APP_KEY="b", DD_SITE="https://us5.datadoghq.com")
    assert settings.site == "us5.datadoghq.com"


def test_datadog_site_por_servico():
    settings = DatadogSettings(
        DD_API_KEY="a", DD_APP_KEY="b", DD_SITE="datadoghq.com", DD_LOGS_SITE="us3.datadoghq.com"
    )
    assert settings.host_for("logs") == "us3.datadoghq.com"
    assert settings.host_for("metrics") == "datadoghq.com"


def test_servicenow_completa_dominio_da_instancia():
    settings = ServiceNowSettings(SNOW_INSTANCE="dev12345", SNOW_USERNAME="u", SNOW_PASSWORD="p")
    assert settings.base_url == "https://dev12345.service-now.com"


def test_datadog_api_key_sozinha_nao_configura():
    """A API Key sozinha permite envio, não leitura — não deve contar como configurado."""
    assert not DatadogSettings(DD_API_KEY="a").configured
    assert DatadogSettings(DD_API_KEY="a", DD_APP_KEY="b").configured


# ------------------------------------------------------ tratamento de erro


@respx.mock
async def test_403_do_datadog_explica_a_causa_provavel():
    respx.mock.get(url__regex=r".*").mock(return_value=httpx.Response(403, json={}))
    client = DatadogClient(DatadogSettings(DD_API_KEY="a", DD_APP_KEY="b"), max_retries=0)

    with pytest.raises(AuthorizationError) as exc:
        await client.get_monitor(1)

    assert "Application Key" in str(exc.value)
    await client.aclose()


@respx.mock
async def test_204_vira_none():
    respx.mock.delete(url__regex=r".*").mock(return_value=httpx.Response(204))
    client = FullStoryClient(FullStorySettings(FULLSTORY_API_KEY="k"), max_retries=0)

    assert await client.delete_user("u1") is None
    await client.aclose()


@respx.mock
async def test_429_retenta_e_depois_levanta():
    route = respx.mock.get(url__regex=r".*").mock(
        return_value=httpx.Response(429, headers={"Retry-After": "0"})
    )
    client = DatadogClient(DatadogSettings(DD_API_KEY="a", DD_APP_KEY="b"), max_retries=2)

    with pytest.raises(RateLimitError):
        await client.get_monitor(1)

    assert route.call_count == 3, "deveria ter tentado 1 + 2 retentativas"
    await client.aclose()


@respx.mock
async def test_500_e_retentado_e_sucede_na_segunda():
    respx.mock.get(url__regex=r".*").mock(
        side_effect=[httpx.Response(500), httpx.Response(200, json={"ok": True})]
    )
    client = DatadogClient(DatadogSettings(DD_API_KEY="a", DD_APP_KEY="b"), max_retries=2)

    assert await client.get_monitor(1) == {"ok": True}
    await client.aclose()


@respx.mock
async def test_404_nao_e_retentado():
    route = respx.mock.get(url__regex=r".*").mock(return_value=httpx.Response(404, json={}))
    client = DatadogClient(DatadogSettings(DD_API_KEY="a", DD_APP_KEY="b"), max_retries=3)

    with pytest.raises(ProviderError):
        await client.get_monitor(1)

    assert route.call_count == 1, "erro de cliente não deve ser retentado"
    await client.aclose()


# ------------------------------------------------- análise portada (FullStory)


def _pix_events():
    return load("fullstory", "session_pix_failure.json")["events"]


def test_duracao_da_sessao_em_segundos():
    ordered = analytics.sort_events(_pix_events())
    # 14:30:00 → 14:31:50
    assert analytics.calculate_session_duration(ordered) == 110


def test_clusterizacao_categoriza_por_intencao():
    ordered = analytics.sort_events(_pix_events())
    clustering = analytics.generate_event_clustering(ordered)

    categorias = clustering["behavioralCategories"]
    assert categorias[analytics.NAVIGATION]["count"] == 1  # navigate
    assert categorias[analytics.TASK]["count"] == 2  # dois change
    assert categorias[analytics.INFORMATION]["count"] == 1  # click
    assert categorias[analytics.ENTERTAINMENT]["count"] == 2  # mouse_thrash
    assert categorias[analytics.FEEDBACK]["count"] == 1  # exception

    assert clustering["totalEvents"] == 7
    assert sum(c["percentage"] for c in categorias.values()) == pytest.approx(100, abs=2)


def test_fluxo_da_sessao_identifica_transicoes():
    ordered = analytics.sort_events(_pix_events())
    flow = analytics.analyze_session_flow(ordered)

    assert len(flow["transitions"]) == 6  # 7 eventos → 6 transições
    caminhos = {p["path"] for p in flow["commonPaths"]}
    assert "click → mouse_thrash" in caminhos


def test_abandono_so_conta_acima_de_cinco_minutos():
    events = [
        {"event_type": "click", "event_time": "2026-08-07T14:00:00Z"},
        {"event_type": "click", "event_time": "2026-08-07T14:10:00Z"},
    ]
    flow = analytics.analyze_session_flow(analytics.sort_events(events))
    assert len(flow["dropoffPoints"]) == 1
    assert flow["dropoffPoints"][0]["delayMinutes"] == 10


def test_evento_custom_de_compra_vai_para_transacao():
    events = [
        {
            "event_type": "custom",
            "event_time": "2026-08-07T14:00:00Z",
            "event_properties": {"event_name": "checkout_concluido"},
        }
    ]
    clustering = analytics.generate_event_clustering(analytics.sort_events(events))
    assert clustering["behavioralCategories"][analytics.TRANSACTION]["count"] == 1


def test_score_de_engajamento_no_intervalo():
    assert analytics.calculate_engagement_score([]) == 0
    score = analytics.calculate_engagement_score(
        [{"event_type": "purchase"}, {"event_type": "page_view"}]
    )
    assert 0 <= score <= 100


def test_meta_da_sessao_separa_local_e_dispositivo():
    ordered = analytics.sort_events(_pix_events())
    meta = analytics.extract_session_meta(ordered)
    assert meta["location"] == {"country": "BR"}
    assert meta["device"] == {"type": "mobile"}


def test_timestamp_invalido_nao_derruba_a_analise():
    """O original propagava `Invalid Date`; aqui o evento é ignorado."""
    events = [
        {"event_type": "click", "event_time": "não é data"},
        {"event_type": "click", "event_time": "2026-08-07T14:00:00Z"},
    ]
    processed = analytics.process_session_events(events)
    assert processed["eventCount"] == 2
    assert analytics.calculate_session_duration(processed["sortedEvents"]) == 0


def test_analise_de_lista_vazia_devolve_estrutura_estavel():
    processed = analytics.process_session_events([])
    assert processed["eventCount"] == 0
    assert processed["behavioralClustering"]["clusters"] == []
