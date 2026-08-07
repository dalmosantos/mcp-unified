"""Exceções comuns a todos os provedores.

O objetivo é que quem escreve uma tool não precise saber se o erro veio do
httpx, do provedor ou da validação — todos chegam como `MCPUnifiedError` com
mensagem já orientada a quem vai ler (um modelo, normalmente).
"""

from __future__ import annotations


class MCPUnifiedError(Exception):
    """Base de todos os erros tratados do servidor."""


class ConfigurationError(MCPUnifiedError):
    """Credencial ausente ou configuração inválida para um provedor."""


class ProviderError(MCPUnifiedError):
    """Falha vinda da API de um provedor."""

    def __init__(
        self,
        provider: str,
        message: str,
        *,
        status_code: int | None = None,
        payload: object | None = None,
    ) -> None:
        self.provider = provider
        self.status_code = status_code
        self.payload = payload
        super().__init__(f"[{provider}] {message}")


class AuthorizationError(ProviderError):
    """403 — chave válida, permissão insuficiente.

    Separado de propósito: no Datadog é de longe a falha mais comum e a
    mensagem genérica não ajuda quem está configurando as chaves.
    """


class RateLimitError(ProviderError):
    """429 depois de esgotadas as retentativas."""

    def __init__(
        self, provider: str, message: str, *, retry_after: float | None = None
    ) -> None:
        self.retry_after = retry_after
        super().__init__(provider, message, status_code=429)


class ValidationError(MCPUnifiedError):
    """Argumento rejeitado pela camada de validação de conteúdo."""


class CorrelationError(MCPUnifiedError):
    """Não foi possível correlacionar — janela indeterminada, identidade ausente etc."""
