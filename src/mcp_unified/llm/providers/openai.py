"""Adaptador OpenAI. Import do SDK é preguiçoso."""

from __future__ import annotations

from typing import TypeVar

from pydantic import BaseModel

from ...errors import ConfigurationError, ProviderError

SchemaT = TypeVar("SchemaT", bound=BaseModel)


class OpenAIProvider:
    name = "openai"

    def __init__(
        self, *, model: str, api_key: str | None = None, base_url: str | None = None
    ) -> None:
        try:
            from openai import AsyncOpenAI
        except ImportError as exc:  # pragma: no cover - depende do ambiente
            raise ConfigurationError(
                "provedor 'openai' selecionado mas o SDK não está instalado. "
                "Instale com: pip install 'mcp-unified[llm-openai]'"
            ) from exc

        self.model = model
        kwargs: dict[str, object] = {}
        if api_key:
            kwargs["api_key"] = api_key
        if base_url:
            kwargs["base_url"] = base_url
        self._client = AsyncOpenAI(**kwargs)  # type: ignore[arg-type]

    async def complete_structured(
        self,
        *,
        system: str,
        prompt: str,
        schema: type[SchemaT],
        effort: str = "medium",
    ) -> SchemaT:
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ]

        # O helper `parse` mudou de lugar entre versões do SDK; aceita os dois.
        parse = getattr(getattr(self._client, "chat", None), "completions", None)
        parse_fn = getattr(parse, "parse", None)
        if parse_fn is None:
            beta = getattr(self._client, "beta", None)
            parse_fn = getattr(
                getattr(getattr(beta, "chat", None), "completions", None), "parse", None
            )
        if parse_fn is None:  # pragma: no cover - SDK muito antigo
            raise ConfigurationError(
                "a versão instalada do SDK da OpenAI não expõe saída estruturada; "
                "atualize com: pip install -U openai"
            )

        try:
            response = await parse_fn(
                model=self.model, messages=messages, response_format=schema
            )
        except Exception as exc:  # noqa: BLE001
            raise ProviderError("openai", f"falha na inferência: {exc}") from exc

        try:
            parsed = response.choices[0].message.parsed
        except (AttributeError, IndexError) as exc:
            raise ProviderError("openai", "resposta em formato inesperado") from exc

        if parsed is None:
            raise ProviderError(
                "openai", "resposta não pôde ser validada contra o schema solicitado"
            )
        return parsed
