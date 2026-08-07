"""Redação de PII antes de qualquer prompt sair da máquina.

Roda **independente do provedor**. Com modelo local a rede não é atravessada,
mas a redação continua valendo: o prompt também vai para log de depuração e
para o histórico de quem chamou.

Escopo brasileiro por necessidade: CPF, CNPJ e chave PIX não são cobertos por
detectores genéricos. As regras são conservadoras — preferem redigir a mais
do que a menos.
"""

from __future__ import annotations

import re
from typing import Any

# Ordem importa: padrões mais específicos primeiro, senão um genérico consome
# o trecho antes do específico ter chance.
_PATTERNS: list[tuple[str, re.Pattern[str], str]] = [
    (
        "credential",
        re.compile(
            r"\b(?:bearer|token|api[_-]?key|secret|senha|password)\s*[:=]\s*\S{8,}",
            re.IGNORECASE,
        ),
        "[CREDENCIAL_REDIGIDA]",
    ),
    (
        "cnpj",
        re.compile(r"\b\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}\b"),
        "[CNPJ_REDIGIDO]",
    ),
    (
        "cpf",
        re.compile(r"\b\d{3}\.?\d{3}\.?\d{3}-?\d{2}\b"),
        "[CPF_REDIGIDO]",
    ),
    (
        "card",
        re.compile(r"\b(?:\d{4}[ -]?){3}\d{4}\b"),
        "[CARTAO_REDIGIDO]",
    ),
    (
        "email",
        re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b"),
        "[EMAIL_REDIGIDO]",
    ),
    (
        "phone",
        re.compile(r"\b(?:\+55\s?)?\(?\d{2}\)?\s?9?\d{4}[- ]?\d{4}\b"),
        "[TELEFONE_REDIGIDO]",
    ),
    (
        "pix_random_key",
        re.compile(
            r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b",
            re.IGNORECASE,
        ),
        "[CHAVE_PIX_REDIGIDA]",
    ),
]

# Campos cujo valor é redigido inteiro, independente de casar com padrão algum.
_SENSITIVE_KEYS = frozenset(
    {
        "cpf",
        "cnpj",
        "email",
        "e_mail",
        "telefone",
        "phone",
        "chave_pix",
        "pix_key",
        "pixkey",
        "senha",
        "password",
        "token",
        "api_key",
        "secret",
        "authorization",
        "card_number",
        "numero_cartao",
    }
)


def redact_text(text: str) -> str:
    """Aplica todos os padrões a uma string."""
    if not text:
        return text
    for _, pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def redact(value: Any, *, _depth: int = 0) -> Any:
    """Redige recursivamente strings, dicionários e listas.

    Profundidade limitada para não travar em estrutura cíclica ou absurda.
    """
    if _depth > 12:
        return "[PROFUNDIDADE_EXCEDIDA]"

    if isinstance(value, str):
        return redact_text(value)

    if isinstance(value, dict):
        out: dict[Any, Any] = {}
        for key, inner in value.items():
            if isinstance(key, str) and key.strip().lower() in _SENSITIVE_KEYS:
                out[key] = "[REDIGIDO]"
            else:
                out[key] = redact(inner, _depth=_depth + 1)
        return out

    if isinstance(value, (list, tuple)):
        return [redact(item, _depth=_depth + 1) for item in value]

    return value


def audit(text: str) -> dict[str, int]:
    """Conta ocorrências por tipo, sem expor o conteúdo.

    Usado nos testes para provar que a redação aconteceu, e útil em log de
    depuração para saber o que foi removido sem registrar o que era.
    """
    return {
        name: len(pattern.findall(text)) for name, pattern, _ in _PATTERNS if pattern.search(text)
    }
