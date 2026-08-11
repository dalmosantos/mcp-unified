# Fase 2 — Agente Reativo de SRE

**Fase 1 é o servidor `mcp-unified`; fase 2 é este agente.** Não confundir com as
fases numeradas do [`PLAN.md`](../PLAN.md), que são etapas *de construção do
servidor* — a "Fase 2" de lá é o provedor FullStory, e está pronta. As duas
numerações convivem: uma é do produto, outra é da obra.

Esqueleto do agente L1 descrito em [`sre-agente-autonomo.md`](../sre-agente-autonomo.md),
Semanas 3 a 5. **Não faz parte do pacote `mcp_unified`** — o
`pyproject.toml:41` empacota só `src/mcp_unified`, e o `PLAN.md:41` é explícito:

> O que **não** entra aqui: vector store, RAG, lógica de classificação,
> agendamento. Isso é lógica de agente e vive no projeto de SRE. O MCP expõe
> dados; quem decide é o cliente.

Esta pasta existe para não perder o desenho enquanto o projeto de agente não
tem repositório próprio. Quando tiver, mova-a inteira — nada aqui importa de
`mcp_unified`.

## Por que sem framework

O que as Semanas 3–5 descrevem é um **workflow determinístico**, não um agente:
polling → debounce → RAG → classificar → rotear → notificar. A ordem é fixa e
conhecida de antemão; nenhum passo depende do modelo decidir qual ferramenta
chamar em seguida. Um framework de agente entrega o laço, o roteador e a
memória de conversa — as três coisas que este fluxo não usa — e cobra em
acoplamento a um provedor.

As duas peças que valeriam o framework estão aqui, em ~200 linhas:

| Peça | Onde | O que garante |
|---|---|---|
| Handoff tipado entre fases | [`schemas.py`](schemas.py) | uma fase não recebe da anterior nada que o tipo não descreva |
| Allowlist de tools | [`pipeline.py`](pipeline.py) | o agente não alcança tool de escrita, aconteça o que acontecer no prompt |

Se o fluxo deixar de ser linear — o modelo escolhendo o próximo passo, laço com
estado persistente — a refatoração natural é Pydantic AI (agnóstico de modelo,
MCP de primeira classe, Pydantic nativo). Decida isso com o fluxo na mão, não
antes.

## O desenho

```
alerta do Datadog
      │
      ▼
  debounce + dedupe          ← determinístico, sem LLM
      │
      ▼
  contexto: FAISS (histórico) + mcp-unified (impacto ao vivo)
      │
      ▼
  classificar  ──▶ AlertClassification      ← única chamada ao LLM
      │              (evidence ≥ 1, confidence ∈ [0,1],
      │               should_notify_immediately + deferral_reason)
      ▼
  should_page()              ← função pura, testável sem LLM
      │
   ┌──┴───┐
   ▼      ▼
 Teams   acumulador
 agora   → digest 07h
```

A decisão de rota (`should_page`) é uma função pura sobre a classificação. Ela
não chama modelo, não chama rede, e o teste dela não precisa de nenhum dos
dois — é o mesmo princípio do `correlation/window.py`.

## Estado

Esqueleto: os tipos e o fluxo de controle estão completos; os colaboradores
(`AlertSource`, `IncidentMemory`, `Classifier`, `Notifier`) são protocolos sem
implementação. Escritos como `Protocol` de propósito, como em
[`protocols.py`](../src/mcp_unified/protocols.py): o pipeline é testável com
dublês antes de existir FAISS, LLM ou canal do Teams.

O que **não** está aqui e é da Semana 3–5: o cliente MCP concreto, o índice
FAISS, a sanitização de PII, o circuit breaker e o job das 07h.
