"""Adaptador Anthropic. Import do SDK é preguiçoso."""

from __future__ import annotations

from typing import TypeVar

from pydantic import BaseModel

from ...errors import ConfigurationError, ProviderError

SchemaT = TypeVar("SchemaT", bound=BaseModel)

# Mapeia o esforço genérico para o parâmetro do provedor.
_EFFORT = {"low": "low", "medium": "medium", "high": "high"}


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, *, model: str, api_key: str | None = None) -> None:
        try:
            from anthropic import AsyncAnthropic
        except ImportError as exc:  # pragma: no cover - depende do ambiente
            raise ConfigurationError(
                "provedor 'anthropic' selecionado mas o SDK não está instalado. "
                "Instale com: pip install 'mcp-unified[llm-anthropic]'"
            ) from exc

        self.model = model
        # Sem api_key explícita o SDK resolve pelo ambiente.
        self._client = AsyncAnthropic(api_key=api_key) if api_key else AsyncAnthropic()

    async def complete_structured(
        self,
        *,
        system: str,
        prompt: str,
        schema: type[SchemaT],
        effort: str = "medium",
    ) -> SchemaT:
        try:
            response = await self._client.messages.parse(
                model=self.model,
                max_tokens=8192,
                system=system,
                messages=[{"role": "user", "content": prompt}],
                output_format=schema,
                thinking={"type": "adaptive"},
                output_config={"effort": _EFFORT.get(effort, "medium")},
            )
        except Exception as exc:  # noqa: BLE001
            raise ProviderError("anthropic", f"falha na inferência: {exc}") from exc

        parsed = getattr(response, "parsed_output", None)
        if parsed is None:
            raise ProviderError(
                "anthropic",
                "resposta não pôde ser validada contra o schema solicitado",
            )
        return parsed
