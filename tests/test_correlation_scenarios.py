"""Cenários de correlação, no domínio de um internet banking.

O cenário de PIX não é decoração: uma falha de transferência tem sintoma no
frontend (cliente travado na confirmação, rage clicks) e causa no backend
(timeout no SPI), que é exatamente o que a correlação promete ligar. Se ela
funciona aqui, funciona no caso geral.
"""

from __future__ import annotations

import respx
from mcp import Client

from mcp_unified.server import build_server, get_context

from .conftest import (
    mock_datadog_aggregate,
    mock_datadog_logs,
    mock_fullstory_session,
    mock_fullstory_sessions_list,
    mock_pix_incident_stack,
    mock_servicenow,
)

PIX_SESSION = {"user_id": "dev-77", "session_id": "sess-pix-1"}


async def _call(server, tool: str, args: dict):
    """Chama a tool pelo `Client` in-memory do SDK — sem transporte, sem rede.

    O SDK envolve retorno de dict em `{"result": ...}`; desembrulha aqui para
    os testes lerem o payload que a tool realmente produziu.
    """
    async with Client(server) as client:
        result = await client.call_tool(tool, args)
        structured = result.structured_content or {}
        return structured.get("result", structured)


# --------------------------------------------------------- 1. caminho feliz


@respx.mock
async def test_acesso_home_deriva_janela_e_traz_logs(fake_env):
    """Sessão saudável de acesso à home: janela correta, logs sem erro."""
    mock_fullstory_session(respx.mock, "session_home_access.json")
    mock_datadog_logs(respx.mock, "logs_home_ok.json")

    server = build_server(profile="ide")
    out = await _call(
        server, "correlate_session_with_logs", {"user_id": "dev-1", "session_id": "s-home"}
    )

    # A sessão dura 40s; o padding padrão de 60s de cada lado leva a 160s.
    assert out["window"]["duration_seconds"] == 160
    assert out["summary"]["total_entries"] == 2
    # Nenhum log de erro: nada foi marcado como notável.
    assert out["summary"]["notable_entries"] == 0


# ------------------------------------------- 2. o teste central: PIX falhando


@respx.mock
async def test_timeline_pix_intercala_frontend_e_backend_na_ordem(fake_env):
    """A prova de que a correlação funciona.

    A timeline deve intercalar, nesta ordem: clique em "Confirmar
    transferência" (FullStory) → span lento (APM) → log de timeout do SPI
    (Logs) → rage clicks (FullStory).
    """
    mock_pix_incident_stack(respx.mock)

    server = build_server(profile="all")
    out = await _call(server, "build_unified_timeline", PIX_SESSION)

    timeline = out["timeline"]
    assert timeline, "a timeline não deveria estar vazia"

    # Ordem cronológica global.
    stamps = [entry["ts"] for entry in timeline]
    assert stamps == sorted(stamps), "a timeline não está ordenada por tempo"

    def index_of(source: str, needle: str) -> int:
        for i, entry in enumerate(timeline):
            if entry["source"] == source and needle.lower() in entry["summary"].lower():
                return i
        raise AssertionError(f"não encontrei '{needle}' em {source}: {timeline}")

    clique = index_of("fullstory", "Confirmar transferência")
    span = index_of("datadog-spans", "/api/pagamentos/transferencia")
    log = index_of("datadog-logs", "timeout ao consultar SPI")
    rage = index_of("fullstory", "mouse_thrash")

    assert clique < span < log < rage, (
        "a sequência causal está fora de ordem: "
        f"clique={clique}, span={span}, log={log}, rage={rage}"
    )

    # Fontes de origens diferentes participaram — é o ponto do exercício.
    assert {"fullstory", "datadog-logs", "datadog-spans"} <= set(out["sources_used"])


@respx.mock
async def test_span_lento_e_marcado_como_notavel(fake_env):
    """8,2s no POST de transferência precisa saltar aos olhos na timeline."""
    mock_pix_incident_stack(respx.mock)

    server = build_server(profile="all")
    out = await _call(server, "build_unified_timeline", PIX_SESSION)

    span = next(e for e in out["timeline"] if e["source"] == "datadog-spans")
    assert span["summary"].startswith("⚠"), "span acima de 1s deveria ser marcado"
    assert "8200ms" in span["summary"]


# ------------------------------------------- 3. mudança entra sem tocar na tool


@respx.mock
async def test_servicenow_entra_na_timeline_sem_alterar_a_tool(fake_env_with_snow):
    """O teste que prova o protocolo.

    ServiceNow é registrado como `TimelineSource` e a change request aparece na
    timeline — sem que `build_unified_timeline` conheça ServiceNow pelo nome.
    """
    mock_pix_incident_stack(respx.mock)
    mock_servicenow(respx.mock)

    server = build_server(profile="all")
    ctx = get_context(server)
    assert "servicenow" in ctx.timeline_source_names()

    out = await _call(server, "build_unified_timeline", PIX_SESSION)

    changes = [e for e in out["timeline"] if e["kind"] == "change_request"]
    assert changes, f"a change request não entrou na timeline: {out['sources_used']}"
    assert "CHG0000045" in changes[0]["summary"]

    # E veio antes da primeira falha — que é o que a torna suspeita.
    primeiro_erro = next(e for e in out["timeline"] if "timeout" in e["summary"].lower())
    assert changes[0]["ts"] < primeiro_erro["ts"]


