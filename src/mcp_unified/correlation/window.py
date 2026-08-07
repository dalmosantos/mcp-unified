"""Derivação da janela temporal de uma sessão.

A janela é o denominador comum entre provedores: todo mundo sabe responder
"o que aconteceu entre X e Y", mesmo sem entender o conceito de sessão.

Derivar a janela **a partir de uma sessão** é a única parte da correlação que
precisa de um provedor de sessão (hoje, FullStory). Quando não há um
configurado, as tools exigem `from`/`to` explícitos — e dizem isso.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from ..errors import CorrelationError
from ..models import SessionWindow
from ..providers.fullstory.analytics import event_timestamp
from ..providers.fullstory.tools import _extract_events


async def derive_session_window(
    fullstory_client: Any | None,
    user_id: str,
    session_id: str,
    *,
    padding_seconds: int = 60,
) -> SessionWindow:
    """Busca os eventos da sessão e extrai a janela [primeiro, último] + padding."""
    if fullstory_client is None:
        raise CorrelationError(
            "Nenhum provedor de sessão configurado (FULLSTORY_API_KEY ausente). "
            "Use as tools que aceitam janela explícita (from/to) ou configure a FullStory."
        )

    payload = await fullstory_client.get_session_events(user_id, session_id)
    events = _extract_events(payload)
    if not events:
        raise CorrelationError(
            f"Sessão {user_id}:{session_id} não retornou eventos — "
            "não é possível derivar a janela temporal. Verifique os IDs."
        )

    stamps = sorted(ts for ts in (event_timestamp(e) for e in events) if ts is not None)
    if not stamps:
        raise CorrelationError(
            f"Sessão {user_id}:{session_id} tem eventos, mas nenhum com timestamp "
            "parseável — não é possível derivar a janela."
        )

    window = SessionWindow(
        start=stamps[0],
        end=stamps[-1],
        uid=user_id,
        session_id=session_id,
        event_count=len(events),
    )
    return window.padded(padding_seconds) if padding_seconds else window


def parse_window(
    from_: str | None, to: str | None, *, uid: str | None = None
) -> SessionWindow:
    """Monta uma janela a partir de strings ISO8601 ou epoch."""
    start = _parse_moment(from_)
    end = _parse_moment(to) or datetime.now(timezone.utc)
    if start is None:
        raise CorrelationError(
            "Início da janela é obrigatório quando não se deriva de uma sessão. "
            "Informe `from` em ISO8601 (ex: 2026-08-07T14:00:00Z)."
        )
    if start >= end:
        raise CorrelationError("O início da janela precisa ser anterior ao fim.")
    return SessionWindow(start=start, end=end, uid=uid)


def _parse_moment(value: str | None) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    if text.isdigit():
        seconds = int(text)
        if seconds > 1e11:  # milissegundos
            seconds //= 1000
        return datetime.fromtimestamp(seconds, tz=timezone.utc)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise CorrelationError(
            f"Não consegui interpretar '{value}' como data. "
            "Use ISO8601 (2026-08-07T14:00:00Z) ou epoch em segundos."
        ) from exc
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
