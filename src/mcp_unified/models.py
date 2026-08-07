"""Modelos compartilhados entre provedores e correlação."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field

CorrelationMode = Literal["time", "identity", "both"]


class SessionWindow(BaseModel):
    """Janela temporal derivada de uma sessão de usuário.

    É o denominador comum entre os provedores: todo mundo sabe responder
    "o que aconteceu entre X e Y", mesmo sem entender de sessão.
    """

    start: datetime
    end: datetime
    uid: str | None = None
    session_id: str | None = None
    event_count: int = 0

    @property
    def duration_seconds(self) -> float:
        return (self.end - self.start).total_seconds()

    def padded(self, seconds: int) -> SessionWindow:
        return self.model_copy(
            update={
                "start": self.start - timedelta(seconds=seconds),
                "end": self.end + timedelta(seconds=seconds),
            }
        )

    @classmethod
    def from_epoch(cls, start: float, end: float, **kwargs: Any) -> SessionWindow:
        return cls(
            start=datetime.fromtimestamp(start, tz=timezone.utc),
            end=datetime.fromtimestamp(end, tz=timezone.utc),
            **kwargs,
        )


class TimelineEntry(BaseModel):
    """Uma entrada normalizada na linha do tempo unificada.

    `raw` preserva o payload original para que quem quiser detalhe não
    precise refazer a chamada.
    """

    ts: datetime
    source: str = Field(description="Provedor de origem: fullstory, datadog, servicenow…")
    kind: str = Field(description="Tipo dentro do provedor: log, span, rage_click, change…")
    summary: str = Field(description="Descrição curta, legível")
    raw: dict[str, Any] = Field(default_factory=dict, repr=False)

    def sort_key(self) -> tuple[datetime, str]:
        return (self.ts, self.source)


class Subject(BaseModel):
    """Uma identidade afetada, extraída de alguma fonte."""

    id: str
    kind: str = "user"
    source: str
    occurrences: int = 1


class CorrelationResult(BaseModel):
    """Envelope comum das tools de correlação.

    `effective_mode` e `fallback_reason` existem para que quem consome saiba
    se o resultado veio filtrado por identidade ou só por tempo — sem isso o
    modelo interpreta ruído de outros usuários como evidência.
    """

    window: SessionWindow
    requested_mode: CorrelationMode
    effective_mode: CorrelationMode
    fallback_reason: str | None = None
    sources_used: list[str] = Field(default_factory=list)
    sources_skipped: dict[str, str] = Field(default_factory=dict)
