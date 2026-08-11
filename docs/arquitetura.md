# Arquitetura e fluxo

Como o servidor está montado por dentro, e o que acontece entre a pergunta que
você digita e a resposta que volta.

> **Três documentos, três papéis.** Este descreve o que **existe hoje**.
> [`PLAN.md`](../PLAN.md) é o registro das decisões de projeto e do porquê de
> cada uma — histórico, não referência. [`sre-agente-autonomo.md`](../sre-agente-autonomo.md)
> descreve o **consumidor** autônomo que vai chamar este servidor, e é
> documento de produto, não de código. Para instalar, veja
> [`ide-setup.md`](ide-setup.md).

---

## As camadas

```
   você, ou um agente
           │
           │  linguagem natural
           ▼
┌──────────────────────────────────────────────────────────────┐
│  CLIENTE MCP    VS Code · Claude Code · Devin CLI · …        │
│                 decide qual tool chamar, com quais argumentos│
└───────────────────────────┬──────────────────────────────────┘
                            │  JSON-RPC sobre stdio ou HTTP
                            ▼
┌──────────────────────────────────────────────────────────────┐
│  mcp-unified                                                 │
│                                                              │
│  server.py      monta o MCPServer, resolve o perfil          │
│  toolsets.py    quais tools registrar (ide / sre-agent / all)│
│  security/      validação · rate limit · OAuth · middleware  │
│                                                              │
│  ┌─────────────────────────┐   ┌──────────────────────────┐  │
│  │ correlation/            │   │ providers/               │  │
│  │  window   identity      │◄──┤  fullstory   datadog     │  │
│  │  timeline               │   │  servicenow  msgraph     │  │
│  │                         │   │                          │  │
│  │  não conhece provedor   │   │  cada um: client.py      │  │
│  │  por nome — só fala com │   │  tools.py · sources.py   │  │
│  │  protocols.py           │   │                          │  │
│  └─────────────────────────┘   └──────────────────────────┘  │
│                                                              │
│  http.py        retry · backoff · 429 · erro traduzido       │
└───────────────────────────┬──────────────────────────────────┘
                            │  HTTPS
                            ▼
   FullStory · Datadog · ServiceNow · SharePoint + Teams (MS Graph)
```

A seta entre `correlation/` e `providers/` aponta para dentro da correlação de
propósito: os provedores se **anunciam** para ela implementando um protocolo, e
ela nunca escreve o nome de nenhum. É o que permite plugar uma fonte nova sem
tocar em tool alguma.

| Protocolo | Quem implementa | Serve para |
|---|---|---|
| `TimelineSource` | FullStory, 4× Datadog, ServiceNow | entrar na timeline unificada |
| `SubjectResolver` | Datadog (logs), ServiceNow | responder "quem foi afetado?" |
| `SessionProvider` | FullStory | derivar a janela a partir de uma sessão |

---

## Na IDE e no CLI — é o mesmo fluxo

VS Code, Claude Code e Devin CLI conversam com o servidor exatamente do mesmo
jeito: o cliente sobe o processo, fala JSON-RPC pelo stdin/stdout e mata o
processo ao sair. Não há daemon, não há porta, não há estado entre sessões.

```
  ABERTURA — uma vez por sessão do cliente

  cliente                          mcp-unified                  provedores
     │                                  │                            │
     │  spawn: mcp-unified              │                            │
     │  --profile sre-agent             │                            │
     │  --env-file .env                 │                            │
     ├─────────────────────────────────►│                            │
     │                                  │ lê o .env                  │
     │                                  │ resolve o perfil           │
     │                                  │ registra só os toolsets    │
     │                                  │ cujo provedor tem          │
     │                                  │ credencial                 │
     │  ◄── initialize / tools/list ────┤                            │
     │      54 tools                    │                            │
     │                                  │                            │

  USO — a cada pergunta

     │  "o que quebrou na sessão        │                            │
     │   sess-pix-1 do dev-77?"         │                            │
     │                                  │                            │
     │  tools/call                      │                            │
     │  build_unified_timeline          │                            │
     ├─────────────────────────────────►│                            │
     │                                  │  1. janela da sessão       │
     │                                  ├───────────────────────────►│
     │                                  │◄────── 14:30:00 → 14:31:30 │
     │                                  │                            │
     │                                  │  2. todas as fontes,       │
     │                                  │     em paralelo            │
     │                                  ├──────────┬────────┬───────►│
     │                                  │  logs   spans   RUM   SNOW │
     │                                  │◄─────────┴────────┴────────┤
     │                                  │                            │
     │                                  │  3. funde e ordena         │
     │                                  │     por timestamp          │
     │  ◄──── timeline + envelope ──────┤                            │
     │                                  │                            │
     │  o modelo lê e responde          │                            │
     ▼                                  │                            │
```

