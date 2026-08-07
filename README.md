# mcp-unified

Servidor MCP que unifica **FullStory**, **Datadog**, **ServiceNow** e **Microsoft Graph**
numa só superfície, com tools de correlação que cruzam a sessão do usuário com a
telemetria de backend.

> Documentação de planejamento: [`PLAN.md`](PLAN.md) · [`sre-agente-autonomo.md`](sre-agente-autonomo.md)

## Por que

FullStory responde *"o que o usuário fez na tela"*. Datadog responde *"o que o backend fez"*.
Usados separados, quem investiga copia timestamps e IDs de um para o outro na mão.
Este servidor faz esse cruzamento como tool.

## Instalação

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
cp .env.example .env    # preencha as credenciais que você tem
```

Nenhum provedor é obrigatório. Faltar credencial de um deles **desabilita aquele
toolset** e a correlação segue com as fontes restantes — o servidor sobe do mesmo jeito.

## Uso

```bash
mcp-unified --profile ide                     # stdio, para a IDE
mcp-unified --transport streamable-http --profile sre-agent --port 8080
mcp-unified --list-tools                      # inspeciona a seleção sem subir o servidor
```

### Perfis

| Perfil | Tools | Toolsets | Para quem |
|---|---|---|---|
| `ide` (padrão) | 32 | `fullstory-core`, `datadog-core`, `correlation` | investigação interativa |
| `sre-agent` | 51 | + `datadog-rum`, `servicenow`, `msgraph` | agente autônomo |
| `all` | 73 | tudo | inspeção e testes |

As contagens valem com **todos** os provedores configurados. Sem credencial, as
tools daquele provedor simplesmente não são registradas.

`SAFE_MODE=true` remove todos os toolsets de escrita, independente do perfil.

## Registro na IDE

`.mcp.json` na raiz do projeto:

```json
{ "mcpServers": { "mcp-unified": {
  "command": "/caminho/para/.venv/bin/mcp-unified",
  "args": ["--profile", "ide"],
  "env": { "FULLSTORY_API_KEY": "...", "DD_API_KEY": "...", "DD_APP_KEY": "..." }
}}}
```

Ou via Docker:

```json
{ "mcpServers": { "mcp-unified": {
  "command": "docker",
  "args": ["run", "-i", "--rm", "--env-file", "/caminho/.env", "mcp-unified", "--profile", "ide"]
}}}
```

Três detalhes que costumam quebrar o registro por Docker:

- **`-i` é obrigatório.** Sem ele o stdin fecha e o servidor encerra sem erro útil.
- **`--rm`** evita acumular um container morto por sessão da IDE.
- **Nada pode ir para o stdout além do protocolo.** Todo log vai para stderr; um
  `print()` esquecido corrompe a sessão MCP.

## Correlação

As três tools de correlação não conhecem provedor por nome — iteram sobre quem
implementa `TimelineSource` / `SubjectResolver` (ver `protocols.py`). Plugar uma
fonte nova é implementar `events_in_window` e registrar.

| Tool | Responde |
|---|---|
| `correlate_session_with_logs` | "o que o backend registrou enquanto o usuário estava nessa sessão?" |
| `build_unified_timeline` | "conta a história do que aconteceu", intercalando todas as fontes |
| `find_sessions_for_incident` | "quais usuários reais foram afetados por esse incidente?" |

`correlation_mode` controla o cruzamento: `time` (só janela), `identity` (janela +
filtro por usuário) ou `both` (tenta identidade, cai para tempo e **avisa** no output).

## Camada LLM (opcional, agnóstica)

Desligada por padrão. `MCP_LLM_PROVIDER` aceita `anthropic`, `openai` ou
`openai-compat` — este último fala com qualquer endpoint compatível (Ollama, vLLM,
LM Studio, Azure, OpenRouter).

Num contexto com dado sensível, apontar `openai-compat` para um modelo local mantém
tudo dentro da rede. Independente do provedor, a redação de PII roda antes de o
prompt sair da máquina.

## Licença

MIT. Deriva de [fs-lexicon](https://github.com/fullstorydev/fs-lexicon) e
[datadog-mcp-server](https://github.com/GeLi2001/datadog-mcp-server), ambos MIT —
ver [`LICENSE`](LICENSE) para a atribuição completa.
