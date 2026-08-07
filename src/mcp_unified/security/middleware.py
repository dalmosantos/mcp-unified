"""Middleware único aplicado a todas as tools.

O SDK expõe `ServerMiddleware`, então validação e rate limiting entram de uma
vez para as ~70 tools — em vez do dispatcher gigante com `switch` que o
servidor original em JS precisava manter.

Ordem: rate limit → validação de conteúdo. O rate limit vem primeiro de
propósito: rejeitar cedo custa menos que validar um payload enorme para depois
recusar por excesso de chamadas.
"""

from __future__ import annotations

import logging
from typing import Any

from ..config import SecuritySettings
from .rate_limit import TokenBucketLimiter
from .validation import validate_arguments

logger = logging.getLogger(__name__)

_TOOL_CALL_METHOD = "tools/call"


class SecurityMiddleware:
    """Implementa o protocolo `ServerMiddleware` do SDK."""

    def __init__(self, settings: SecuritySettings) -> None:
        self.settings = settings
        self._limiter = (
            TokenBucketLimiter(capacity=settings.rate_limit_tool_per_minute)
            if settings.rate_limit_enabled
            else None
        )

    async def __call__(self, ctx: Any, call_next: Any) -> Any:
        if ctx.method != _TOOL_CALL_METHOD:
            return await call_next(ctx)

        params = ctx.params or {}
        tool_name = params.get("name") or "<desconhecida>"
        arguments = params.get("arguments")

        if self._limiter is not None:
            key = self._bucket_key(ctx)
            allowed, wait_seconds = self._limiter.check(key)
            if not allowed:
                logger.warning("rate limit atingido para %s (%s)", tool_name, key)
                return _error(
                    f"Limite de chamadas excedido. Aguarde ~{wait_seconds:.0f}s antes de "
                    f"chamar novamente. Ajuste com RATE_LIMIT_TOOL_MAX_REQUESTS."
                )

        report = validate_arguments(tool_name, arguments)
        if not report.valid:
            logger.warning("validação rejeitou %s: %s", tool_name, report.errors)
            return _error(
                "Argumentos rejeitados pela validação de conteúdo: " + "; ".join(report.errors[:5])
            )

        return await call_next(ctx)

    @staticmethod
    def _bucket_key(ctx: Any) -> str:
        """Chave do bucket: sessão quando existir, senão global.

        Em stdio há uma sessão só, então o limite é efetivamente global — que
        é o comportamento desejado para uso local.
        """
        session = getattr(ctx, "session", None)
        return str(id(session)) if session is not None else "global"


def _error(message: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": message}], "isError": True}
