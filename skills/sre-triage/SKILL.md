---
name: sre-triage
description: Triagem de incidente a partir de um alerta do Datadog — estabelece o que quebrou, desde quando, qual o alcance e qual a causa mais provável. Use quando o usuário disser "o monitor X disparou", "está tudo lento", "temos um incidente", "o que está acontecendo com o serviço Y", "por que os erros subiram", ou colar um alerta do Datadog. Termina com uma hipótese e uma decisão sobre escalar para impacto no usuário.
license: MIT
metadata:
  version: "1.0"
  repository: https://github.com/dalmosantos/mcp-unified
  tags: sre,datadog,incidente,triagem,observabilidade
---

# Triagem de Incidente

Do alerta à hipótese, sem abrir cinco abas.

## Disponibilidade — confira antes de propor um caminho

Este servidor desabilita provedores sem credencial, e isso é o caso comum.
**Antes de planejar a investigação**, veja o que existe:

1. Procure tools `datadog_*` na sua lista. Sem elas, não há triagem possível —
   diga ao usuário que faltam `DD_API_KEY` e `DD_APP_KEY` (as duas: a API Key
   sozinha só permite envio, não leitura) e pare.
2. Se `build_unified_timeline` existir, você tem correlação. Isso muda a
   Fase 3 — veja lá.
3. Se `datadog_apm_search_spans` existir, o caminho de latência fica muito mais
   curto. Sem ela, você trabalha só com logs.

Não pergunte ao usuário o que está configurado; descubra na sua lista de tools
e **anuncie** o que vai usar em uma linha.

## Metodologia

**Delimitar → Confirmar → Ampliar → Datar → Concluir**

Cinco fases, nesta ordem. A tentação é pular direto para "buscar logs de erro";
resista, porque sem delimitar você não sabe se o que achou é o incidente ou o
ruído de fundo normal do sistema.

### Fase 1 — Delimitar

Estabeleça três coisas antes de qualquer busca:

| O quê | Como |
|---|---|
| Qual serviço | do alerta, ou pergunte |
| Desde quando | `datadog_get_monitors` com `group_states=["alert"]` |
| É novo? | o mesmo monitor já disparou antes hoje? |

```
datadog_get_monitors(group_states=["alert", "warn"])
datadog_get_monitor(monitor_id=<id do alerta>)
```

Se vários monitores estão em alerta ao mesmo tempo, **pare e reconsidere**: um
incidente sistêmico se investiga de cima para baixo (o que é comum entre eles),
não serviço por serviço.

### Fase 2 — Confirmar

Confirme que o sinal é real antes de investir na causa. Alerta não é incidente:
monitor mal calibrado dispara sozinho.

```
datadog_aggregate_logs(
  filter={"query": "service:<serviço> status:error", "from": "now-2h", "to": "now"},
  compute=[{"aggregation": "count", "type": "timeseries"}],
)
```

Compare com o mesmo intervalo de ontem ou da semana passada. Se o volume atual
está dentro da variação normal, diga isso — "o monitor disparou mas o volume
está normal para o horário" é uma conclusão legítima e economiza horas.

### Fase 3 — Ampliar

Agora sim, entenda a natureza da falha.

**Prefira `datadog_error_tracking_search_issues` a buscar logs crus.** As
ocorrências já vêm agrupadas por assinatura, com contagem e primeira/última
aparição — é a diferença entre ler 4.000 linhas e ler 3 causas.

```
datadog_error_tracking_search_issues(query="service:<serviço>", from_="now-2h")
```

Se o sintoma é **lentidão** e não erro, vá para spans:

```
datadog_apm_search_spans(query="service:<serviço>", from_="now-2h")
datadog_apm_aggregate_spans(
  query="service:<serviço>", from_="now-2h",
  compute=[{"aggregation": "pc95", "metric": "@duration"}],
  group_by=[{"facet": "@resource_name", "limit": 10}],
)
```

> Se `build_unified_timeline` estiver disponível **e** o usuário mencionar uma
> sessão ou um cliente específico, pare esta fase e passe para a skill
> `sre-user-impact`. A timeline unificada responde "o que aconteceu" melhor do
> que a soma destas buscas — e já traz o lado do usuário.

### Fase 4 — Datar

A pergunta que mais frequentemente resolve o incidente: **o que mudou?**

```
datadog_get_events(start=<epoch>, end=<epoch>)          # deploys, alertas
servicenow_search_change_requests(start_after=..., start_before=...)
```

Amplie a janela para trás bem além do início da falha. Um deploy costuma
preceder o sintoma em minutos ou dezenas de minutos — se você olhar só a janela
do erro, não o encontra.

**Correlação temporal não é causalidade.** Um deploy antes da falha é indício
forte, não prova. Diga "coincide com" e não "foi causado por", a menos que
tenha evidência adicional.

### Fase 5 — Concluir

Entregue quatro coisas, nesta ordem:

1. **O que quebrou** — serviço, sintoma, desde quando
2. **Alcance** — quantos erros, quais rotas, está piorando ou estável
3. **Hipótese** com nível de confiança, e o que a sustenta
4. **Próximo passo** — uma ação concreta, não uma lista

Se `analyze_incident_timeline` estiver disponível e a investigação envolver
uma sessão, ela produz esse veredito estruturado a partir da timeline.

## Armadilhas

**Não conclua causa a partir de correlação temporal sozinha.** É a falha mais
comum. Procure ao menos um sinal adicional: o erro cita o componente alterado?
o horário bate no minuto? o rollback resolveu?

**Não confunda sintoma com causa.** "Timeout no gateway" quase nunca é a causa
— é o que aconteceu enquanto a causa acontecia mais abaixo. Siga a cadeia até
um componente que falhou por conta própria.

**Não busque `status:error` cru quando o Error Tracking existe.** Você vai ler
4.000 ocorrências da mesma exceção.

**Não pergunte o que dá para descobrir.** Serviço, janela e monitor
normalmente estão no alerta que o usuário colou. Pergunte só o que
permanece ambíguo depois de olhar.

## Quando parar e escalar

Passe para `sre-user-impact` quando:

- a pergunta virar "quantos clientes foram afetados?"
- o sintoma for de experiência (tela travada, botão que não responde) e não de
  infraestrutura
- você tiver a causa mas precisar dimensionar o dano para decidir severidade

Passe para `sre-postmortem` quando o incidente estiver mitigado e a pergunta
for de registro.
