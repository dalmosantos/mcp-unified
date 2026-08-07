"""Infraestrutura comum dos testes.

Nenhum teste faz chamada de rede nem usa credencial real: todas as respostas
HTTP são interceptadas por `respx`, e o provedor de LLM é um dublê que
implementa o `Protocol`.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import respx
from httpx import Response
from pydantic import BaseModel

from mcp_unified.config import Settings

FIXTURES = Path(__file__).parent / "fixtures"

# Credenciais sintéticas. O valor não importa: nada sai da máquina.
FAKE_ENV = {
    "FULLSTORY_API_KEY": "fake-fs-key",
    "FULLSTORY_ORG_ID": "ORG123",
    "DD_API_KEY": "fake-dd-api",
    "DD_APP_KEY": "fake-dd-app",
    "FS_DD_USER_ATTR": "@usr.id",
}

SNOW_ENV = {
    "SNOW_INSTANCE": "dev-instance",
    "SNOW_USERNAME": "svc_user",
    "SNOW_PASSWORD": "svc_pass",
}


def load(*parts: str) -> Any:
    return json.loads((FIXTURES.joinpath(*parts)).read_text(encoding="utf-8"))


@pytest.fixture
def fake_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """FullStory + Datadog configurados; ServiceNow e Graph ausentes."""
    for key in (
        "SNOW_INSTANCE",
        "SNOW_USERNAME",
        "SNOW_PASSWORD",
        "MSGRAPH_TENANT_ID",
        "MSGRAPH_CLIENT_ID",
        "MSGRAPH_CLIENT_SECRET",
        "MCP_LLM_PROVIDER",
        "SAFE_MODE",
        "MCP_TOOLSETS",
        "MCP_PROFILE",
    ):
        monkeypatch.delenv(key, raising=False)
    for key, value in FAKE_ENV.items():
        monkeypatch.setenv(key, value)


@pytest.fixture
def fake_env_with_snow(fake_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """Acrescenta ServiceNow — usado no cenário de mudança como causa raiz."""
    for key, value in SNOW_ENV.items():
        monkeypatch.setenv(key, value)


@pytest.fixture
def settings(fake_env: None) -> Settings:
    return Settings()


# --------------------------------------------------------------- rotas mock


def mock_fullstory_session(router: respx.Router, fixture: str) -> None:
    """Eventos de sessão. `url__regex` porque o ID é composto (uid:sid)."""
    router.get(url__regex=r".*/v2/sessions/.+/events.*").mock(
        return_value=Response(200, json=load("fullstory", fixture))
    )


def mock_fullstory_sessions_list(router: respx.Router) -> None:
    router.get(url__regex=r".*/v1/sessions.*").mock(
        return_value=Response(200, json=load("fullstory", "sessions_list.json"))
    )


def mock_datadog_logs(router: respx.Router, fixture: str) -> None:
    router.post(url__regex=r".*/api/v2/logs/events/search").mock(
        return_value=Response(200, json=load("datadog", fixture))
    )


def mock_datadog_aggregate(router: respx.Router) -> None:
    router.post(url__regex=r".*/api/v2/logs/analytics/aggregate").mock(
        return_value=Response(200, json=load("datadog", "aggregate_logs_by_user.json"))
    )


def mock_datadog_spans(router: respx.Router) -> None:
    router.post(url__regex=r".*/api/v2/spans/events/search").mock(
        return_value=Response(200, json=load("datadog", "spans_pix_slow.json"))
    )


def mock_datadog_events(router: respx.Router) -> None:
    router.get(url__regex=r".*/api/v1/events.*").mock(
        return_value=Response(200, json=load("datadog", "events_deploy.json"))
    )


def mock_datadog_rum_empty(router: respx.Router) -> None:
    router.post(url__regex=r".*/api/v2/rum/events/search").mock(
        return_value=Response(200, json={"data": []})
    )


def mock_servicenow(router: respx.Router) -> None:
    """A Table API é o mesmo path para tabelas diferentes; roteia pelo nome."""

    def handler(request: Any) -> Response:
        if "change_request" in str(request.url):
            return Response(200, json=load("servicenow", "change_request_pix.json"))
        if "incident" in str(request.url):
            return Response(200, json=load("servicenow", "incident_pix.json"))
        return Response(200, json={"result": []})

    router.get(url__regex=r".*/api/now/table/.*").mock(side_effect=handler)


def mock_pix_incident_stack(router: respx.Router) -> None:
    """Todas as fontes do cenário central de falha de PIX."""
    mock_fullstory_session(router, "session_pix_failure.json")
    mock_datadog_logs(router, "logs_pix_errors.json")
    mock_datadog_spans(router)
    mock_datadog_events(router)
    mock_datadog_rum_empty(router)


# ------------------------------------------------------------- dublê de LLM


class FakeLLMProvider:
    """Implementa `LLMProvider` sem chamar API nenhuma.

    Guarda o último prompt recebido — é assim que o teste de vazamento de PII
    verifica que a redação aconteceu antes de o prompt sair.
    """

    name = "fake"
    model = "fake-model"

    def __init__(self, response: BaseModel | None = None) -> None:
        self.response = response
        self.last_system: str | None = None
        self.last_prompt: str | None = None
        self.calls = 0

    async def complete_structured(
        self, *, system: str, prompt: str, schema: type[BaseModel], effort: str = "medium"
    ) -> BaseModel:
        self.calls += 1
        self.last_system = system
        self.last_prompt = prompt
        if self.response is not None:
            return self.response
        return schema.model_construct()
