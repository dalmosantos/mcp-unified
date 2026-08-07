---
name: sre-setup
description: Diagnostica e configura o servidor quando algo não funciona como esperado — descobre qual atributo de identidade os logs realmente carregam, verifica o que está configurado, e explica por que uma tool devolveu vazio. Use quando o usuário disser "não retornou nada", "não achou nenhum cliente", "está vazio", "não está funcionando", "quais provedores estão configurados", "como descubro o FS_DD_USER_ATTR", ou quando uma correlação vier sem resultado sem erro aparente. Distingue falha de configuração de ausência real de dados.
license: MIT
metadata:
  version: "1.0"
  repository: https://github.com/dalmosantos/mcp-unified
  tags: sre,configuração,diagnóstico,troubleshooting,onboarding
---

# Configuração e Diagnóstico

A falha nº 1 deste servidor não é código — é configuração. E ela se disfarça de
resultado vazio, que parece "não houve impacto" quando na verdade é "não sei
procurar".

## A distinção que esta skill existe para fazer

| Sintoma | Interpretação errada | Causa real frequente |
|---|---|---|
| `find_sessions_for_incident` vazio | "ninguém foi afetado" | `FS_DD_USER_ATTR` não bate com o log |
| timeline sem entradas de uma fonte | "não houve nada" | provedor sem credencial |
| timeline sem mudanças | "nada mudou" | janela curta demais |
| correlação traz outros clientes | "esse cliente sofreu tudo isso" | caiu para modo temporal |

**Nunca reporte ausência de dado sem antes descartar configuração.** Um
post-mortem que diz "nenhum cliente afetado" quando o atributo estava errado é
pior que não ter post-mortem.

## Fase 1 — O que está no ar

Comece sempre por aqui; é barato e evita investigar o problema errado.

Toda resposta de correlação traz `providers_unavailable`. Se estiver
preenchido, aquele provedor não tem credencial — e as tools dele não existem
na sua lista.

```
fullstory_health_check()
```

Confirma conectividade e credencial da FullStory. Para os demais, a ausência
das tools `datadog_*`, `servicenow_*` ou `msgraph_*` na sua lista já é a
resposta.

| Falta | Variáveis |
|---|---|
| FullStory | `FULLSTORY_API_KEY`, `FULLSTORY_ORG_ID` |
| Datadog | `DD_API_KEY` **e** `DD_APP_KEY` (as duas — a API Key sozinha só envia) |
| ServiceNow | `SNOW_INSTANCE` + usuário/senha ou client id/secret |
| Teams e SharePoint | `MSGRAPH_TENANT_ID`, `MSGRAPH_CLIENT_ID`, `MSGRAPH_CLIENT_SECRET` |

Para Teams especificamente: se as credenciais existem mas a busca dá 403, o
problema é a permissão `ChannelMessage.Read.All` no Azure AD, que exige
consentimento de administrador. É configuração de tenant, não do servidor.

## Fase 2 — Descobrir o atributo de identidade

Esta é a fase que resolve a maior parte dos "voltou vazio", e ela é
**executável**: não é preciso perguntar ao time como eles logam usuário; dá
para descobrir.

A ideia: agregar logs por cada faceta candidata e ver qual delas de fato tem
valores.

```
datadog_aggregate_logs(
  filter={"query": "service:<serviço>", "from": "now-24h", "to": "now"},
  compute=[{"aggregation": "count", "type": "total"}],
  group_by=[{"facet": "@usr.id", "limit": 5}],
)
```

Repita para os candidatos comuns, um por chamada:

| Faceta | De onde costuma vir |
|---|---|
| `@usr.id` | convenção padrão do Datadog (o padrão do servidor) |
| `@user.id` | variação frequente |
| `@usr.email` | quando a identidade é o e-mail |
| `@customer_id`, `@client_id` | convenções internas |
| `@context.user.id` | logs estruturados aninhados |

**Leitura do resultado:**

- **Grupos com valores** → é esta a faceta. Se for diferente de `@usr.id`,
  instrua o usuário a definir `FS_DD_USER_ATTR=<faceta>` e reiniciar o servidor.
- **Lista vazia em todas** → os logs não carregam identidade de usuário. Isso
  não se corrige por configuração: é instrumentação da aplicação. Diga isso
  claramente — `find_sessions_for_incident` não vai funcionar até que o time
  adicione o atributo aos logs.

**O valor precisa casar com o uid da FullStory.** Não basta existir uma faceta
de usuário; ela tem que conter o mesmo identificador que a FullStory usa. Se o
Datadog loga `@usr.id: 4471` e a FullStory conhece o usuário como
`cliente-4471`, a correlação por identidade nunca vai casar — e o modo `both`
vai silenciosamente cair para temporal. Compare um valor de cada lado antes de
declarar sucesso.

## Fase 3 — Diagnosticar um resultado vazio

Fluxo, na ordem — pare no primeiro que explicar:

1. **A tool existe na sua lista?** Não → provedor sem credencial (Fase 1).
2. **A resposta tem `hint`?** `find_sessions_for_incident` explica ali o motivo.
   Leia antes de teorizar.
3. **`fallback_reason` está preenchido?** Então a busca por identidade falhou e
   o resultado é da janela inteira. Vá para a Fase 2.
4. **A janela está certa?** Confira contra o horário do alerta. Para achar a
   causa (deploy, mudança), amplie: `padding_seconds=900` ou mais. A causa
   quase sempre precede a sessão.
5. **A query está certa?** Teste isolada com `datadog_search_logs` antes de
   culpar a correlação. Nome de serviço errado é comum.
6. **Só então** conclua que não houve dado.

## Fase 4 — Confirmar que ficou bom

O teste de aceitação é um round-trip, não uma chamada isolada:

1. `build_unified_timeline` numa sessão conhecida com falha → frontend e
   backend aparecem intercalados na ordem certa
2. `find_sessions_for_incident` na mesma janela → a sessão original volta, com
   link de replay válido

Se o passo 2 não devolve a sessão do passo 1, a identidade não está casando
entre os dois lados — volte à Fase 2.

## Ajustes que resolvem a maioria dos casos

| Variável | Quando mexer |
|---|---|
| `FS_DD_USER_ATTR` | a faceta descoberta na Fase 2 é outra |
| `FS_DD_WINDOW_PADDING_SECONDS` | a causa fica sistematicamente fora da janela |
| `FS_DD_CORRELATION_MODE` | o app não loga identidade → `time` evita o fallback repetido |
| `DD_LOGS_SITE` | logs em site diferente do padrão da conta |
| `FULLSTORY_DATACENTER` | conta na UE → `EU1` |
| `SAFE_MODE` | o agente não deve poder escrever em produção |

## Quando o problema não é configuração

Diga claramente, em vez de continuar tentando:

- **Logs sem identidade de usuário** — instrumentação da aplicação
- **`ChannelMessage.Read.All` negada** — política do tenant
- **Sessão não existe na FullStory** — fora da retenção, ou gravação bloqueada
  (`fullstory_get_recording_block_rules` mostra as regras)
- **Identificadores incompatíveis entre os dois lados** — decisão de produto
  sobre como identificar usuário; não há configuração que reconcilie

Nesses casos, o valor da sua resposta é dizer o que precisa mudar e em qual
sistema — não continuar procurando um ajuste que não existe.
