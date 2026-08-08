"""Contrato agnóstico de provedor de modelo.

Este módulo **não importa nenhum SDK**. O contrato compartilhado é
*"devolve um modelo Pydantic validado"*; **como** cada provedor chega lá é
problema do adaptador — e é exatamente aí que a agnosticidade costuma quebrar,
porque saída estruturada é a parte menos padronizada entre APIs.
"""

from __future__ import annotations

from typing import Protocol, TypeVar, runtime_checkable

from pydantic import BaseModel

from ..config import LLMSettings, reveal
from ..errors import ConfigurationError

SchemaT = TypeVar("SchemaT", bound=BaseModel)


@runtime_checkable
class LLMProvider(Protocol):
    """Provedor de modelo capaz de devolver saída estruturada validada."""

    name: str
    model: str

    async def complete_structured(
        self,
        *,
        system: str,
        prompt: str,
        schema: type[SchemaT],
        effort: str = "medium",
    ) -> SchemaT:
        """Executa a inferência e devolve uma instância validada de `schema`."""
        ...


# Padrões por provedor. Ficam aqui, e não espalhados pelos adaptadores, para
# que trocar de provedor continue sendo só mudar variável de ambiente.
DEFAULT_MODELS: dict[str, str] = {
    "anthropic": "claude-opus-5",
    "openai": "gpt-4.1-mini",
    "openai-compat": "llama3.1:8b",
}


def build_provider(settings: LLMSettings) -> LLMProvider:
    """Instancia o adaptador conforme a configuração.

    O import do SDK acontece dentro do adaptador, preguiçosamente: SDK ausente
    desabilita aquele provedor com mensagem clara, em vez de derrubar o servidor
    na importação.
    """
    if settings.provider == "none":
        raise ConfigurationError(
            "MCP_LLM_PROVIDER não configurado. Use 'anthropic', 'openai' ou "
            "'openai-compat' (Ollama, vLLM, LM Studio, Azure, OpenRouter)."
        )

    model = settings.model or DEFAULT_MODELS.get(settings.provider)
    if not model:
        raise ConfigurationError("MCP_LLM_MODEL é obrigatório para este provedor.")

    if settings.provider == "anthropic":
        from .providers.anthropic import AnthropicProvider

        return AnthropicProvider(model=model, api_key=reveal(settings.api_key))

    if settings.provider == "openai":
        from .providers.openai import OpenAIProvider

        return OpenAIProvider(model=model, api_key=reveal(settings.api_key), base_url=settings.base_url)

    from .providers.openai_compat import OpenAICompatProvider

    if not settings.base_url:
        raise ConfigurationError(
            "MCP_LLM_BASE_URL é obrigatório com 'openai-compat' "
            "(ex: http://localhost:11434/v1 para Ollama)."
        )
    return OpenAICompatProvider(model=model, base_url=settings.base_url, api_key=reveal(settings.api_key))
