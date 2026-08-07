"""Resolução do modo de correlação.

Identidade e tempo não são alternativas: o tempo define a **janela**, a
identidade define o **filtro** dentro dela. O ponto delicado é o modo `both`,
que tenta identidade e cai para temporal — e precisa **dizer** que caiu, senão
quem consome interpreta ruído de outros usuários como evidência.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..models import CorrelationMode


@dataclass
class ResolvedCorrelation:
    mode: CorrelationMode
    query: str
    fallback_reason: str | None = None

    @property
    def filtered_by_identity(self) -> bool:
        return self.mode == "identity"


def compose_query(base_query: str | None, user_attr: str, uid: str | None) -> str:
    """Anexa o filtro de identidade à consulta base."""
    identity_clause = f"{user_attr}:{uid}"
    if not base_query or base_query.strip() in ("", "*"):
        return identity_clause
    return f"{base_query} {identity_clause}"


def resolve(
    requested: CorrelationMode,
    *,
    base_query: str | None,
    user_attr: str | None,
    uid: str | None,
) -> ResolvedCorrelation:
    """Decide o modo efetivo antes da consulta.

    O fallback por resultado vazio é decidido depois, por quem chamou — ver
    `downgrade_to_time`.
    """
    normalized_query = base_query or "*"

    if requested == "time":
        return ResolvedCorrelation(mode="time", query=normalized_query)

    if not uid:
        reason = "sem uid na janela — não há identidade para filtrar"
        if requested == "identity":
            return ResolvedCorrelation(mode="identity", query=normalized_query, fallback_reason=reason)
        return ResolvedCorrelation(mode="time", query=normalized_query, fallback_reason=reason)

    if not user_attr:
        reason = "FS_DD_USER_ATTR não configurado — não há atributo pelo qual filtrar"
        if requested == "identity":
            return ResolvedCorrelation(mode="identity", query=normalized_query, fallback_reason=reason)
        return ResolvedCorrelation(mode="time", query=normalized_query, fallback_reason=reason)

    return ResolvedCorrelation(mode="identity", query=compose_query(base_query, user_attr, uid))


def downgrade_to_time(
    resolved: ResolvedCorrelation, base_query: str | None, *, requested: CorrelationMode
) -> ResolvedCorrelation:
    """Aplica o fallback do modo `both` quando a busca por identidade veio vazia.

    Só o modo `both` cai. Em `identity` puro, resultado vazio é uma resposta
    legítima — significa que o atributo não está nos logs, e mascarar isso com
    dados de outros usuários seria pior que devolver vazio.
    """
    if requested != "both":
        return resolved
    return ResolvedCorrelation(
        mode="time",
        query=base_query or "*",
        fallback_reason=(
            "filtro por identidade não retornou resultados; "
            "resultado abaixo é da janela inteira e pode conter outros usuários"
        ),
    )
