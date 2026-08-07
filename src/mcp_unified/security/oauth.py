"""Verificação de token para o transporte HTTP.

O SDK já entrega os endpoints `.well-known`, o bearer auth e o binding de
audiência — só falta um `TokenVerifier`. O ponto que importa aqui é validar
`aud` contra a URI canônica do servidor: é isso que impede o ataque de
*confused deputy*, em que um token emitido para outro serviço é reaproveitado
contra este.

Desligado por padrão. Relevante no modo HTTP — ou seja, para o agente de SRE,
não para a IDE via stdio.
"""

from __future__ import annotations

import logging
from typing import Any

from ..config import SecuritySettings
from ..errors import ConfigurationError

logger = logging.getLogger(__name__)


class JWTTokenVerifier:
    """Valida JWT por JWKS, conferindo assinatura, expiração e audiência."""

    def __init__(self, settings: SecuritySettings) -> None:
        if not settings.canonical_uri:
            raise ConfigurationError(
                "MCP_SERVER_CANONICAL_URI é obrigatório com MCP_AUTH_ENABLED=true — "
                "sem ele não há como validar a audiência do token, e o servidor "
                "aceitaria tokens emitidos para outros serviços."
            )
        if not settings.jwks_url:
            raise ConfigurationError("MCP_AUTH_JWKS_URL é obrigatório com MCP_AUTH_ENABLED=true.")

        try:
            from jwt import PyJWKClient
        except ImportError as exc:  # pragma: no cover
            raise ConfigurationError(
                "PyJWT com extras de cripto é necessário para autenticação. "
                "Instale com: pip install 'pyjwt[crypto]'"
            ) from exc

        self.settings = settings
        self._jwks = PyJWKClient(settings.jwks_url)
        self._required_scopes = (
            {s.strip() for s in settings.required_scopes.split(",") if s.strip()}
            if settings.required_scopes
            else set()
        )

    async def verify_token(self, token: str) -> Any:
        import jwt

        try:
            signing_key = self._jwks.get_signing_key_from_jwt(token)
            claims = jwt.decode(
                token,
                signing_key.key,
                algorithms=["RS256", "RS512", "ES256"],
                audience=self.settings.canonical_uri,
                issuer=self.settings.auth_server_url or None,
                options={"require": ["exp", "aud"]},
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("token rejeitado: %s", exc)
            return None

        if self._required_scopes:
            granted = set(str(claims.get("scope", "")).split())
            missing = self._required_scopes - granted
            if missing:
                logger.warning("token sem escopos necessários: %s", sorted(missing))
                return None

        return claims


def build_auth(settings: SecuritySettings) -> tuple[Any | None, Any | None]:
    """Devolve `(auth_settings, token_verifier)` para passar ao `MCPServer`."""
    if not settings.auth_enabled:
        return None, None

    from mcp.server.auth.settings import AuthSettings
    from pydantic import AnyHttpUrl

    verifier = JWTTokenVerifier(settings)
    auth_settings = AuthSettings(
        issuer_url=AnyHttpUrl(settings.auth_server_url or settings.canonical_uri),
        resource_server_url=AnyHttpUrl(settings.canonical_uri),
        required_scopes=sorted(verifier._required_scopes) or None,
    )
    return auth_settings, verifier
