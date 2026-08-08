"""Configuração via variáveis de ambiente.

Princípio central: **validação preguiçosa por provedor**. Faltar credencial de
um provedor não derruba o servidor — desabilita o toolset dele, e a correlação
segue com as fontes que restarem. Com quatro provedores isso deixa de ser
conveniência e vira requisito: quase ninguém terá os quatro configurados.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict

from .models import CorrelationMode


def reveal(value: SecretStr | None) -> str:
    """Extrai o valor de um segredo, na hora de usá-lo.

    Toda credencial é `SecretStr`, cujo `str()` e `repr()` devolvem
    `**********`. Isso protege log e traceback por padrão, mas cobra o preço
    de quebrar em silêncio quem interpolar o campo direto numa f-string — o
    header sairia com os asteriscos dentro. Esta função é o único ponto onde o
    valor real aparece; se você precisou dela fora de um cliente HTTP, pare e
    releia o que está fazendo.
    """
    return value.get_secret_value() if value else ""


def _strip_scheme(url: str) -> str:
    """Remove `https://` de um host.

    O servidor original em TS fazia isso porque as pessoas colam a URL
    completa na variável de site do Datadog. Mantido por compatibilidade.
    """
    for prefix in ("https://", "http://"):
        if url.startswith(prefix):
            return url[len(prefix) :]
    return url.rstrip("/")


class _Base(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Põe o cofre **acima** do `.env`, invertendo o padrão do pydantic.

        Por padrão a ordem é `env > dotenv > cofre`, e isso erra justamente no
        caso que o cofre existe para resolver: um container com `/run/secrets`
        montado pelo orquestrador e um `.env` esquecido na imagem usaria o
        `.env` — credencial errada, sem erro nenhum, e provavelmente a antiga.

        Um diretório de segredos só existe quando alguém o montou de
        propósito, então ele ganha do arquivo. A variável de ambiente continua
        no topo: é o override explícito, e é como Kubernetes e as IDEs injetam.
        """
        return (init_settings, env_settings, file_secret_settings, dotenv_settings)


class FullStorySettings(_Base):
    api_key: SecretStr | None = Field(default=None, alias="FULLSTORY_API_KEY")
    org_id: str | None = Field(default=None, alias="FULLSTORY_ORG_ID")
    datacenter: Literal["US", "EU1"] = Field(default="US", alias="FULLSTORY_DATACENTER")
    # Override do host. Existe para o modo demo (scripts/demo_upstream.py) e
    # para quem precisa apontar para um proxy corporativo.
    base_url_override: str | None = Field(default=None, alias="FULLSTORY_BASE_URL")

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    @property
    def base_url(self) -> str:
        if self.base_url_override:
            return self.base_url_override.rstrip("/")
        return (
            "https://api.eu1.fullstory.com"
            if self.datacenter == "EU1"
            else "https://api.fullstory.com"
        )


class DatadogSettings(_Base):
    api_key: SecretStr | None = Field(default=None, alias="DD_API_KEY")
    app_key: SecretStr | None = Field(default=None, alias="DD_APP_KEY")
    site: str = Field(default="datadoghq.com", alias="DD_SITE")
    logs_site: str | None = Field(default=None, alias="DD_LOGS_SITE")
    metrics_site: str | None = Field(default=None, alias="DD_METRICS_SITE")
    # Override do host completo, incluindo esquema e porta. Mesma motivação do
    # equivalente na FullStory: modo demo e proxy corporativo.
    base_url_override: str | None = Field(default=None, alias="DD_BASE_URL")

    @field_validator("site", "logs_site", "metrics_site", mode="after")
    @classmethod
    def _clean(cls, v: str | None) -> str | None:
        return _strip_scheme(v) if v else v

    @property
    def configured(self) -> bool:
        # Ambas as chaves são necessárias: a API key sozinha só serve para
        # submissão, não para leitura.
        return bool(self.api_key and self.app_key)

    def host_for(self, service: Literal["default", "logs", "metrics"]) -> str:
        if service == "logs":
            return self.logs_site or self.site
        if service == "metrics":
            return self.metrics_site or self.site
        return self.site


class ServiceNowSettings(_Base):
    instance: str | None = Field(default=None, alias="SNOW_INSTANCE")
    auth_mode: Literal["basic", "oauth2"] = Field(default="basic", alias="SNOW_AUTH")
    username: str | None = Field(default=None, alias="SNOW_USERNAME")
    password: SecretStr | None = Field(default=None, alias="SNOW_PASSWORD")
    client_id: str | None = Field(default=None, alias="SNOW_CLIENT_ID")
    client_secret: SecretStr | None = Field(default=None, alias="SNOW_CLIENT_SECRET")

    @property
    def configured(self) -> bool:
        if not self.instance:
            return False
        if self.auth_mode == "basic":
            return bool(self.username and self.password)
        return bool(self.client_id and self.client_secret)

    @property
    def base_url(self) -> str:
        inst = _strip_scheme(self.instance or "")
        if "." not in inst:
            inst = f"{inst}.service-now.com"
        return f"https://{inst}"


