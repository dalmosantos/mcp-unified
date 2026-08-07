# Plano: `mcp-unified` — camada unificada de dados operacionais em Python

## Contexto

Existem dois MCP servers separados, ambos em JS/TS, que respondem a metades da mesma pergunta:

- **[fs-lexicon](https://github.com/fullstorydev/fs-lexicon)** — o MCP é uma subpasta (`MCP/`) de uma plataforma maior de webhooks. Expõe **30 tools FullStory** (v1+v2) sobre `api.fullstory.com` com auth `Basic`, transporte HTTP via Express, mais OAuth 2.1, rate limiting e validação de input. Responde *"o que o usuário fez na tela"*.
- **[datadog-mcp-server](https://github.com/GeLi2001/datadog-mcp-server)** — **10 tools** read-only (monitors, dashboards, metrics, events, incidents, logs), transporte stdio, sobre o SDK oficial em TS. Responde *"o que o backend fez"*.

Usá-los separados obriga quem investiga a copiar timestamps e IDs de um para o outro na mão.

### Dois consumidores, não um

Este servidor tem **dois clientes previstos**, e isso é o que define a arquitetura:

1. **A IDE, hoje** — investigação interativa via stdio. Superfície enxuta, contexto é recurso escasso.
2. **O Agente Autônomo de SRE** (ver `sre-agente-autonomo.md`) — um agente L1 que classifica alertas do Datadog cruzando 3 anos de histórico. Ele precisa das mesmas fontes, mais ServiceNow, SharePoint e Teams.

O agente de SRE, como estava desenhado originalmente, ia escrever o próprio cliente de API para cada fonte. Isso duplicaria retry, backoff, tratamento de 429, sanitização de PII e validação — tudo que este servidor já vai ter. **A decisão é que o MCP seja a camada de dados do agente**, não um projeto paralelo.

```
┌──────────────────────────────────────────────────────────────┐
│  Consumidores                                                 │
│                                                               │
│   IDE (stdio)              Agente SRE (HTTP)                 │
│   investigação             RAG · classificação · notificação  │
│   interativa                                                  │
└─────────┬────────────────────────┬───────────────────────────┘
          │                        │
          └────────┬───────────────┘
                   ▼
     ┌─────────────────────────────────────┐
     │  mcp-unified                        │
     │  tools · correlação · segurança     │
     └──┬────────┬────────┬────────┬───────┘
        ▼        ▼        ▼        ▼
   FullStory  Datadog  ServiceNow  MS Graph
                                (SharePoint+Teams)
```

O que **não** entra aqui: vector store, RAG, lógica de classificação, agendamento. Isso é lógica de agente e vive no projeto de SRE. O MCP expõe dados; quem decide é o cliente.

**Resultado esperado:** 73 tools (33 FullStory + 24 Datadog + 6 ServiceNow + 5 MS Graph + 3 correlação + 2 análise) em *toolsets* selecionáveis, para que a IDE carregue 32 e o agente carregue outro recorte.

## Decisões tomadas

| Tema | Decisão |
|---|---|
| Arquitetura | **Reimplementação pura em Python** — sem depender de MCP hospedado de terceiros |
| Cliente HTTP | `httpx` async para todos os provedores, sem SDK proprietário |
| Transporte | `stdio` (IDE) + `streamable-http` (agente SRE) |
| Segurança | Validação de input + rate limiting + OAuth 2.1 |
| Provedores | FullStory · Datadog · ServiceNow · Microsoft Graph (SharePoint + Teams) |
| Correlação | Orientada a **protocolo**, não a provedores nominais — ver abaixo |
| Identidade | Atributo configurável **e** modo temporal, combinados |
| Empacotamento | **Docker** para rodar local (stdio e HTTP), além do venv nativo |
| Modelo via API | Camada opcional e **agnóstica de provedor**, desligada por padrão |

### Sobre custo dos MCPs oficiais

Pesquisado antes de fixar a arquitetura:

- **Datadog MCP** ([docs](https://docs.datadoghq.com/bits_ai/mcp_server/)): não há cobrança separada documentada. É governado por *fair-use limits* ajustáveis via suporte, exige conta Datadog, e os dados consultados já são faturados pelo [pricing normal](https://docs.datadoghq.com/account_management/billing/pricing/). Não suportado em GovCloud.
- **FullStory MCP** (`https://api.fullstory.com/mcp/fullstory`): restrito ao **programa beta**, sem pricing público.

Nenhum dos dois cobra por chamada de MCP, mas ambos adicionam dependência de serviço hospedado e, no caso da FullStory, um gate de beta. Com reimplementação pura paga-se só a assinatura que já existe — as APIs REST têm rate limit, não cobrança por chamada.

### Correlação: identidade **e** tempo

Não são alternativas. O tempo define a **janela**; a identidade define o **filtro** dentro dela. Cada tool de correlação recebe `correlation_mode`:

- `"time"` — só janela temporal (funciona sempre, inclui ruído de outros usuários)
- `"identity"` — janela + filtro `<FS_DD_USER_ATTR>:<uid>` (preciso; vazio se o app não loga esse atributo)
- `"both"` (**padrão**) — tenta identidade; se o atributo não estiver configurado ou o resultado vier vazio, cai para temporal e **marca no output** que houve fallback

---

## O que a pesquisa estabeleceu

### 1. Os 4 domínios novos do Datadog são viáveis via REST público

Confirmado no OpenAPI v2 oficial (`datadog-api-client-python/.generator/schemas/v2/openapi.yaml`), com os métodos exatos:

| Domínio | Endpoints confirmados |
|---|---|
| RUM | `POST /api/v2/rum/events/search`, `POST /api/v2/rum/analytics/aggregate`, `GET /api/v2/rum/applications` |
| Error Tracking | `POST /api/v2/error-tracking/issues/search`, `GET /api/v2/error-tracking/issues/{id}`, `PUT .../{id}/state`, `PUT .../{id}/assignee` |
| APM / spans | `POST /api/v2/spans/events/search`, `POST /api/v2/spans/analytics/aggregate`, `GET /api/v2/spans/events` |
| Product Analytics | `POST /api/v2/product-analytics/analytics/scalar`, `POST .../analytics/timeseries`, `POST .../users/query` |

### 2. Duas capacidades da FullStory não têm REST público — limitação real

O MCP oficial da FullStory (analisado via [fullstory-skills](https://github.com/fullstorydev/fullstory-skills)) tem superfície bem diferente do fs-lexicon:

- **Métricas/segmentos por linguagem natural** (`build_metric`, `compute_metric`) — é uma camada **LLM do servidor hospedado deles**, não um endpoint REST. Não é reimplementável.
  **No lugar:** `list_segments` e `get_segment` (API v1 real, que o fs-lexicon nem usa — ele só tinha *exports*), mais o módulo de analytics portado.
- **Inspeção visual do replay** (`session_open` / `session_view` / `session_diff`) — **não existe API REST pública**. Só via MCP hospedado, no beta.
  **No lugar:** o transcript de eventos com timestamps (`get_session_events`) e a **URL do replay** (`get_session_link`), para o humano abrir no ponto certo.

**Mitigação:** o registro de provedores tem ponto de extensão para *passthrough* — se um dia houver acesso ao beta, dá para plugar um cliente MCP que injeta essas tools sem refatorar o resto.

### 3. Toolsets e perfis

73 tools num só servidor consomem contexto demais numa IDE — a própria Datadog avisa disso na doc deles. Grupos selecionáveis via `--toolsets` / `MCP_TOOLSETS`:

| Toolset | Tools |
|---|---|
| `fullstory-core` | 19 (leitura: sessões, users, analytics, segments, health) |
| `fullstory-write` | 14 (create/update/delete, batch, exports, annotations) |
| `datadog-core` | 10 (monitors, dashboards, metrics, events, incidents, logs) |
| `datadog-rum` | 8 (RUM 4 + Error Tracking 4) |
| `datadog-apm` | 3 (spans) |
| `datadog-product-analytics` | 3 |
| `servicenow` | 6 (incidents, change requests, problems, knowledge — read-only) |
| `msgraph` | 5 (SharePoint docs + mensagens de Teams) |
| `correlation` | 3 |
| `llm` | 2 |

Como os dois consumidores querem recortes diferentes, `--profile` expande para uma lista de toolsets:

| Perfil | Toolsets | Tools |
|---|---|---|
| `ide` (padrão) | `fullstory-core`, `datadog-core`, `correlation` | 32 |
| `sre-agent` | `datadog-core`, `datadog-rum`, `servicenow`, `msgraph`, `fullstory-core`, `correlation` | 51 |
| `all` | tudo | 73 |

`SAFE_MODE=true` força só os grupos de leitura, independente do perfil.

---

## Estrutura do projeto

```
mcp-unified/
├── pyproject.toml               # mcp>=2,<3, httpx, pydantic-settings, pyjwt; extras [llm-anthropic] / [llm-openai]
├── README.md                    # inclui atribuição MIT aos dois projetos originais
├── .env.example
├── LICENSE                      # MIT + NOTICE
├── Dockerfile                   # imagem única, serve stdio e HTTP
├── docker-compose.yml           # modo HTTP + perfil local-llm
├── .dockerignore
└── src/mcp_unified/
    ├── __main__.py              # CLI: --transport, --profile, --toolsets, --port, --safe-mode
    ├── server.py                # monta MCPServer, registra toolsets, middleware, auth
    ├── config.py                # pydantic-settings
    ├── toolsets.py              # registro de grupos e perfis
    ├── protocols.py             # TimelineSource, SubjectResolver — o contrato de extensão
    ├── errors.py
    ├── http.py                  # BaseApiClient httpx: retry, backoff, 429, timeout, mapeamento de erro
    ├── models.py                # TimelineEntry, SessionWindow, Subject, ...
    ├── security/
    │   ├── validation.py        # port do inputValidator.js
    │   ├── rate_limit.py        # token bucket em memória
    │   ├── oauth.py             # TokenVerifier + AuthSettings
    │   └── middleware.py        # ServerMiddleware: rate limit → safe mode → validação
    ├── providers/
    │   ├── registry.py          # provedores se registram como fontes de timeline / subject
    │   ├── fullstory/           # client.py · analytics.py · tools.py   (32 tools)
    │   ├── datadog/             # client.py · tools.py                  (24 tools)
    │   ├── servicenow/          # client.py · tools.py                  (6 tools)
    │   └── msgraph/             # client.py · sharepoint.py · teams.py · tools.py  (5 tools)
    ├── correlation/
    │   ├── window.py            # deriva janela temporal
    │   ├── identity.py          # resolve correlation_mode e monta o filtro
    │   ├── timeline.py          # funde entradas de qualquer TimelineSource registrado
    │   └── tools.py             # 3 tools
    ├── llm/
    │   ├── base.py              # Protocol LLMProvider; nenhum SDK importado aqui
    │   ├── providers/           # anthropic.py · openai.py · openai_compat.py
    │   ├── redact.py            # redação de PII antes de montar o prompt
    │   ├── schemas.py           # modelos Pydantic das saídas estruturadas
    │   └── tools.py             # 2 tools
    └── tests/
```

**Convenção de nomes:** `fullstory_*`, `datadog_*`, `servicenow_*`, `msgraph_*`, `correlate_*` — tudo `snake_case`. As tools do Datadog são **renomeadas** em relação ao servidor original (`get-monitors` → `datadog_get_monitors`): quebra proposital, para o namespace ficar legível com 73 tools.

## Descobertas técnicas que orientam a implementação

1. **O SDK Python do MCP é o `mcp` 2.0.0** — `pip install mcp` já traz 2.x. A API é `from mcp.server import MCPServer` (**não** `FastMCP`, que era v1), e o JSON Schema vem dos type hints: não se escreve schema à mão como no fs-lexicon. Pinar `mcp>=2,<3`.
2. **OAuth 2.1 é nativo**: `MCPServer(auth=AuthSettings(...), token_verifier=...)` já entrega `.well-known`, bearer auth e audience binding. Não é preciso portar os 14KB de `MCP/auth/mcpAuth.js` — só implementar um `TokenVerifier`.
3. **`ServerMiddleware` existe** (`middleware=[...]`, protocolo `async __call__(ctx, call_next)`): validação e rate limiting entram como middleware único para todas as tools, em vez do dispatcher gigante com `switch` do original.
4. **O `Fullstory.js` não é só cliente HTTP**: `getUserProfile`, `getUserAnalytics` e `getSessionInsights` fazem ~660 linhas de análise client-side (clustering de eventos, fluxo, funil, engajamento). Precisa ser portado como lógica, não como chamada de API.
5. Ambos os originais são **MIT** — derivação permitida, com atribuição.

---

## Fase 1 — Fundação e protocolos de extensão

**Arquivos:** `pyproject.toml`, `config.py`, `http.py`, `errors.py`, `models.py`, `protocols.py`, `toolsets.py`, `providers/registry.py`, `server.py`, `__main__.py`

1. `pyproject.toml` com `requires-python = ">=3.10"` (a máquina tem 3.10.12) e entry point `mcp-unified`. Ambiente com `python3 -m venv .venv` + `pip` (não há `uv` instalado).

2. `config.py` — `pydantic-settings`, um bloco por provedor:
   - FullStory: `FULLSTORY_API_KEY`, `FULLSTORY_ORG_ID`, `FULLSTORY_DATACENTER` (`US`|`EU1`)
   - Datadog: `DD_API_KEY`, `DD_APP_KEY`, `DD_SITE`, `DD_LOGS_SITE`, `DD_METRICS_SITE` — preservando o override por serviço e o `cleanupUrl` que tira o prefixo `https://`
   - ServiceNow: `SNOW_INSTANCE`, `SNOW_AUTH` (`basic`|`oauth2`), credenciais correspondentes
   - Microsoft Graph: `MSGRAPH_TENANT_ID`, `MSGRAPH_CLIENT_ID`, `MSGRAPH_CLIENT_SECRET`
   - Correlação: `FS_DD_USER_ATTR` (padrão `@usr.id`), `FS_DD_CORRELATION_MODE` (padrão `both`), `FS_DD_WINDOW_PADDING_SECONDS` (padrão `60`)
   - Servidor: `MCP_PROFILE`, `MCP_TOOLSETS`, `SAFE_MODE`, `RATE_LIMIT_*`, `MCP_AUTH_*`
   - **Validação preguiçosa por provedor:** faltar credencial de um provedor não derruba o servidor — desabilita o toolset dele e as fontes de correlação que dependem dele, com mensagem clara. Com quatro provedores isso deixa de ser conveniência e vira requisito: quase ninguém terá os quatro configurados.

3. `http.py` — `BaseApiClient` async compartilhado: `AsyncClient` reaproveitado, timeout, retry com backoff exponencial, `429` respeitando `Retry-After`, `204` → `None`, erros HTTP mapeados para exceção comum.

4. **`protocols.py` — o ponto que mantém o projeto extensível.** A correlação não chama provedores nominalmente; ela itera sobre o que estiver registrado:

```python
class TimelineSource(Protocol):
    """Qualquer provedor que saiba produzir eventos numa janela temporal."""
    source_name: str
    async def events_in_window(
        self, window: SessionWindow, *, query: str | None = None, limit: int = 100
    ) -> list[TimelineEntry]: ...

class SubjectResolver(Protocol):
    """Qualquer provedor que saiba mapear uma janela/consulta em identidades afetadas."""
    source_name: str
    async def subjects_in_window(
        self, window: SessionWindow, *, query: str, max_subjects: int = 10
    ) -> list[Subject]: ...
```

   `providers/registry.py` mantém as listas e ignora o que não tiver credencial. Plugar um quinto provedor na timeline passa a ser: implementar `events_in_window` e registrar. Nenhuma tool de correlação muda.

5. `toolsets.py` — cada tool declara seu grupo; resolve `--profile`, `--toolsets a,b`, `all` e cruza com `SAFE_MODE`.

6. `__main__.py` — argparse com `--transport {stdio,streamable-http}` (padrão `stdio`), `--profile`, `--toolsets`, `--host`, `--port`, `--safe-mode`, chamando `mcp.run(transport=...)`.

## Fase 2 — Provedor FullStory (33 tools)

**Arquivos:** `providers/fullstory/{client,analytics,tools}.py`

`client.py` porta o `Fullstory.js`: base URL por datacenter (`api.fullstory.com` / `api.eu1.fullstory.com`), `Authorization: Basic <token>`, e o ID composto `{user_id}:{session_id}` (`_formatSessionId`) usado pelos endpoints de sessão.

| Grupo | Tools | Endpoints |
|---|---|---|
| Session profiles | `get_profile`, `list_session_profiles`, `create_profile`, `update_profile`, `delete_profile` | `GET/POST/DELETE /v2/visit_profile[/{id}]` |
| Sessões | `get_session_events`, `generate_session_context`, `generate_session_summary`, `get_session_insights`, `list_sessions` | `/v2/sessions/{uid}:{sid}/{events,context,summary}`, `GET /v1/sessions?uid=` |
| Users v2 | `create_user`, `get_user`, `update_user`, `delete_user`, `create_users_batch` | `/v2/users[/{id}]`, `/v2/users/batch` |
| Users v1 | `set_user_properties_v1`, `set_user_events_v1`, `get_user_events`, `get_user_pages` | `/users/v1/individual/{uid}/{customvars,customevent,events,pages}` |
| Eventos | `create_event`, `create_events_batch`, `create_annotation` | `/v2/events`, `/v2/events/batch`, `/v2/annotations` |
| Batch jobs | `get_batch_job_status`, `get_batch_job_errors` | `/v2/batch/{jobId}[/errors]` |
| Segments | `create_segment_export`, `get_segment_export_status`, `list_segments`, `get_segment` | `/segments/v1/exports[/{id}]`, `/segments/v1[/{id}]` |
| Settings | `get_recording_block_rules` | `/settings/recording/v1/blocking` |
| Derivadas | `get_user_profile`, `get_user_analytics` | compostas (ver `analytics.py`) |
| Health/link | `health_check`, `get_session_link` | — / monta URL do replay |

`analytics.py` porta a lógica client-side do `Fullstory.js` (linhas ~897–1560): clustering de eventos, análise de fluxo, duração, eventos mais frequentes, funil de conversão, score de engajamento, padrões de comportamento.

**Implementa `TimelineSource`:** eventos de sessão viram `TimelineEntry` com `source="fullstory"`.

**SAFE_MODE:** a lista read-only do original (`SAFE_TOOL_NAMES`) mais `list_segments`/`get_segment`/`get_session_link` forma o `fullstory-core`. Com `SAFE_MODE=true` as demais são **omitidas do `list_tools`**, não só bloqueadas na chamada — o original só bloqueia no dispatch, o que faz o modelo tentar e falhar.

## Fase 3 — Provedor Datadog (24 tools)

**Arquivos:** `providers/datadog/{client,tools}.py`

`httpx` com headers `DD-API-KEY` e `DD-APPLICATION-KEY`, respeitando o override de site por serviço.

**`datadog-core` (10, paridade com o original):**

| Tool | Endpoint |
|---|---|
| `datadog_get_monitors` | `GET /api/v1/monitor` (+ `group_states`, `tags`, `monitor_tags`; `limit` client-side) |
| `datadog_get_monitor` | `GET /api/v1/monitor/{id}` |
| `datadog_get_dashboards` | `GET /api/v1/dashboard` |
| `datadog_get_dashboard` | `GET /api/v1/dashboard/{id}` |
| `datadog_get_metrics` | `GET /api/v1/search?q=` (confirmar na doc ao implementar) |
| `datadog_get_metric_metadata` | `GET /api/v1/metrics/{metric_name}` |
| `datadog_get_events` | `GET /api/v1/events?start=&end=` |
| `datadog_get_incidents` | `GET /api/v2/incidents` |
| `datadog_search_logs` | `POST /api/v2/logs/events/search` (usa `DD_LOGS_SITE`) |
| `datadog_aggregate_logs` | `POST /api/v2/logs/analytics/aggregate` (usa `DD_LOGS_SITE`) |

**`datadog-rum` (8):** `rum_search_events`, `rum_aggregate_events`, `rum_list_applications`, `rum_get_application`, `error_tracking_search_issues`, `error_tracking_get_issue`, `error_tracking_update_issue_state` (PUT), `error_tracking_update_issue_assignee` (PUT).

**`datadog-apm` (3):** `apm_search_spans`, `apm_aggregate_spans`, `apm_list_spans`.

**`datadog-product-analytics` (3):** `product_analytics_scalar`, `product_analytics_timeseries`, `product_analytics_query_users`.

Os schemas aninhados (`filter`, `compute`, `group_by`, `options`) viram modelos Pydantic — o SDK deriva o JSON Schema deles. Preservar o tratamento especial de `403` do original (mensagem explícita sobre permissões de chave): é a falha mais comum do Datadog e a mensagem genérica não ajuda.

**Implementa `TimelineSource`** (logs, RUM, spans, deploys) **e `SubjectResolver`** (aggregate com `group_by` no facet de usuário).

## Fase 4 — ServiceNow e Microsoft Graph (11 tools)

Estes dois provedores existem para o agente de SRE. São **read-only** — no L1 nada é escrito em nenhum dos dois.

### ServiceNow (6 tools)

**Arquivos:** `providers/servicenow/{client,tools}.py`

Table API (`/api/now/table/{table}`), auth Basic ou OAuth2 conforme `SNOW_AUTH`. Encaixa direto no `BaseApiClient`.

| Tool | Tabela / uso |
|---|---|
| `servicenow_search_incidents` | `incident` — filtro por `sys_created_on`, `severity`, `assignment_group`, query encoded |
| `servicenow_get_incident` | `incident/{sys_id}` ou por `number` (`INC0000000`) |
| `servicenow_search_change_requests` | `change_request` — janela temporal; é o que responde *"teve mudança aprovada nessa janela?"* |
| `servicenow_get_change_request` | `change_request/{sys_id}` |
| `servicenow_search_problems` | `problem` — causas raiz recorrentes |
| `servicenow_search_knowledge` | `kb_knowledge` — runbooks e procedimentos |

**Implementa `TimelineSource`** (change requests e mudanças de estado de incidente entram na timeline ao lado dos deploys do Datadog) **e `SubjectResolver`** (usuários afetados declarados no ticket).

**Ressalva importante:** nem todo alerta do Datadog vira ticket no ServiceNow — normalmente só os escalados. O número do ticket é uma chave de junção **incompleta**, e o corpus histórico resultante fica enviesado para incidentes graves. Isso é aceitável para hipótese de causa, mas quem usar esses dados como linha de base de jornadas não-críticas precisa saber. Documentar no README.

### Microsoft Graph — SharePoint + Teams (5 tools)

**Arquivos:** `providers/msgraph/{client,sharepoint,teams,tools}.py`

Um cliente só: as duas fontes são a mesma API (`graph.microsoft.com/v1.0`), mesmo fluxo de auth (client credentials via Azure AD), mesmos rate limits e mesmo tratamento de throttling.

| Tool | Uso |
|---|---|
| `msgraph_search_sharepoint` | busca em post-mortems, runbooks, procedimentos |
| `msgraph_get_document` | conteúdo de um documento por ID |
| `msgraph_search_teams_messages` | busca em conversas de incidente |
| `msgraph_get_channel_messages` | mensagens de um canal numa janela temporal |
| `msgraph_list_sites` | descoberta de sites/canais disponíveis |

**Atenção de escopo:** ler mensagens de Teams exige `ChannelMessage.Read.All` — permissão de aplicação ampla, que passa por revisão de segurança e consentimento de administrador. **Isso é caminho crítico e costuma levar semanas.** Iniciar o pedido antes de escrever qualquer código.

O Graph aplica throttling agressivo com `429` + `Retry-After`; o `BaseApiClient` já respeita isso, mas ingestão de histórico longo precisa de paginação com backoff, não de rajada.

## Fase 5 — Correlação (3 tools) — o núcleo do projeto

**Arquivos:** `correlation/{window,identity,timeline,tools}.py`

`window.py` — dada `user_id`+`session_id`, chama `get_session_events`, extrai min/max dos timestamps, aplica padding, devolve `SessionWindow`.
`identity.py` — implementa o `correlation_mode`; compõe o filtro e sinaliza fallback.
`timeline.py` — **itera sobre os `TimelineSource` registrados**, normaliza para `TimelineEntry(ts, source, kind, summary, raw)` e funde por ordem cronológica. Não conhece FullStory nem Datadog pelo nome.

**Tools:**

1. **`correlate_session_with_logs`** — `(user_id, session_id, query?, correlation_mode?, padding_seconds?, limit?)`
   Deriva a janela → busca logs no intervalo com o filtro composto → devolve logs + a janela usada + o modo efetivo.

2. **`build_unified_timeline`** — `(user_id, session_id, query?, correlation_mode?, sources?, limit_per_source?)`
   Funde numa linha do tempo única, com origem marcada: eventos FullStory + logs, RUM, error tracking, spans e deploys do Datadog + change requests do ServiceNow — todos na mesma janela. `sources` filtra quais fontes registradas participam. É a tool que responde *"conta a história do que aconteceu"*.

3. **`find_sessions_for_incident`** — `(query, from, to, max_users?, correlation_mode?)`
   Direção inversa: consulta os `SubjectResolver` registrados para extrair as identidades afetadas na janela → para cada uma (limitado por `max_users`, padrão 10) busca as sessões FullStory que cruzam a janela. Devolve as sessões **com o link do replay**.
   Exige identidade: em modo `"time"` puro não há como extrair usuários. Sem o atributo configurado, retorna vazio com mensagem explicando o que configurar, não erro genérico.

O fan-out de `find_sessions_for_incident` é limitado para não estourar o rate limit da FullStory.

## Fase 6 — Camada LLM agnóstica (2 tools, opcional)

**Arquivos:** `llm/base.py`, `llm/providers/{anthropic,openai,openai_compat}.py`, `llm/{redact,schemas,tools}.py`

`base.py` define um `Protocol` mínimo e **não importa SDK nenhum**:

```python
class LLMProvider(Protocol):
    async def complete_structured(
        self, *, system: str, prompt: str, schema: type[BaseModelT], effort: str
    ) -> BaseModelT: ...
```

O contrato é *"devolve um modelo Pydantic validado"*. **Como** cada provedor chega lá é problema do adaptador — e é aí que a agnosticidade costuma quebrar, porque saída estruturada é a parte menos padronizada entre APIs:

| Adaptador | Mecanismo de saída estruturada |
|---|---|
| `anthropic` | `messages.parse()` com esquema Pydantic |
| `openai` | `responses.parse()` / `response_format` com `json_schema` |
| `openai_compat` | `json_schema` quando o endpoint suporta; senão modo JSON + validação Pydantic com **um** retry apresentando o erro |

Os SDKs entram como extras opcionais e são importados **preguiçosamente** dentro do adaptador — falta de SDK desabilita aquele provedor, não derruba o servidor. O `openai_compat` fala com qualquer endpoint compatível (Ollama, vLLM, LM Studio, Azure, OpenRouter, Groq) usando só `httpx`.

Configuração: `MCP_LLM_PROVIDER` (`none` por padrão), `MCP_LLM_MODEL`, `MCP_LLM_BASE_URL`, `MCP_LLM_API_KEY`, `MCP_LLM_EFFORT`. Trocar de provedor é mudar env var — nenhuma tool muda.

**Tools:**

1. **`analyze_incident_timeline`** — monta a timeline unificada e devolve um `IncidentAnalysis` validado: `root_cause_hypothesis`, `evidence` (entradas específicas que sustentam a hipótese), `confidence`, `blast_radius`, `recommended_next_step`. Substitui centenas de linhas de contexto por um veredito rastreável.

2. **`nl_to_datadog_query`** — traduz linguagem natural na sintaxe de query do Datadog. Devolve `QueryTranslation` com `query`, `explanation` e `confidence`, e **não executa nada**: quem chama revisa a query e chama a tool de busca. Separação proposital.

**Por que agnóstico importa:** a timeline pode conter CPF e chave PIX. Mandar isso para provedor hospedado é decisão que precisa ser consciente. Com `MCP_LLM_PROVIDER=openai-compat` apontando para um modelo local, o dado **não sai da rede**. Independente do provedor, `redact.py` aplica redação de PII **antes** de montar o prompt.

## Fase 7 — Docker

**Arquivos:** `Dockerfile`, `docker-compose.yml`, `.dockerignore`

Uma imagem serve os dois transportes — o que muda é como se executa.

`Dockerfile`: `python:3.12-slim`, `pip install .`, usuário não-root, `ENTRYPOINT ["mcp-unified"]`, `CMD ["--transport", "stdio"]`.

- **stdio (IDE):** `docker run -i --rm --env-file .env mcp-unified`. O `-i` mantém o stdin aberto; sem ele o servidor sobe e morre.
- **HTTP (agente SRE):** `docker-compose.yml` com porta 8080, `--transport streamable-http --profile sre-agent`, healthcheck em `/health`, `restart: unless-stopped`.
- **Perfil `local-llm`:** serviço Ollama ao lado, com `MCP_LLM_BASE_URL=http://ollama:11434/v1`. Sobe com `docker compose --profile local-llm up`.

Credenciais por `--env-file` ou secrets do compose — **nunca no `Dockerfile`**. O `.dockerignore` exclui `.env`, `.venv/` e `tests/`.

## Fase 8 — Segurança

**Arquivos:** `security/{validation,rate_limit,oauth,middleware}.py`

- **`validation.py`** — port do `inputValidator.js` (32KB): SQL injection, XSS, path traversal, command injection, com sanitização e limites de tamanho/profundidade. A validação de *tipo* já vem do Pydantic — este módulo cobre a camada de **conteúdo malicioso**.
- **`rate_limit.py`** — token bucket em memória, por sessão/token, limites separados para requests HTTP e chamadas de tool. Sem Redis nesta versão; interface pronta para plugar depois.
- **`oauth.py`** — `TokenVerifier` validando JWT (assinatura, `exp`, e **`aud` contra `MCP_SERVER_CANONICAL_URI`** — audience binding que previne confused deputy). Endpoints `.well-known` vêm do SDK. Desligado por padrão; **obrigatório quando o agente SRE consumir por HTTP**.
- **`middleware.py`** — um `ServerMiddleware` encadeando rate limit → SAFE_MODE → validação antes de `call_next`, aplicado a todas as tools.

## Fase 9 — Testes (domínio: instituição financeira)

`pytest` + `pytest-asyncio` + `respx` (mock de httpx, **nenhuma credencial real e nenhuma chamada de rede**) e o `Client` in-memory do SDK para exercitar as tools sem transporte.

Os fixtures modelam um **internet banking**: acesso à home e transferência via **PIX**. Não é decoração — é o cenário que melhor exercita a correlação, porque uma falha de PIX tem sintoma no frontend (cliente travado na confirmação) e causa no backend (timeout no SPI).

```
tests/fixtures/
├── fullstory/
│   ├── session_home_access.json     # login → home → consulta de saldo (caminho feliz)
│   ├── session_pix_success.json     # PIX concluído
│   ├── session_pix_failure.json     # PIX travado: erro na confirmação + rage clicks
│   ├── sessions_list.json
│   └── user_profile.json
├── datadog/
│   ├── logs_home_ok.json
│   ├── logs_pix_errors.json         # timeout ao consultar o SPI
│   ├── rum_events_pix.json
│   ├── error_tracking_pix_issue.json
│   ├── spans_pix_slow.json          # POST /api/pagamentos/transferencia
│   ├── events_deploy.json
│   └── aggregate_logs_by_user.json
└── servicenow/
    ├── change_request_pix.json      # mudança aprovada na janela
    └── incident_pix.json            # INC correspondente
```

Todos os dados são **sintéticos**: CPF, chave PIX, e-mail e valores fictícios.

### Cenários

1. **Acesso à home (caminho feliz)** — janela derivada corretamente, logs de `service:canal-digital` retornam, nenhum erro.
2. **PIX que falha (o teste central)** — `build_unified_timeline` deve intercalar, **nesta ordem**: clique em "Confirmar transferência" (FullStory) → span lento `POST /api/pagamentos/transferencia` (APM) → log `timeout ao consultar SPI` (Logs) → issue do Error Tracking → rage clicks (FullStory).
3. **Mudança como causa raiz** — a mesma janela com ServiceNow registrado: o change request aprovado aparece **antes** da primeira falha, ao lado do deploy do Datadog. Prova que um `TimelineSource` novo entra sem tocar na tool.
4. **Incidente → clientes afetados** — `find_sessions_for_incident` agrega por `@usr.id`, devolve uids, e as sessões voltam com link de replay. Verificar que `max_users` limita o fan-out.
5. **Modos de correlação** — os três modos na mesma sessão; `both` cai para temporal quando o fixture não tem o atributo e **marca o fallback**.
6. **SAFE_MODE** — `fullstory_create_event` e `fullstory_delete_user` não aparecem no `list_tools`. Num contexto financeiro isso importa.
7. **Degradação por credencial ausente** — com ServiceNow e Graph não configurados, o servidor sobe, `list_tools` mostra o recorte disponível, e a timeline usa só as fontes ativas. É o caso mais comum na prática.
8. **Vazamento de dado sensível** — CPF e chave nos payloads: testar que `validation.py` os trata, que não vão para log nem mensagem de erro, e que `llm/redact.py` os remove **antes** de qualquer prompt sair da máquina.

### Cobertura técnica

URL e auth de cada cliente; `429`/`403`/`204`; a lógica de analytics portada (clustering, funil, engajamento — onde bugs de port passam despercebidos); derivação de janela; ordenação da timeline; resolução de perfis e toolsets; e um `LLMProvider` falso implementando o `Protocol`, para testar as 2 tools de análise sem chamar API nenhuma.

---

## Verificação

```bash
cd /home/admin/projects/mcp-unified
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"

# 1. Testes (sem credenciais, sem rede)
.venv/bin/pytest -q

# 2. Contagem de tools por perfil
.venv/bin/python -c "
import asyncio
from mcp import Client
from mcp_unified.server import build_server
async def m(p):
    async with Client(build_server(profile=p)) as c:
        print(p, '->', len((await c.list_tools()).tools))
for p in ('ide', 'sre-agent', 'all'):
    asyncio.run(m(p))
"
# espera: ide -> 32, sre-agent -> 51, all -> 73

# 3. Degradação: sem credencial de ServiceNow o servidor sobe mesmo assim
env -u SNOW_INSTANCE .venv/bin/python -c "..."

# 4. stdio com credenciais reais — uso na IDE
.venv/bin/mcp-unified --profile ide

# 5. Inspector interativo
.venv/bin/mcp dev src/mcp_unified/server.py

# 6. Docker — stdio (o -i é obrigatório)
docker build -t mcp-unified .
docker run -i --rm --env-file .env mcp-unified --profile ide

# 7. Docker — HTTP, o que o agente SRE consome
docker compose up -d && curl -fsS localhost:8080/health

# 8. Docker — com LLM local, sem dado saindo da rede
docker compose --profile local-llm up -d
```

## Como executar na IDE

Registrar em `.mcp.json` na raiz do projeto (ou via `claude mcp add`). Duas formas, ambas stdio:

**A. venv nativo** — mais rápido de iterar:
```json
{ "mcpServers": { "mcp-unified": {
  "command": "/home/admin/projects/mcp-unified/.venv/bin/mcp-unified",
  "args": ["--profile", "ide"],
  "env": { "FULLSTORY_API_KEY": "...", "DD_API_KEY": "...", "DD_APP_KEY": "..." }
}}}
```

**B. Docker** — ambiente isolado e reprodutível:
```json
{ "mcpServers": { "mcp-unified": {
  "command": "docker",
  "args": ["run", "-i", "--rm",
           "--env-file", "/home/admin/projects/mcp-unified/.env",
           "mcp-unified", "--profile", "ide"]
}}}
```

Três detalhes que quebram o registro por Docker, e que o README precisa dizer:
- **`-i` é obrigatório.** Sem ele o stdin fecha e o servidor encerra sem erro útil.
- **`--rm`** evita acumular container morto por sessão da IDE.
- **Nada pode ir para o stdout além do protocolo.** Todo log vai para stderr — um `print()` esquecido corrompe a sessão MCP.

**Teste de aceitação da correlação** (com credenciais reais, contra uma janela conhecida de falha de PIX): rodar `build_unified_timeline` com todas as fontes e confirmar que o clique de confirmação, o span lento, o log de timeout do SPI e o change request aparecem intercalados na ordem certa; depois rodar `find_sessions_for_incident` sobre a mesma janela e confirmar que a sessão original volta, com link de replay válido. Esse round-trip é o critério de que a unificação funcionou.

## Riscos e pontos de atenção

- **`ChannelMessage.Read.All` é caminho crítico.** A permissão de aplicação para ler Teams passa por revisão de segurança e consentimento de administrador — historicamente o item mais lento de projetos assim. Pedir antes de escrever código; se não sair, o provedor `msgraph` fica desabilitado e o resto do sistema segue (a degradação por credencial ausente cobre isso).
- **A análise client-side do FullStory é a parte mais arriscada do port** — ~660 linhas de heurística sem testes no original. Portar com testes próprios; aceitar divergência numérica pequena desde que a forma do output seja igual.
- **Duas capacidades da FullStory não são entregáveis por REST** (métricas por NL e inspeção visual do replay). As substituições estão especificadas; o ponto de extensão para passthrough fica preparado, não construído.
- **A chave de junção do ServiceNow é incompleta** — nem todo alerta vira ticket. Enviesa o corpus para incidentes graves.
- **`GET /api/v1/search?q=` para `get_metrics`** é inferência de qual endpoint o `listMetrics` do SDK TS chama — confirmar na doc ao implementar.
- **Renomear as tools do Datadog** quebra configs do servidor original. Proposital; documentar no README.
- **73 tools é superfície grande.** Perfis e toolsets resolvem o contexto, mas descrições precisas importam mais que o normal.
- **Saída estruturada é o ponto frágil da camada LLM agnóstica.** O adaptador `openai_compat` depende de modo JSON + validação com retry, menos confiável que `json_schema` nativo. Modelos locais pequenos erram mais — testar o modelo escolhido antes de confiar nas 2 tools de análise.
- **Escopo excluído:** o resto do fs-lexicon (webhooks, Slack, Snowflake, BigQuery, Atlassian), as 4 tools de sistema, Redis no rate limiting, escrita em ServiceNow ou Graph, e toda a lógica de agente (vector store, RAG, classificação, agendamento) — essa vive em `sre-agente-autonomo.md`.