**Fonte que falha não derrota a chamada.** No passo 2 as consultas rodam sob um
`asyncio.gather`; uma que estoure vira uma entrada em `sources_failed` no
envelope, e a timeline sai incompleta **e declarada**. Timeline parcial com
aviso é melhor que nenhuma timeline — e muito melhor que uma que silenciosamente
omite uma fonte.

### O que muda entre IDE e CLI

Nada no protocolo. A diferença está em **quanto julgamento** o cliente carrega
junto — e é uma diferença grande, porque 54 tools sem orientação é uma escolha
difícil para o modelo:

|  | tools | skills (`skills/`) | subagente |
|---|---|---|---|
| Claude Code | ✅ | ✅ via plugin | ✅ |
| Devin CLI · Antigravity | ✅ | ✅ via `.agents/skills/` | ❌ |
| VS Code · Copilot · Windsurf · Devin Desktop | ✅ | ❌ | ❌ |

Sem skill, o cliente tem as mesmas tools mas nenhuma pista de que
`find_sessions_for_incident` responde "quem foi afetado" e
`build_unified_timeline` responde "conta a história". Tende a chamar a tool mais
óbvia pelo nome, não a certa para a pergunta.

---

## Como agente autônomo — HTTP, não stdio

Mesmo servidor, mesmo código, topologia diferente: um processo de vida longa,
compartilhado, que não pertence a ninguém.

```
   alerta do Datadog
         │
         ▼
┌────────────────────────┐
│  agente de SRE         │   vector store · RAG · classificação
│  (sre-agente-autonomo) │   debounce · circuit breaker · Teams
└───────────┬────────────┘
            │  MCP sobre streamable-http  (porta 8080)
            │  ← aqui passa a rede: token + SAFE_MODE importam
            ▼
┌────────────────────────┐
│  mcp-unified           │   um processo, N clientes
│  --transport           │   rate limit por cliente
│    streamable-http     │
└───────────┬────────────┘
            ▼
     as quatro fontes
```

Quatro coisas mudam ao sair do stdio, e nenhuma é opcional em produção:

1. **Autenticação.** No stdio, quem consegue rodar o binário já tinha o `.env`.
   Em HTTP, a porta é a fronteira — ligue a verificação de token (`security/oauth.py`).
2. **`--safe-mode`.** Remove todo toolset de escrita, independente do perfil. Um
   agente que investiga sozinho não precisa poder alterar issue no Datadog.
3. **Rate limit compartilhado.** Um cliente em laço consome a cota da API que os
   outros também usam.
4. **`--secrets-dir` no lugar do `--env-file`.** Um `.env` vira variável de
   ambiente do container, e variável de ambiente aparece em `docker inspect` e
   no dump de um processo. Segredo montado como arquivo, não.

### De onde vêm as credenciais

```
   variável de ambiente          ← ganha de todos
          ▲
   --secrets-dir  /run/secrets/DD_API_KEY      um arquivo por credencial
          ▲                                     (Docker · Kubernetes)
   --env-file     .env                         texto plano, só em dev
          ▲
   default do código
```

A ordem entre cofre e arquivo **inverte o padrão do `pydantic-settings`**, e é
de propósito. No padrão o `.env` ganharia, e o caso que o cofre existe para
resolver quebraria em silêncio: um container com `/run/secrets` montado pelo
orquestrador e um `.env` esquecido na imagem usaria o `.env` — credencial
errada, sem erro nenhum, e provavelmente a antiga. A inversão vive em
`config.py:settings_customise_sources` e está travada por
`test_cofre_ganha_do_env_file`.

Variável de ambiente continua no topo porque é o override explícito, e é como
Kubernetes e as IDEs injetam.

---

## Onde tocar para estender

| Quero… | Mexo em | E ganho de graça |
|---|---|---|
| adicionar uma fonte de dados | `providers/<nome>/` + `toolsets.py` | entra na timeline se implementar `TimelineSource` |
| adicionar uma tool | `providers/<nome>/tools.py` | schema derivado dos type hints |
| mudar o recorte de tools | `toolsets.py` | nada mais; as configurações de IDE só citam o perfil |
| trocar o provedor de LLM | variável `MCP_LLM_PROVIDER` | redação de PII antes de qualquer prompt sair |

O passo a passo de cada um está em [`AGENTS.md`](../AGENTS.md), junto com os
invariantes que não podem ser quebrados — o principal deles sendo que **nada
pode ir para o stdout além do protocolo**, porque no stdio o stdout *é* o canal.
