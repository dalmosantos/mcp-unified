---
name: sre-business-impact
description: Traduz um incidente ou comportamento técnico em linguagem de negócio para gerentes de tecnologia, gerentes de produto e product analytics. Use quando o usuário disser "qual o impacto no negócio", "resuma para o gerente", "quantos clientes perdemos", "impacto na conversão", "tendência de erros", "status do incidente", "perda financeira", "funil afetado", "como o produto está", ou "dados para product analytics". Entrega números, tendências e uma narrativa que não exija entender logs.
license: MIT
metadata:
  version: "1.0"
  repository: https://github.com/dalmosantos/mcp-unified
  tags: sre,produto,negócio,impacto,gerente,analytics,funil,conversão
---

# Impacto de Negócio

A pergunta que a sala de guerra raramente responde bem: **e o produto, como
fica?**

Esta skill pega os mesmos dados técnicos das skills de triagem e impacto no
usuário e os reorganiza para três audiências diferentes:

- **Gerente de tecnologia** — status, severidade, sistemas afetados, tendência.
- **Gerente de produto** — quantos usuários, qual funcionalidade, jornada
  interrompida, risco de churn.
- **Product analytics** — séries temporais, comparação com baseline, funil,
  segmentação.

## Disponibilidade — confira antes

1. Tools `datadog_*` na sua lista? Sem Datadog não há volume, monitores nem
   métricas de produto.
2. `find_sessions_for_incident` ou `build_unified_timeline` disponíveis? Dão a
   dimensão de clientes afetados.
3. `datadog_product_analytics_*` ou `datadog_rum_*` disponíveis? São a base para
   conversão e funil.
4. `fullstory_get_user_analytics` disponível? Traz comportamento agregado de
   usuários e funil de conversão.
5. `analyze_incident_timeline` disponível? Pode gerar o resumo executivo quando
   houver uma timeline montada.

Anuncie em uma linha o que vai usar e o que está faltando. **Não invente
números** que não conseguiu obter.

## Escolha o público e a profundidade

Não pergunte "para quem é o resumo"; deduza do tom da pergunta e anuncie.

| Pergunta típica | Audiência | Profundidade | Tool de entrada |
|---|---|---|---|
| "status do incidente para o gerente" | Tech manager | Alto nível | `datadog_get_monitors` + `datadog_aggregate_logs` |
| "quantos clientes foram afetados" | Product manager | Quantidade + jornada | `find_sessions_for_incident` |
| "impacto na conversão" | Product manager / analytics | Funil + comparação | `datadog_product_analytics_scalar` + `fullstory_get_user_analytics` |
| "tendência de erros" | Analytics | Série temporal | `datadog_aggregate_logs` + `datadog_product_analytics_timeseries` |
| "resuma o incidente" | Qualquer um | Executivo | `analyze_incident_timeline` se houver timeline |

---

## Para gerentes de tecnologia — status e severidade

**Pergunta típica:** *"qual o status do incidente para eu subir na reunião?"*

```
datadog_get_monitors(group_states=["alert", "warn"])
datadog_aggregate_logs(
  filter={"query": "service:<serviço> status:error", "from": "<início>", "to": "<agora>"},
  compute=[{"aggregation": "count", "type": "timeseries"}],
)
```

Entregue quatro coisas, nesta ordem:

1. **Estado atual** — quantos monitores em alerta, desde quando.
2. **Tendência** — está piorando, estável ou melhorando? Use a série temporal.
3. **Sistemas afetados** — serviços, rotas ou componentes com erro.
4. **Próxima ação** — uma decisão, não uma lista de tarefas.

Não use jargão de log. "O serviço de pagamentos está em alerta há 23 minutos e
a taxa de erro subiu 8x em relação à baseline" é melhor que "status:error no
service:payments".

---

## Para gerentes de produto — clientes e jornada

**Pergunta típica:** *"quantos clientes estão sendo afetados e em qual parte do
app?"*