# ------------------------------------------------ 4. incidente → clientes


@respx.mock
async def test_incidente_resolve_clientes_afetados_com_link_de_replay(fake_env):
    """Direção inversa: da query do incidente até as sessões reais."""
    mock_datadog_aggregate(respx.mock)
    mock_fullstory_sessions_list(respx.mock)

    server = build_server(profile="ide")
    out = await _call(
        server,
        "find_sessions_for_incident",
        {
            "query": "service:servico-transferencia status:error",
            "from_": "2026-08-07T14:25:00Z",
            "to": "2026-08-07T14:45:00Z",
            "max_users": 2,
        },
    )

    users = out["affected_users"]
    assert len(users) == 2, "max_users deveria limitar o fan-out"
    # Ordenado por número de ocorrências nos logs.
    assert users[0]["uid"] == "cliente-4471"
    assert users[0]["occurrences"] == 12

    assert out["sessions"], "deveria ter resolvido sessões para os usuários"
    assert all(s["replay_url"] for s in out["sessions"])
    assert "app.fullstory.com" in out["sessions"][0]["replay_url"]


@respx.mock
async def test_incidente_sem_identidade_explica_o_que_configurar(fake_env):
    """Sem o atributo nos logs, a resposta orienta em vez de dar erro genérico."""
    respx.mock.post(url__regex=r".*/api/v2/logs/analytics/aggregate").mock(
        return_value=respx.MockResponse(200, json={"data": []})
    )

    server = build_server(profile="ide")
    out = await _call(
        server,
        "find_sessions_for_incident",
        {"query": "status:error", "from_": "2026-08-07T14:00:00Z"},
    )

    assert out["affected_users"] == []
    assert "FS_DD_USER_ATTR" in out["hint"]
    assert "@usr.id" in out["hint"]


# ------------------------------------------------------ 5. modos de correlação


@respx.mock
async def test_modo_identity_filtra_pela_identidade(fake_env):
    mock_pix_incident_stack(respx.mock)

    server = build_server(profile="ide")
    out = await _call(
        server,
        "correlate_session_with_logs",
        {**PIX_SESSION, "correlation_mode": "identity", "query": "service:x"},
    )

    assert out["effective_mode"] == "identity"
    assert "@usr.id:dev-77" in out["query_used"]


@respx.mock
async def test_modo_time_nao_filtra_por_usuario(fake_env):
    mock_pix_incident_stack(respx.mock)

    server = build_server(profile="ide")
    out = await _call(
        server, "correlate_session_with_logs", {**PIX_SESSION, "correlation_mode": "time"}
    )

    assert out["effective_mode"] == "time"
    assert "@usr.id" not in out["query_used"]


@respx.mock
async def test_modo_both_cai_para_tempo_e_avisa(fake_env):
    """O ponto delicado: cair para temporal é aceitável, mas silenciar não é.

    Sem o aviso, quem consome interpreta ruído de outros usuários como
    evidência sobre este usuário.
    """
    mock_fullstory_session(respx.mock, "session_pix_failure.json")

    chamadas = {"n": 0}

    def logs_handler(request):
        chamadas["n"] += 1
        # Primeira chamada (com filtro de identidade) vem vazia; a segunda traz dados.
        if chamadas["n"] == 1:
            return respx.MockResponse(200, json={"data": []})
        return respx.MockResponse(
            200,
            json={
                "data": [
                    {
                        "id": "l",
                        "attributes": {
                            "timestamp": "2026-08-07T14:31:09Z",
                            "service": "outro-servico",
                            "status": "error",
                            "message": "erro de outro cliente",
                        },
                    }
                ]
            },
        )

    respx.mock.post(url__regex=r".*/api/v2/logs/events/search").mock(side_effect=logs_handler)

    server = build_server(profile="ide")
    out = await _call(
        server, "correlate_session_with_logs", {**PIX_SESSION, "correlation_mode": "both"}
    )

    assert out["requested_mode"] == "both"
    assert out["effective_mode"] == "time"
    assert out["fallback_reason"], "o fallback precisa ser declarado no envelope"
    assert "outros usuários" in out["fallback_reason"]
    assert out["summary"]["total_entries"] == 1


@respx.mock
async def test_modo_identity_puro_nao_cai_para_tempo(fake_env):
    """Em `identity`, vazio é resposta legítima — mascarar seria pior."""
    mock_fullstory_session(respx.mock, "session_pix_failure.json")
    respx.mock.post(url__regex=r".*/api/v2/logs/events/search").mock(
        return_value=respx.MockResponse(200, json={"data": []})
    )

    server = build_server(profile="ide")
    out = await _call(
        server, "correlate_session_with_logs", {**PIX_SESSION, "correlation_mode": "identity"}
    )

    assert out["effective_mode"] == "identity"
    assert out["summary"]["total_entries"] == 0