class MSGraphSettings(_Base):
    tenant_id: str | None = Field(default=None, alias="MSGRAPH_TENANT_ID")
    client_id: str | None = Field(default=None, alias="MSGRAPH_CLIENT_ID")
    client_secret: SecretStr | None = Field(default=None, alias="MSGRAPH_CLIENT_SECRET")

    @property
    def configured(self) -> bool:
        return bool(self.tenant_id and self.client_id and self.client_secret)

    @property
    def base_url(self) -> str:
        return "https://graph.microsoft.com/v1.0"

    @property
    def token_url(self) -> str:
        return f"https://login.microsoftonline.com/{self.tenant_id}/oauth2/v2.0/token"


class CorrelationSettings(_Base):
    user_attr: str = Field(default="@usr.id", alias="FS_DD_USER_ATTR")
    mode: CorrelationMode = Field(default="both", alias="FS_DD_CORRELATION_MODE")
    window_padding_seconds: int = Field(default=60, alias="FS_DD_WINDOW_PADDING_SECONDS")
    max_subjects: int = Field(default=10, alias="FS_DD_MAX_SUBJECTS")


class LLMSettings(_Base):
    provider: Literal["none", "anthropic", "openai", "openai-compat"] = Field(
        default="none", alias="MCP_LLM_PROVIDER"
    )
    model: str | None = Field(default=None, alias="MCP_LLM_MODEL")
    base_url: str | None = Field(default=None, alias="MCP_LLM_BASE_URL")
    api_key: SecretStr | None = Field(default=None, alias="MCP_LLM_API_KEY")
    effort: Literal["low", "medium", "high"] = Field(default="medium", alias="MCP_LLM_EFFORT")
    max_timeline_entries: int = Field(default=300, alias="MCP_LLM_MAX_TIMELINE_ENTRIES")

    @property
    def configured(self) -> bool:
        return self.provider != "none"


class SecuritySettings(_Base):
    safe_mode: bool = Field(default=False, alias="SAFE_MODE")
    rate_limit_enabled: bool = Field(default=True, alias="RATE_LIMIT_ENABLED")
    rate_limit_tool_per_minute: int = Field(default=60, alias="RATE_LIMIT_TOOL_MAX_REQUESTS")
    auth_enabled: bool = Field(default=False, alias="MCP_AUTH_ENABLED")
    auth_server_url: str | None = Field(default=None, alias="MCP_AUTH_SERVER_URL")
    canonical_uri: str | None = Field(default=None, alias="MCP_SERVER_CANONICAL_URI")
    jwks_url: str | None = Field(default=None, alias="MCP_AUTH_JWKS_URL")
    required_scopes: str | None = Field(default=None, alias="MCP_AUTH_REQUIRED_SCOPES")


class ServerSettings(_Base):
    profile: str = Field(default="ide", alias="MCP_PROFILE")
    toolsets: str | None = Field(default=None, alias="MCP_TOOLSETS")
    http_timeout_seconds: float = Field(default=30.0, alias="MCP_HTTP_TIMEOUT")
    http_max_retries: int = Field(default=3, alias="MCP_HTTP_MAX_RETRIES")


class Settings:
    """Agregador. Instanciado uma vez e passado adiante.

    `env_file` existe por causa das IDEs. Cada cliente MCP tem uma sintaxe
    própria — ou nenhuma — para injetar segredo no bloco `env` da configuração,
    e o diretório de trabalho do processo lançado é imprevisível. Apontar um
    arquivo explícito é o único mecanismo que funciona igual em todos eles.

    `secrets_dir` existe por causa do transporte HTTP. Ali o servidor é um
    processo de vida longa e compartilhada, e um `.env` em texto plano deixa de
    ser aceitável: Docker e Kubernetes entregam segredo como arquivo em
    `/run/secrets/<NOME_DA_VARIAVEL>`, que não aparece em `docker inspect` nem
    no dump de um processo, ao contrário de variável de ambiente.

    Precedência final: **ambiente > cofre > arquivo > default**.
    """

    def __init__(
        self,
        env_file: str | Path | None = None,
        secrets_dir: str | Path | None = None,
    ) -> None:
        # Só repassa quando há caminho: em pydantic-settings, `_env_file=None`
        # explícito significa "nenhum arquivo", e não "use o padrão da classe".
        kw: dict[str, Any] = {}
        if env_file is not None:
            kw["_env_file"] = env_file
        if secrets_dir is not None:
            kw["_secrets_dir"] = secrets_dir
        self.fullstory = FullStorySettings(**kw)
        self.datadog = DatadogSettings(**kw)
        self.servicenow = ServiceNowSettings(**kw)
        self.msgraph = MSGraphSettings(**kw)
        self.correlation = CorrelationSettings(**kw)
        self.llm = LLMSettings(**kw)
        self.security = SecuritySettings(**kw)
        self.server = ServerSettings(**kw)

    def configured_providers(self) -> dict[str, bool]:
        return {
            "fullstory": self.fullstory.configured,
            "datadog": self.datadog.configured,
            "servicenow": self.servicenow.configured,
            "msgraph": self.msgraph.configured,
            "llm": self.llm.configured,
        }
