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

## Como testar localmente

Cinco níveis, do que funciona sem nada até o uso real.

### 1. Suíte de testes — sem credencial, sem rede

```bash
.venv/bin/pytest -q          # 84 testes
.venv/bin/ruff check src/ tests/
```

Todas as respostas HTTP são interceptadas por `respx` e o provedor de modelo é
um dublê. Nada sai da máquina.

### 2. Inventário de tools — sem subir o servidor

```bash
mcp-unified --profile ide --list-tools
```

Mostra os toolsets ativos, quantas tools foram registradas, quais fontes de
timeline existem e **por que** cada provedor ausente está desabilitado.

### 3. Modo demo — exercita a correlação de verdade, sem conta nenhuma

Um servidor HTTP local serve os mesmos fixtures dos testes nos caminhos que a
FullStory, o Datadog e o ServiceNow usariam. O cliente faz requisições reais
contra ele; o que muda é só para onde aponta.

```bash
# terminal 1
.venv/bin/python scripts/demo_upstream.py

# terminal 2
source scripts/demo.env
.venv/bin/mcp dev src/mcp_unified/server.py      # inspetor interativo
```

Ou direto, sem inspetor:

```python
import asyncio
from mcp import Client
from mcp_unified.server import build_server

async def main():
    async with Client(build_server(profile="all")) as c:
        r = await c.call_tool("build_unified_timeline",
                              {"user_id": "dev-77", "session_id": "sess-pix-1"})
        out = (r.structured_content or {})["result"]
        for e in out["timeline"]:
            print(f"{e['ts'][11:19]}  {e['source']:<16} {e['summary'][:50]}")

asyncio.run(main())
```

Saída esperada — a narrativa que justifica o projeto:

```
14:30:00  fullstory        navigate: /pix/transferir
14:31:00  fullstory        click: Confirmar transferência
14:31:01  datadog-spans    ⚠ [servico-transferencia] POST /api/pagamentos/tra…
14:31:09  datadog-logs     ⚠ [servico-transferencia] timeout ao consultar SPI
14:31:30  fullstory        ⚠ mouse_thrash: Confirmar transferência
```

> 💡 O deploy que causou a falha aconteceu **antes** da sessão começar, então
> não cabe na janela padrão. Aumente a folga para encontrá-lo:
> `padding_seconds=900` traz o `Deploy servico-transferencia v2.14` às 14:22.
> Esse é o uso normal de `padding_seconds`.

### 4. Na IDE

```bash
claude mcp add mcp-unified -- /caminho/para/.venv/bin/mcp-unified --profile ide
```

Ou o `.mcp.json` da seção acima. Depois, `/mcp` no Claude Code confirma que as
tools apareceram. Para experimentar sem conta real, use o modo demo: aponte o
`env` do `.mcp.json` para `http://127.0.0.1:8931` com as variáveis
`FULLSTORY_BASE_URL` e `DD_BASE_URL`.

### 5. Com credenciais reais

O mínimo para a correlação funcionar são quatro variáveis:

```bash
FULLSTORY_API_KEY=...   FULLSTORY_ORG_ID=...    # sessão e replay
DD_API_KEY=...          DD_APP_KEY=...          # logs (as duas são necessárias)
```

Depois valide com uma sessão que você conheça:

```bash
mcp-unified --profile ide --log-level DEBUG
```

O critério de aceite é o round-trip: rode `build_unified_timeline` numa janela
de falha conhecida, confirme que frontend e backend aparecem intercalados na
ordem certa, e então rode `find_sessions_for_incident` na mesma janela —
a sessão original precisa voltar, com link de replay válido.

### Docker

```bash
docker build -t mcp-unified .
docker run -i --rm --env-file .env mcp-unified --profile ide --list-tools
```

Para verificar o protocolo de ponta a ponta dentro do container:

```bash
printf '%s\n' \
 '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"t","version":"1"}}}' \
 '{"jsonrpc":"2.0","method":"notifications/initialized"}' \
 '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' \
 | docker run -i --rm --env-file .env mcp-unified --profile ide --log-level ERROR
```

## Skills de SRE

Três skills em [`skills/`](skills/) ensinam um agente a usar bem as 73 tools —
qual escolher para qual pergunta, e quais armadilhas evitar. A divisão é por
**pergunta**, não por provedor:

| Skill | Responde |
|---|---|
| [`sre-triage`](skills/sre-triage/SKILL.md) | "o monitor disparou, o que está acontecendo?" |
| [`sre-user-impact`](skills/sre-user-impact/SKILL.md) | "quem foi afetado e o que a pessoa viveu?" |
| [`sre-postmortem`](skills/sre-postmortem/SKILL.md) | "como registro isso para a próxima vez?" |

```bash
mkdir -p .claude/skills && cp -r skills/sre-* .claude/skills/
```

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
