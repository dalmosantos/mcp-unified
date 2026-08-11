"""Handoff tipado entre as fases do agente reativo.

Cada fase declara o que produz. A fase seguinte recebe esse tipo, não uma
string — o que transforma "um agente conversando com outro" em chamada de
função com contrato verificável.

Duas garantias vivem no tipo, não no prompt, porque prompt é sugestão e tipo é
lei:

1. `evidence` tem `min_length=1` — não existe classificação sem ao menos uma
   evidência. É a falha que mais machuca um L1: severidade confiante sem nada
   que a sustente.
2. `should_notify_immediately=False` exige `deferral_reason`, e o inverso
   também é recusado. Uma decisão de adiar sem motivo legível é indistinguível
   de um bug de classificação três semanas depois.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator

Severity = Literal["critical", "high", "medium", "low"]
"""Espelha a severidade do monitor do Datadog. Literal em vez de Enum para
seguir o idioma de `models.py`, e porque o valor atravessa JSON dos dois lados."""

Channel = Literal["teams_immediate", "daily_digest", "suppressed"]
"""Destino da notificação. `suppressed` é para duplicata de alerta já notificado
— não é adiamento, é silêncio deliberado."""


class EvidenceItem(BaseModel):
    """Um fato que sustenta a classificação, com a origem preservada.

    `tool` guarda qual tool do mcp-unified produziu o fato. Sem isso, revisar
    uma classificação errada vira arqueologia: você lê a conclusão e não tem
    como refazer o caminho.
    """

    kind: Literal["historico", "impacto_ao_vivo", "mudanca", "chamado", "documentacao"]
    summary: str = Field(description="Uma frase sobre o que este fato mostra")
    tool: str | None = Field(
        default=None,
        description="Tool do mcp-unified que produziu o fato: correlate_session_with_logs…",
    )
    reference: str | None = Field(
        default=None,
        description="Ponteiro verificável: INC0000000, ID de sessão, URL do post-mortem",
    )


class AlertClassification(BaseModel):
    """Fase 1 — o que o LLM devolve ao ver um alerta.

    É a única saída de modelo em todo o pipeline. Tudo depois dela é código
    determinístico operando sobre este objeto.
    """

    alert_id: str = Field(description="ID do monitor/evento no Datadog")
    incident_key: str | None = Field(
        default=None,
        description="Número do ServiceNow quando existe. É a chave de junção do histórico "
        "— e é incompleta de propósito: nem todo alerta vira chamado.",
    )
    title: str = Field(description="Uma linha legível por humano")
    severity: Severity
    summary: str = Field(description="O que está acontecendo, em um parágrafo")

    similar_incidents: list[str] = Field(
        default_factory=list,
        description="Referências dos incidentes semelhantes achados no histórico",
    )
    evidence: list[EvidenceItem] = Field(
        min_length=1,
        description="Ao menos um fato. Sem evidência não há classificação.",
    )
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description="Confiança na classificação. Limites validados para que o "
        "limiar da rota seja comparável entre execuções.",
    )

    # ── A aresta estrutural ────────────────────────────────────────────────
    # O roteamento "crítico → Teams imediato, não-crítico → acumulador" da
    # Semana 4 é uma decisão do agente. Como campo tipado, ela vira propriedade
    # do payload: dá para testar, auditar e medir contra o golden dataset sem
    # rodar o modelo. Como `if` no meio do código, não dá.
    should_notify_immediately: bool = Field(
        default=False,
        description="True só quando a interrupção se justifica agora. O padrão é False: "
        "o custo de acumular é atraso; o de interromper à toa é o time parar de ler.",
    )
    deferral_reason: str | None = Field(
        default=None,
        description="Obrigatório quando should_notify_immediately=False. Ex.: 'abaixo do "
        "limiar de confiança', 'jornada não-crítica', 'duplicata de INC0012345'.",
    )

    @model_validator(mode="after")
    def _coerencia_do_adiamento(self) -> AlertClassification:
        """Adiar sem motivo, ou justificar um adiamento que não houve, é erro.

        O Heimdall deixa esse par livre; aqui ele é travado. Um `deferral_reason`
        órfão costuma indicar que o modelo mudou de ideia no meio da geração, e
        é sinal barato de classificação instável.
        """
        if not self.should_notify_immediately and not self.deferral_reason:
            raise ValueError(
                "should_notify_immediately=False exige deferral_reason — um adiamento "
                "sem motivo legível é indistinguível de falha de classificação"
            )
        if self.should_notify_immediately and self.deferral_reason:
            raise ValueError("deferral_reason não faz sentido com should_notify_immediately=True")
        return self


class Notification(BaseModel):
    """Fase 2 — o que foi entregue, ou por que não foi.

    Não é o texto da mensagem: é o registro do que aconteceu com ela. É o que
    alimenta a medição contra o golden dataset e a pesquisa de utilidade de um
    clique da Semana 5.
    """

    alert_id: str
    channel: Channel
    headline: str = Field(description="Assunto da mensagem, tamanho de notificação")
    body_markdown: str = Field(description="Corpo: histórico, impacto ao vivo, mudanças na janela")

    delivered_at: datetime | None = Field(
        default=None, description="None quando acumulado para o digest ou suprimido"
    )
    delivery_error: str | None = Field(
        default=None,
        description="Falha de entrega. Preenchido não derruba o pipeline: o alerta cru do "
        "Datadog continua chegando ao Teams em paralelo, e o agente é aditivo.",
    )

    @property
    def delivered(self) -> bool:
        return self.delivered_at is not None and self.delivery_error is None
