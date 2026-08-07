"""Adaptador para qualquer endpoint compatível com a API da OpenAI.

Cobre Ollama, vLLM, LM Studio, Azure, OpenRouter e Groq com o mesmo código, e
usa só `httpx` — sem dependência nova.

**Este é o ponto frágil da camada agnóstica**, e vale ser explícito: saída
estruturada é o que menos padroniza entre implementações. A estratégia é em
degraus: tenta `json_schema` nativo; se o endpoint não suportar, cai para modo
JSON; se ainda assim a validação falhar, faz **uma** retentativa apresentando o
erro de validação ao modelo. Modelos locais pequenos erram mais aqui — teste o
modelo escolhido antes de confiar nas tools de análise.
"""

from __future__ import annotations

import json
import logging
from typing import Any, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from ...errors import ProviderError

logger = logging.getLogger(__name__)

SchemaT = TypeVar("SchemaT", bound=BaseModel)


class OpenAICompatProvider:
    name = "openai-compat"

    def __init__(self, *, model: str, base_url: str, api_key: str | None = None) -> None:
        self.model = model
        self.base_url = base_url.rstrip("/")
        self._headers = {"Content-Type": "application/json"}
        if api_key:
            self._headers["Authorization"] = f"Bearer {api_key}"

    async def complete_structured(
        self,
        *,
        system: str,
        prompt: str,
        schema: type[SchemaT],
        effort: str = "medium",
    ) -> SchemaT:
        json_schema = schema.model_json_schema()
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ]

        async with httpx.AsyncClient(timeout=httpx.Timeout(180.0)) as client:
            # Degrau 1: json_schema nativo.
            text = await self._try(
                client,
                messages,
                {
                    "type": "json_schema",
                    "json_schema": {
                        "name": schema.__name__,
                        "schema": json_schema,
                        "strict": True,
                    },
                },
            )

            # Degrau 2: modo JSON genérico, com o schema descrito no prompt.
            if text is None:
                logger.info(
                    "endpoint não aceitou json_schema; caindo para modo JSON genérico"
                )
                messages = [
                    {
                        "role": "system",
                        "content": (
                            f"{system}\n\nResponda EXCLUSIVAMENTE com JSON válido "
                            f"aderente a este schema:\n{json.dumps(json_schema, ensure_ascii=False)}"
                        ),
                    },
                    {"role": "user", "content": prompt},
                ]
                text = await self._try(client, messages, {"type": "json_object"})

            if text is None:
                text = await self._try(client, messages, None)

            if text is None:
                raise ProviderError(
                    self.name, "o endpoint não respondeu de forma utilizável"
                )

            try:
                return schema.model_validate_json(_strip_fences(text))
            except ValidationError as first_error:
                # Degrau 3: uma retentativa apresentando o erro de validação.
                logger.info("saída não validou; uma retentativa com o erro no prompt")
                retry_messages = messages + [
                    {"role": "assistant", "content": text},
                    {
                        "role": "user",
                        "content": (
                            "A resposta anterior não passou na validação:\n"
                            f"{first_error}\n\n"
                            "Responda de novo, só com o JSON corrigido."
                        ),
                    },
                ]
                retry = await self._try(client, retry_messages, {"type": "json_object"})
                if retry is None:
                    raise ProviderError(
                        self.name, f"saída inválida e retentativa falhou: {first_error}"
                    ) from first_error
                try:
                    return schema.model_validate_json(_strip_fences(retry))
                except ValidationError as second_error:
                    raise ProviderError(
                        self.name,
                        "o modelo não produziu saída aderente ao schema após uma "
                        f"retentativa: {second_error}. Modelos pequenos costumam "
                        "falhar aqui — considere um modelo maior.",
                    ) from second_error

    async def _try(
        self,
        client: httpx.AsyncClient,
        messages: list[dict[str, str]],
        response_format: dict[str, Any] | None,
    ) -> str | None:
        body: dict[str, Any] = {"model": self.model, "messages": messages, "stream": False}
        if response_format:
            body["response_format"] = response_format

        try:
            response = await client.post(
                f"{self.base_url}/chat/completions", json=body, headers=self._headers
            )
        except httpx.HTTPError as exc:
            raise ProviderError(self.name, f"falha de rede: {exc}") from exc

        if response.status_code >= 400:
            # 400 com response_format normalmente significa "não suporto isso":
            # devolve None para o chamador tentar o próximo degrau.
            if response.status_code == 400 and response_format is not None:
                return None
            raise ProviderError(
                self.name,
                f"HTTP {response.status_code}: {response.text[:300]}",
                status_code=response.status_code,
            )

        try:
            payload = response.json()
            return payload["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError):
            return None


def _strip_fences(text: str) -> str:
    """Remove cercas markdown — modelos locais adoram embrulhar JSON em ```json."""
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.split("\n", 1)[-1] if "\n" in stripped else stripped
        if stripped.endswith("```"):
            stripped = stripped[:-3]
    return stripped.strip()