```
find_sessions_for_incident(
  query="service:<serviço> status:error",
  from_="<início>",
  to="<agora>",
  max_users=20,
)
```

Isso devolve os clientes mais atingidos e, se houver sessão, a URL de replay.
Para dimensionar:

```
datadog_rum_aggregate_events(
  query="@type:action @application.name:<app> @action.name:<ação>",
  from_="<início>",
  to="<agora>",
  compute=[{"aggregation": "count", "type": "total"}],
)
```

Entregue:

1. **Volume** — número de clientes distintos ou ações afetadas.
2. **Jornada** — em qual etapa ocorre (login, checkout, confirmação, etc.).
3. **Severidade experienciada** — erro, travamento, lentidão, abandono.
4. **Replay** — ofereça o link quando a discussão virar "o que a pessoa viu".

Evite afirmar "todos os clientes" se você só viu uma amostra. Diga "pelo menos
N clientes" ou "os N mais atingidos".

---

## Para product analytics — tendências, baseline e funil

**Pergunta típica:** *"qual a tendência de erros nas últimas 4 semanas?"*

```
datadog_product_analytics_timeseries(
  query="@application.name:<app> @event.name:<evento>",
  from_="2026-07-07T00:00:00Z",
  to="2026-08-07T00:00:00Z",
  interval="day",
  compute=[{"aggregation": "count", "type": "timeseries"}],
)
```

**Para funil e conversão:**

```
fullstory_get_user_analytics(
  user_identifier="<segmento ou uid representativo>",
  limit=500,
)
```

A resposta de `fullstory_get_user_analytics` inclui métricas de engajamento,
funil de conversão e padrão de comportamento. Use-a para dizer onde a jornada
perde usuários.

Sempre que possível, compare com baseline:

- **Baseline:** o mesmo dia/horário da semana anterior.
- **Janela do incidente:** desde o início do sintoma até agora.
- **Delta:** percentual de variação.

Entregue:

1. **Série temporal** — evolução diária/horária do indicador.
2. **Baseline e delta** — quanto acima ou abaixo do normal.
3. **Funil** — onde há maior abandono ou queda de conversão.
4. **Segmentação** — o erro é global ou concentrado em app, região, versão?

---

## Resumo executivo — todos os públicos

Quando a pergunta for genérica ("me resuma o incidente"), use
`analyze_incident_timeline` se já houver uma timeline montada:

```
analyze_incident_timeline(
  timeline=<resultado de build_unified_timeline>,
  question="Qual o impacto de negócio e quantos clientes foram afetados?",
)
```

Se não houver timeline, monte o resumo a partir das fontes acima. Uma boa
resposta executiva tem três partes:

1. **O que aconteceu** — em uma frase, para leigos.
2. **Impacto mensurável** — números de clientes, transações, conversão.
3. **Tendência e próximo passo** — está melhorando? O que decidir agora?

---

## Armadilhas

**Não confunda causa com impacto.** "Deploy do serviço X" é causa; "12% dos
clientes não conseguiram confirmar transferência por 18 minutos" é impacto.

**Não extrapole amostra.** `max_users=20` limita o fan-out de propósito. Diga
"pelo menos 20 clientes" ou "os 20 mais atingidos", não "só 20 clientes".

**Não omita `providers_unavailable`.** Se a resposta de correlação disser que um
provedor está desabilitado, a timeline está incompleta. A conclusão de impacto
precisa levar isso em conta.

**Não traduza "sem dados" como "sem impacto"."** Volte para `sre-setup` se as
ferramentas retornarem vazio sem erro aparente — pode ser configuração.

---

## Quando parar e escalar

- Volte para `sre-triage` se a pergunta virar "o que quebrou e por quê".
- Passe para `sre-user-impact` se precisar da narrativa individual de um cliente.
- Passe para `sre-postmortem` quando o incidente estiver mitigado e for hora de
  registrar.
