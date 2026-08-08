# AGENTS.md

Instruções para agentes que trabalham **neste** repositório.

Se você está procurando como *usar* o servidor, o [`README.md`](README.md) é o
lugar. Este arquivo é sobre mexer no código.

---

## O que é o projeto

Servidor MCP em Python que unifica quatro fontes de dados operacionais —
FullStory, Datadog, ServiceNow e Microsoft Graph — e expõe tools de correlação
que cruzam a sessão do usuário no frontend com a telemetria de backend.

Duas topologias: **stdio** (seis clientes hoje — Claude Code, VS Code, Copilot,
Windsurf, Antigravity, Devin CLI) e **HTTP**, para o agente autônomo de SRE
(ver [`sre-agente-autonomo.md`](sre-agente-autonomo.md)).

As camadas e o fluxo de uma chamada estão em
[`docs/arquitetura.md`](docs/arquitetura.md); o registro em cada cliente, em
[`docs/ide-setup.md`](docs/ide-setup.md). O [`PLAN.md`](PLAN.md) é histórico:
explica por que cada decisão foi tomada, e tem trechos marcados como superseded.

## Ambiente

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
```

Python ≥ 3.10. Não há `uv` nesta máquina; use `venv` + `pip`.

## Comandos

```bash
.venv/bin/pytest -q                        # 111 testes, sem rede, sem credencial
.venv/bin/ruff check src/ tests/           # lint (deve passar limpo)
.venv/bin/ruff check --fix src/ tests/

