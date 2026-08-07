"""Cliente HTTP compartilhado por todos os provedores.

Substitui a fila manual de requests do `Fullstory.js` original por algo que o
httpx já resolve melhor. Concentra aqui: retry com backoff, `429` respeitando
`Retry-After`, `204` → `None`, e mapeamento de erro para as exceções comuns.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import random
from collections.abc import Mapping
from typing import Any

import httpx

from .errors import AuthorizationError, ProviderError, RateLimitError

logger = logging.getLogger(__name__)

_RETRYABLE_STATUS = frozenset({408, 429, 500, 502, 503, 504})


class BaseApiClient:
    """Base dos clientes de provedor.

    Subclasses definem `provider_name`, `base_url` e `default_headers`.
    """

    provider_name: str = "unknown"

    def __init__(
        self,
        base_url: str,
        *,
        headers: Mapping[str, str] | None = None,
        timeout: float = 30.0,
        max_retries: int = 3,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.max_retries = max_retries
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(timeout),
            headers=dict(headers or {}),
            follow_redirects=True,
        )
        if client is not None and headers:
            self._client.headers.update(dict(headers))

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def __aenter__(self) -> BaseApiClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    # ---------------------------------------------------------------- request

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        json: Any | None = None,
        headers: Mapping[str, str] | None = None,
        base_url: str | None = None,
    ) -> Any:
        """Executa a chamada e devolve o JSON já decodificado (ou `None` em 204)."""
        url = f"{(base_url or self.base_url).rstrip('/')}/{path.lstrip('/')}"
        clean_params = (
            {k: v for k, v in params.items() if v is not None} if params else None
        )

        last_exc: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                response = await self._client.request(
                    method,
                    url,
                    params=clean_params,
                    json=json,
                    headers=dict(headers) if headers else None,
                )
            except httpx.TimeoutException as exc:
                last_exc = exc
                if attempt < self.max_retries:
                    await self._sleep_backoff(attempt)
                    continue
                raise ProviderError(
                    self.provider_name, f"timeout após {self.max_retries + 1} tentativas"
                ) from exc
            except httpx.HTTPError as exc:
                raise ProviderError(self.provider_name, f"falha de rede: {exc}") from exc

            if response.status_code in _RETRYABLE_STATUS and attempt < self.max_retries:
                await self._sleep_backoff(attempt, response)
                continue

            return self._handle(response)

        raise ProviderError(self.provider_name, "retentativas esgotadas") from last_exc

    async def _sleep_backoff(
        self, attempt: int, response: httpx.Response | None = None
    ) -> None:
        """Backoff exponencial com jitter; respeita `Retry-After` quando presente."""
        delay = min(2.0**attempt, 30.0) + random.uniform(0, 0.5)
        if response is not None:
            retry_after = response.headers.get("Retry-After")
            if retry_after:
                # Formato de data HTTP: ignora e usa o backoff calculado.
                with contextlib.suppress(ValueError):
                    delay = max(delay, float(retry_after))
        logger.debug("%s: aguardando %.1fs antes de retentar", self.provider_name, delay)
        await asyncio.sleep(delay)

    def _handle(self, response: httpx.Response) -> Any:
        # A checagem de corpo vazio precisa vir *depois* da de erro: um 429 ou
        # 403 sem corpo é comum, e tratá-lo como "sem dados" faria a chamada
        # devolver None em silêncio em vez de sinalizar a falha.
        if response.is_success:
            if response.status_code == 204 or not response.content:
                return None
            try:
                return response.json()
            except ValueError:
                return {"raw_text": response.text}

        body = self._safe_body(response)

        if response.status_code == 401:
            raise ProviderError(
                self.provider_name,
                "credencial inválida ou expirada (401)",
                status_code=401,
                payload=body,
            )
        if response.status_code == 403:
            raise AuthorizationError(
                self.provider_name,
                self._forbidden_hint(),
                status_code=403,
                payload=body,
            )
        if response.status_code == 404:
            raise ProviderError(
                self.provider_name,
                f"recurso não encontrado: {response.request.url.path}",
                status_code=404,
                payload=body,
            )
        if response.status_code == 429:
            retry_after = response.headers.get("Retry-After")
            raise RateLimitError(
                self.provider_name,
                "rate limit atingido e retentativas esgotadas",
                retry_after=float(retry_after) if _is_float(retry_after) else None,
            )

        raise ProviderError(
            self.provider_name,
            f"HTTP {response.status_code}",
            status_code=response.status_code,
            payload=body,
        )

    def _forbidden_hint(self) -> str:
        """Mensagem de 403. Subclasses sobrescrevem com orientação específica."""
        return "permissão insuficiente (403) — verifique os escopos da credencial"

    @staticmethod
    def _safe_body(response: httpx.Response) -> Any:
        try:
            return response.json()
        except ValueError:
            return response.text[:500]

    # ------------------------------------------------------------- atalhos

    async def get(self, path: str, **kwargs: Any) -> Any:
        return await self.request("GET", path, **kwargs)

    async def post(self, path: str, **kwargs: Any) -> Any:
        return await self.request("POST", path, **kwargs)

    async def put(self, path: str, **kwargs: Any) -> Any:
        return await self.request("PUT", path, **kwargs)

    async def patch(self, path: str, **kwargs: Any) -> Any:
        return await self.request("PATCH", path, **kwargs)

    async def delete(self, path: str, **kwargs: Any) -> Any:
        return await self.request("DELETE", path, **kwargs)


def _is_float(value: str | None) -> bool:
    if value is None:
        return False
    try:
        float(value)
    except ValueError:
        return False
    return True
