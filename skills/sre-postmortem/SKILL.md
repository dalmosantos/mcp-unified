---
name: sre-postmortem
description: Monta o registro de um incidente já mitigado — linha do tempo verificada, causa raiz, impacto dimensionado e ações. Busca incidentes semelhantes no histórico antes de escrever, para não redescobrir o que já foi documentado. Use quando o usuário disser "escreve o post-mortem", "documenta esse incidente", "isso já aconteceu antes?", "monta a timeline do incidente", "quais foram as ações", ou pedir um resumo depois que a mitigação terminou.
license: MIT
metadata:
  version: "1.0"
  repository: https://github.com/dalmosantos/mcp-unified
  tags: sre,postmortem,incidente,servicenow,documentação
---

# Post-mortem

Registrar o incidente de forma que a próxima pessoa não precise reinvestigar.

## Regra que orienta tudo aqui

**Busque antes de escrever.** A maior parte do valor de um post-mortem não é o
documento novo — é descobrir que existem três anteriores sobre a mesma causa, e
que a ação corretiva de um deles nunca foi executada.

## Disponibilidade

- `servicenow_*` — chamados e mudanças históricas
- `msgraph_*` — post-mortems e runbooks já escritos
- `build_unified_timeline` — a linha do tempo verificável

Nenhuma é obrigatória, mas diga o que falta: um post-mortem escrito sem acesso
ao histórico é um post-mortem que provavelmente duplica outro.

## Metodologia

**Recuperar → Verificar → Dimensionar → Comparar → Escrever**

### Fase 1 — Recuperar

Junte o que já existe sobre este incidente:

```
servicenow_get_incident(identifier="INC0000123")
servicenow_search_change_requests(start_after=..., start_before=...)
```

### Fase 2 — Verificar

**Cada afirmação da timeline precisa de uma entrada que a sustente.** Esta é a
diferença entre um post-mortem e uma história plausível.

> ⚠️ **Quem escreveu a narrativa é mau juiz da própria precisão.** Depois de
> montar a cronologia, você já acredita nela — reler o próprio texto encontra
> muito menos erro do que deveria. Se o runtime permitir, faça a verificação
> num contexto separado: entregue o documento e as saídas brutas das tools, e
> peça para conferir cada afirmação contra a evidência, sem ter visto a
> redação acontecer. Sem essa possibilidade, o substituto é mecânico —
> percorra afirmação por afirmação e aponte a entrada que a sustenta; o que
> não tiver, marque como estimado.

```
build_unified_timeline(user_id=..., session_id=..., padding_seconds=1800)
```

Padding largo aqui, de propósito: no post-mortem você quer o contexto completo,
incluindo o que veio antes da falha. Diferente da triagem, latência não importa.

Ao escrever a cronologia, cite a origem de cada marco. Se um horário não tem
entrada que o comprove, marque como estimado — não o apresente como fato.

### Fase 3 — Dimensionar

```
find_sessions_for_incident(query=..., from_=..., to=...)
datadog_aggregate_logs(
  filter={"query": ..., "from": ..., "to": ...},
  compute=[{"aggregation": "count", "type": "total"}],
)
```

Prefira "47 clientes com pelo menos uma falha na confirmação" a "impacto alto".
Número com unidade e janela; adjetivo sozinho não sobrevive à próxima reunião.

Se a contagem veio com `fallback_reason` preenchido, **diga isso na
metodologia** do documento. Um número derivado da janela inteira, e não do
filtro por cliente, é um limite superior — não a contagem exata.

### Fase 4 — Comparar

A fase que quase todo mundo pula, e a que mais paga:

```
servicenow_search_problems(query="<termo da causa>")
msgraph_search_sharepoint(query="post-mortem <serviço> <sintoma>")
msgraph_search_teams_messages(query="<termo do incidente>")
```

Três perguntas ao histórico:

1. **Já aconteceu?** Se sim, este é o enésimo — e isso muda a severidade da
   ação corretiva.
2. **Qual foi a ação da última vez?** Se foi executada e o problema voltou, a
   ação estava errada. Se não foi executada, o problema é de acompanhamento.
3. **Existe runbook?** Se existe e não foi seguido, a lacuna é de descoberta,
   não de conhecimento.

> ⚠️ **O histórico do ServiceNow é enviesado.** Nem todo alerta vira chamado —
> normalmente só os escalados. A ausência de incidente anterior **não** prova
> que é a primeira vez; prova que é a primeira vez que escalou. Diga isso ao
> afirmar ineditismo.

### Fase 5 — Escrever

Estrutura mínima:

```markdown
# INC…  —  <título em uma linha>

**Impacto:** <quem, quantos, por quanto tempo>
**Duração:** <detecção> → <mitigação>  (<total>)
**Severidade:** <nível> — <por quê>

## O que aconteceu
<Dois parágrafos, em português, para quem não estava lá.>

## Cronologia
| Horário | Evento | Fonte |
|---|---|---|
| 14:22 | Deploy … | datadog-events / CHG… |
| 14:31 | Primeira falha … | datadog-logs |

## Causa raiz
<A causa, não o sintoma. Se a investigação não chegou lá, diga isso
explicitamente em vez de nomear o sintoma como causa.>

## Por que demorou a detectar / resolver
<Sobre o processo, não sobre pessoas.>

## Incidentes relacionados
<O que a Fase 4 encontrou, ou "nenhum encontrado — ver ressalva".>

## Ações
| Ação | Tipo | Responsável | Prazo |
|---|---|---|---|
| … | prevenir / detectar / mitigar | | |
```

## O que faz um post-mortem prestar

**Causa raiz, não sintoma.** "Timeout no gateway" é o que apareceu; a causa é o
que fez o componente abaixo parar de responder. Se a investigação não chegou
lá, escreva "causa não determinada" — é honesto e sinaliza trabalho pendente.
Nomear o sintoma como causa encerra a investigação prematuramente.

**Ação classificada por tipo.** Prevenir (não acontece de novo), detectar
(descobrimos mais cedo) e mitigar (dói menos quando acontecer) são investimentos
diferentes. Um post-mortem só com ações de "prevenir" costuma ser otimista
demais.

**Sem culpa individual.** "O deploy não tinha teste de integração" é acionável;
"fulano esqueceu" não é — e garante que a próxima pessoa esconda o erro.

**Linguagem de fato separada de linguagem de hipótese.** "O deploy coincidiu
com o início das falhas" e "o deploy causou as falhas" são afirmações
diferentes, com ônus de prova diferentes.

## Ressalvas de método a declarar

Se qualquer destas se aplicar, registre no documento — quem ler daqui a seis
meses precisa saber o que a análise não viu:

- Correlação caiu para janela temporal (`fallback_reason` preenchido)
- Algum provedor estava indisponível (`providers_unavailable`)
- A contagem de afetados é limite superior, não exata
- O histórico do ServiceNow cobre só incidentes escalados
