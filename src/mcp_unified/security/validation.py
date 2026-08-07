"""Validação de conteúdo dos argumentos de tool.

Port do `inputValidator.js` do fs-lexicon. **Escopo deliberadamente estreito:**
a validação de *tipo* já vem do Pydantic pelo SDK, então aqui só existe a
camada de **conteúdo malicioso** — injeção de SQL, XSS, path traversal e
injeção de comando — mais limites de tamanho e profundidade.

Duplicar a checagem de schema aqui só produziria mensagens de erro piores que
as do Pydantic.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

MAX_STRING_LENGTH = 100_000
MAX_DEPTH = 20
MAX_ARRAY_ITEMS = 10_000
MAX_KEYS = 1_000

_SQL_INJECTION = re.compile(
    r"(\b(union\s+select|drop\s+table|truncate\s+table|insert\s+into|delete\s+from)\b)"
    r"|(--\s*$)"
    r"|(;\s*(drop|delete|update|insert)\b)",
    re.IGNORECASE | re.MULTILINE,
)

_XSS = re.compile(
    r"(<script\b)|(javascript:)|(\bon(?:error|load|click|mouseover)\s*=)"
    r"|(<iframe\b)|(<embed\b)|(document\.cookie)",
    re.IGNORECASE,
)

_PATH_TRAVERSAL = re.compile(r"(\.\./)|(\.\.\\)|(%2e%2e[/\\%])|(\x00)", re.IGNORECASE)

_COMMAND_INJECTION = re.compile(
    r"(\$\([^)]*\))"  # $(...)
    r"|(`[^`]+`)"  # backticks
    r"|(\|\s*(sh|bash|zsh|cmd|powershell)\b)"
    r"|(;\s*(rm|curl|wget|nc|chmod)\s)",
    re.IGNORECASE,
)

_CHECKS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("sql_injection", _SQL_INJECTION),
    ("xss", _XSS),
    ("path_traversal", _PATH_TRAVERSAL),
    ("command_injection", _COMMAND_INJECTION),
)


@dataclass
class ValidationReport:
    valid: bool = True
    errors: list[str] = field(default_factory=list)

    def fail(self, message: str) -> None:
        self.valid = False
        self.errors.append(message)


def validate_value(value: Any, *, path: str = "", depth: int = 0) -> ValidationReport:
    """Percorre a estrutura procurando conteúdo malicioso e excessos."""
    report = ValidationReport()
    _walk(value, path or "<root>", depth, report)
    return report


def _walk(value: Any, path: str, depth: int, report: ValidationReport) -> None:
    if depth > MAX_DEPTH:
        report.fail(f"{path}: profundidade máxima de {MAX_DEPTH} excedida")
        return

    if isinstance(value, str):
        _check_string(value, path, report)
        return

    if isinstance(value, dict):
        if len(value) > MAX_KEYS:
            report.fail(f"{path}: objeto com mais de {MAX_KEYS} chaves")
            return
        for key, inner in value.items():
            if isinstance(key, str):
                _check_string(key, f"{path}.{key} (chave)", report)
            _walk(inner, f"{path}.{key}", depth + 1, report)
        return

    if isinstance(value, (list, tuple)):
        if len(value) > MAX_ARRAY_ITEMS:
            report.fail(f"{path}: lista com mais de {MAX_ARRAY_ITEMS} itens")
            return
        for index, inner in enumerate(value):
            _walk(inner, f"{path}[{index}]", depth + 1, report)


def _check_string(value: str, path: str, report: ValidationReport) -> None:
    if len(value) > MAX_STRING_LENGTH:
        report.fail(f"{path}: string acima de {MAX_STRING_LENGTH} caracteres")
        return
    for name, pattern in _CHECKS:
        if pattern.search(value):
            report.fail(f"{path}: padrão suspeito de {name}")


def validate_arguments(tool_name: str, arguments: Any) -> ValidationReport:
    """Ponto de entrada usado pelo middleware."""
    if arguments is None:
        return ValidationReport()
    report = validate_value(arguments, path=f"{tool_name}.arguments")
    return report
