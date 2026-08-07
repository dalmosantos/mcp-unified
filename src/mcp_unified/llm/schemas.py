"""Schemas de saída estruturada das tools de análise.

Como as tools devolvem objetos validados em vez de texto solto, quem consome
pode ramificar programaticamente — e o campo `evidence` obriga o modelo a
apontar as entradas da timeline que sustentam a hipótese, em vez de afirmar
sem lastro.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class Evidence(BaseModel):
    """Uma entrada da timeline citada como suporte à hipótese."""

    ts: str = Field(description="Timestamp da entrada citada")
    source: str = Field(description="Fonte da entrada")
    detail: str = Field(description="O que essa entrada demonstra")


class IncidentAnalysis(BaseModel):
    """Veredito estruturado sobre uma timeline de incidente."""

    root_cause_hypothesis: str = Field(
        description="A explicação mais provável, em uma ou duas frases"
    )
    confidence: Literal["baixa", "média", "alta"] = Field(
        description="Confiança na hipótese, dado o que a timeline mostra"
    )
    evidence: list[Evidence] = Field(
        default_factory=list,
        description="Entradas específicas da timeline que sustentam a hipótese",
    )
    blast_radius: str = Field(
        description="Dimensão do impacto observável: escopo, usuários, serviços"
    )
    recommended_next_step: str = Field(
        description="A próxima ação concreta de investigação ou mitigação"
    )
    contradicting_signals: list[str] = Field(
        default_factory=list,
        description="Sinais na timeline que enfraquecem a hipótese, se houver",
    )


class QueryTranslation(BaseModel):
    """Tradução de linguagem natural para sintaxe de consulta."""

    query: str = Field(description="A consulta na sintaxe do Datadog")
    explanation: str = Field(description="O que a consulta faz, em português")
    confidence: Literal["baixa", "média", "alta"] = Field(
        description="Confiança de que a consulta corresponde à intenção"
    )
    caveats: list[str] = Field(
        default_factory=list,
        description="Suposições feitas — nomes de serviço, atributos assumidos",
    )