.venv/bin/mcp-unified --profile sre-agent --list-tools   # inventário sem subir o servidor
.venv/bin/python scripts/demo_upstream.py                # upstream falso, sem conta
```

Dois flags de credencial, para os dois transportes:

- `--env-file <arquivo>` — stdio, máquina de desenvolvedor. Existe porque cada
  cliente MCP injeta segredo de um jeito, ou de nenhum, e o diretório de
  trabalho do processo lançado é imprevisível.
- `--secrets-dir <dir>` — HTTP. Um arquivo por credencial, nomeado igual à
  variável, como Docker e Kubernetes montam em `/run/secrets`.

**Precedência: ambiente > cofre > arquivo > default.** As duas primeiras
posições são o padrão do `pydantic-settings`; a inversão entre cofre e arquivo
é nossa — ver invariante 8.

Sempre rode `pytest` **e** `ruff` antes de considerar uma mudança pronta.

---

## Invariantes — quebrar qualquer um destes é regressão

### 1. Nada vai para o stdout além do protocolo

No transporte stdio, o stdout **é** o canal MCP. Um `print()` esquecido
corrompe a sessão, e o sintoma é obscuro: o cliente desconecta sem erro útil.

Todo log vai para stderr, via `logging` (já configurado em
`server.configure_logging`). A única exceção é `__main__._print_inventory`,
que só roda com `--list-tools`, quando o servidor não vai subir.

### 2. A correlação não conhece provedor pelo nome

`correlation/` itera sobre quem implementa os protocolos de
[`protocols.py`](src/mcp_unified/protocols.py) — `TimelineSource` e
`SubjectResolver`. É o que permite plugar uma fonte nova sem tocar nas tools.

São três protocolos:

| Protocolo | Quem implementa | Para quê |
|---|---|---|
| `TimelineSource` | FullStory, os 4 do Datadog, ServiceNow | entrar na timeline unificada |
| `SubjectResolver` | Datadog (logs), ServiceNow | responder "quem foi afetado?" |
| `SessionProvider` | FullStory | derivar a janela a partir de uma sessão |

O terceiro existe porque a correlação depende do **conceito** de sessão, não do
produto que a fornece. Sem ele, `correlation/` precisaria escrever
`clients.get("fullstory")`.

Se você precisar nomear um provedor dentro de `correlation/`, pare: ou a
informação pertence a um protocolo, ou pertence ao provedor. Dois testes
guardam isso — `test_correlacao_nao_nomeia_nenhum_provedor` varre o diretório
atrás de nomes literais, e `test_servicenow_entra_na_timeline_sem_alterar_a_tool`
prova que uma fonte nova entra sem alterar as tools.

### 3. Falta de credencial desabilita, não derruba

Com quatro provedores, quase ninguém terá os quatro configurados. Um provedor
sem credencial chama `ctx.disable(nome, motivo)` e sai — o servidor sobe, as
tools dele não são registradas, e o motivo aparece em
`providers_unavailable` nas respostas de correlação.

Nunca levante exceção no `register()` de um provedor por credencial ausente.

### 4. Entrada fora da janela nunca entra na timeline

As consultas mandam `from`/`to`, mas as fontes **também** conferem do lado do
cliente (`_Base._within` em `providers/datadog/sources.py`). Um endpoint que
ignore o filtro faria eventos de outro horário aparecerem como se fossem do
incidente. Uma entrada fora de janela é pior que uma faltando: ela mente.

### 5. O fallback de correlação é sempre declarado

No modo `both`, cair de identidade para tempo é aceitável; silenciar não é.
Sem o aviso, quem consome interpreta ruído de outros usuários como evidência
sobre este usuário. O campo `fallback_reason` do envelope existe para isso.

### 6. As quatro configurações de IDE não divergem

`.mcp.json`, `.vscode/mcp.json`, `.agents/mcp_config.json` e
`.devin/mcp_config.json` registram o mesmo servidor para clientes diferentes.
Todas precisam citar o perfil `sre-agent` e passar `--env-file`.

Divergir aqui falha do pior jeito possível: o servidor sobe, as tools aparecem,
e só faltam algumas — o time descobre no meio de um incidente, não na subida.
`test_config_de_ide_aponta_para_o_perfil_certo` guarda isso.

### 7. Toda skill cabe no perfil que as IDEs carregam

Uma skill que cite tool fora de `sre-agent` passa no teste de existência e
mesmo assim não funciona para ninguém. As exceções são `datadog-apm` e `llm`,
que nem toda conta tem — skills podem citá-los, mas só sob condicional
("se `X` estiver disponível"). `test_skill_cabe_no_perfil_que_as_ides_carregam`
deriva o allowlist dos próprios toolsets, então não há lista de nomes a manter.

### 8. O cofre ganha do `.env`

`settings_customise_sources` em `config.py` inverte a ordem padrão do
`pydantic-settings`, que seria `env > dotenv > cofre`. Não restaure o padrão.

O padrão erra exatamente no caso que o `--secrets-dir` existe para resolver:
um container com `/run/secrets` montado pelo orquestrador e um `.env` esquecido
na imagem usaria o `.env` — credencial errada, sem erro nenhum, e provavelmente
a antiga. Um diretório de segredos só existe quando alguém o montou de
propósito. `test_cofre_ganha_do_env_file` guarda isso.

### 9. Credencial é `SecretStr`, e `reveal()` só no cliente HTTP

Todo campo de credencial em `config.py` é `SecretStr` — `str()` e `repr()`
devolvem `**********`, então log e traceback ficam protegidos por padrão.
`config.reveal()` é o único ponto onde o valor real aparece, e só é legítima
**na fronteira em que a credencial é entregue ao upstream**: montando header
em `providers/*/client.py`, ou passando a chave ao construtor de um provedor
em `llm/base.py`. Precisou dela em qualquer outro lugar — sobretudo perto de
`logger`, de `repr` ou de um payload de resposta — pare e releia.

O preço disso é um erro fácil na direção oposta: interpolar o campo direto numa
f-string manda `**********` para o upstream, e o sintoma é um 403 que parece
problema de permissão. Dois testes guardam os dois lados —
`test_credencial_nao_aparece_em_repr_nem_em_log` e
`test_credencial_chega_intacta_no_header`.

Identificador não é segredo: `org_id`, `instance`, `username`, `client_id` e
`tenant_id` continuam `str`, porque aparecem em URL e em mensagem de erro útil.

---

## Onde as coisas ficam

```
src/mcp_unified/
├── protocols.py       # TimelineSource, SubjectResolver — o contrato de extensão
├── config.py          # pydantic-settings; um bloco por provedor, com .configured
├── http.py            # BaseApiClient: retry, backoff, 429, mapeamento de erro
├── toolsets.py        # grupos e perfis (ide / sre-agent / all)
├── server.py          # monta o MCPServer; a ordem de registro importa
├── providers/
│   ├── registry.py    # ServerContext: clientes, fontes, provedores desabilitados
│   └── <nome>/        # client.py (API) · tools.py (register) · sources.py (protocolos)
├── correlation/       # window · identity · timeline · tools
├── llm/               # base.py (Protocol, sem SDK) · providers/ · redact · schemas
└── security/          # validation · rate_limit · oauth · middleware

skills/                # fonte única das cinco skills
agents/                # subagentes (hoje só session-context)
docs/                  # ide-setup (instalar) · arquitetura (como funciona)

.claude-plugin/        # plugin.json + marketplace.json — empacotamento Claude Code
.mcp.json              # Claude Code
.vscode/mcp.json       # VS Code e GitHub Copilot — chave `servers`, não `mcpServers`
.agents/
├── mcp_config.json    # Antigravity
└── skills/            # LINKS SIMBÓLICOS para skills/ — Devin CLI e Antigravity leem daqui
.devin/mcp_config.json # Devin CLI
```

**`.agents/skills/` é link simbólico, não cópia.** Adicionou uma skill? Crie o
link também, senão ela só existe no Claude Code:
`ln -s ../../skills/<nome> .agents/skills/<nome>`. O
`test_skills_estao_expostas_no_padrao_agents` compara conteúdo, o que pega tanto
o link esquecido quanto um clone Windows sem `core.symlinks` (onde o link vira
um arquivo de texto com o caminho dentro).

**Ordem de registro em `server.py`:** provedores primeiro (registram tools *e*
se anunciam como fontes), correlação e LLM depois — para já enxergarem todas as
fontes disponíveis. Não inverta.

## Como adicionar um provedor

1. `providers/<nome>/client.py` — herde `BaseApiClient`; sobrescreva
   `_forbidden_hint()` com orientação específica do provedor (a mensagem
   genérica de 403 não ajuda ninguém).
2. `providers/<nome>/tools.py` — exponha `register(server, ctx)`. Cheque
   `settings.configured`; se faltar credencial, `ctx.disable(...)` e retorne.
3. Se a fonte tiver eventos com timestamp, implemente `TimelineSource` e
   registre com `ctx.add_timeline_source(...)`. Ela entra na timeline
   automaticamente.
4. Adicione o toolset em `toolsets.py` e inclua nos perfis relevantes.
5. Registre o módulo no laço de `server.build_server`.
6. Atualize as contagens. `test_contagem_de_tools_por_perfil` é a fonte de
   verdade e falha primeiro; os cinco lugares que repetem o número são
   `README.md` (tabela de perfis), `docs/ide-setup.md` (tabela de perfis),
   `PLAN.md` (tabela e bloco de verificação), o docstring de `toolsets.py` e as
   `description` de `.claude-plugin/`.

> **Cuidado ao conferir contagem pelo `--list-tools`.** Ele reporta o que foi
> registrado *neste ambiente*. O toolset `llm` só registra suas 2 tools se o SDK
> do provedor estiver instalado (`pip install -e ".[llm-anthropic]"`), então sem
> ele o perfil `all` mostra 71 em vez de 73 — sem erro nenhum. O teste usa
> `openai-compat`, que não precisa de SDK, e por isso vê as 73.

## Como adicionar uma tool

Funções `async` com type hints e docstring. O SDK deriva o JSON Schema dos
hints; **não escreva schema à mão**.

```python
async def datadog_algo(
    param: Annotated[str, Field(description="o que é")],
    limit: Annotated[int, Field(description="máximo", ge=1, le=100)] = 25,
) -> Any:
    """Primeira linha: o que faz.

    Parágrafo seguinte: **quando** usar, e quando não usar. É isso que
    orienta o modelo a escolher esta tool em vez de outra.
    """
```

Registre com `server.add_tool(fn, name=fn.__name__)` e acrescente a
`ctx.registered_tools`.

---

## Testes

`pytest` + `pytest-asyncio` + `respx`. **Nenhum teste faz chamada de rede nem
usa credencial real.** O provedor de LLM é o dublê `FakeLLMProvider` de
`conftest.py`, que guarda o último prompt — é assim que o teste de vazamento de
PII verifica que a redação aconteceu.

Duas armadilhas conhecidas:

- **O SDK envolve retorno de `dict` em `{"result": ...}`.** Use o helper `_call`
  dos testes de correlação, que já desembrulha.
- **URLs com ID composto precisam de `url__regex`.** O ID de sessão da
  FullStory é `{uid}:{sid}`, então `respx.get("...")` literal não casa.

Ao corrigir um bug, adicione o teste que o teria pego. Os dois bugs corrigidos
até aqui (`http.py` devolvendo `None` em erro sem corpo; entrada fora de janela)
têm teste próprio.

## Skills

`skills/` contém cinco skills que ensinam um agente a *usar* o servidor. Elas
são documentação que o modelo lê como verdade, então:

- **Renomeou uma tool? Atualize a skill.** `tests/test_skills.py` varre cada
  `SKILL.md` atrás de tools citadas e falha se alguma não existir mais.
- **Adicionou uma skill? Duas coisas além do arquivo.** Ela precisa caber no
  perfil `sre-agent` (invariante 7) e ganhar o link em `.agents/skills/`, senão
  fica só no Claude Code — Devin CLI e Antigravity leem de lá.
- A divisão é por **pergunta**, não por provedor. Uma skill "FullStory" isolada
  recriaria a fragmentação que o servidor existe para eliminar.
- `description` no frontmatter é o gatilho — inclua as frases que o usuário
  realmente diria. O teste exige no mínimo 120 caracteres.

## Convenções

- Nomes de tool em `snake_case` com prefixo de provedor: `fullstory_*`,
  `datadog_*`, `servicenow_*`, `msgraph_*`, `correlate_*`.
- Comentário explica **por quê**, não o quê. Se o código já diz o que faz, o
  comentário é ruído.
- Mensagem de erro orienta a ação: diga qual variável configurar, não só que
  algo falhou.
- Documentação e comentários em português; identificadores em inglês.
- `line-length = 100` (ruff).

## Divergências deliberadas do original

O projeto deriva de dois trabalhos MIT (ver [`LICENSE`](LICENSE)). Onde o
comportamento difere de propósito, está anotado no código:

- **`providers/fullstory/analytics.py:_categorize`** — no `Fullstory.js`,
  `'custom'` está no mapa de categorias e a checagem do mapa vem antes da
  lógica por nome, o que torna o ramo de eventos custom **código morto**. A
  ordem foi invertida; sem isso a categoria Transaction ficaria sempre vazia
  em qualquer app que use eventos customizados.
- **Nomes das tools do Datadog** — renomeadas de `get-monitors` para
  `datadog_get_monitors`. Quebra proposital de compatibilidade, por
  necessidade de namespace com 73 tools.

Se for divergir de novo, documente no docstring da função, não só no commit.

## O que este projeto **não** faz

Fora de escopo por decisão, não por falta de tempo:

- Vector store, RAG, classificação de alerta, agendamento — isso é lógica de
  agente e vive no projeto de SRE. O MCP expõe dados; quem decide é o cliente.
- Escrita em ServiceNow ou Microsoft Graph — read-only até que haja revisão
  específica.
- O resto do fs-lexicon: webhooks, Slack, Snowflake, BigQuery, Atlassian.
- Redis no rate limiting (a interface está pronta para plugar).
