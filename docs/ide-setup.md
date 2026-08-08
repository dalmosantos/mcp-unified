# Configuração por IDE

O servidor é MCP puro — qualquer cliente que fale o protocolo funciona. O que
muda entre eles é **onde fica o arquivo de configuração** e **como o arquivo se
chama por dentro**. Esta página cobre as duas coisas para cada IDE do time.

Quatro configurações já estão versionadas no repositório. Quem clona e abre o
projeto não precisa criar nada:

| Arquivo | Cliente que lê sozinho |
|---|---|
| [`.mcp.json`](../.mcp.json) | Claude Code |
| [`.vscode/mcp.json`](../.vscode/mcp.json) | VS Code e GitHub Copilot (modo agente) |
| [`.agents/mcp_config.json`](../.agents/mcp_config.json) | Antigravity (escopo do workspace) |
| [`.devin/mcp_config.json`](../.devin/mcp_config.json) | Devin CLI (escopo do projeto) |

Sobram Windsurf e Devin Desktop, que só têm configuração global, e o Devin na
nuvem, que se configura pela interface. Os três estão cobertos abaixo.

---

## O passo obrigatório: um `.env` e o binário no `PATH`

Nenhuma IDE consegue evitar estes dois. Uma vez por máquina:

```bash
pip install -e .        # coloca `mcp-unified` no PATH
cp .env.example .env    # preencha as credenciais que você tem
```

O `.env` está no `.gitignore` — credencial nenhuma entra no repositório.

**Por que `--env-file` em vez do bloco `env` da configuração.** Cada cliente
injeta segredo de um jeito: o VS Code tem `inputs`, o Claude Code expande
`${VAR}`, o Windsurf não expande nada e o Antigravity só aceita literal. Um
arquivo apontado explicitamente é o único mecanismo que se comporta igual nos
cinco. Também evita o modo de falha silencioso do `.env` implícito: o diretório
de trabalho do processo que a IDE lança é imprevisível, e um `.env` não
encontrado desabilitaria todos os toolsets sem dizer por quê. Com `--env-file`,
um caminho errado falha na hora, com a mensagem certa.

**Relativo ou absoluto — depende do escopo da configuração**, e é por isso que
as versionadas não são todas iguais:

| Escopo | Caminho | Por quê |
|---|---|---|
| projeto (`.mcp.json`, `.agents/`, `.devin/`) | `.env` relativo | o cliente lança o servidor com o diretório de trabalho no raiz do projeto |
| projeto, VS Code | `${workspaceFolder}/.env` | é o único cliente que expande a variável, então dá para ser absoluto de graça |
| global (Windsurf, Devin Desktop) | absoluto, obrigatório | a configuração não pertence a projeto nenhum |

Se as tools aparecem mas tudo volta vazio, é quase sempre isto: o servidor
subiu de outro diretório e não achou o `.env`. Trocar pelo caminho absoluto
resolve, e `--list-tools` confirma antes — ele diz quais provedores ficaram
desabilitados e por quê.

---

## Por IDE

### VS Code e GitHub Copilot

O `.vscode/mcp.json` já está no repositório e vale para os dois — o Copilot em
modo agente lê o mesmo arquivo. Abra o projeto, abra o painel de chat em modo
agente e o servidor aparece. Se não aparecer: `Ctrl+Shift+P` →
**MCP: List Servers** → `mcp-unified` → **Start**.

Só o VS Code aceita `${workspaceFolder}`, então é o único onde o caminho do
`.env` já é absoluto sem você digitar nada.

### Antigravity

O `.agents/mcp_config.json` do repositório é lido no escopo do workspace. Para
valer em todos os projetos, cole o mesmo conteúdo no arquivo global: no painel
lateral do agente, **…** → **MCP Servers** → **Manage MCP Servers** →
**View raw config**. O arquivo fica em `~/.gemini/config/mcp_config.json`
(`%USERPROFILE%\.gemini\config\mcp_config.json` no Windows).

### Windsurf e Devin Desktop

O Devin Desktop **é** a Windsurf renomeada, então esta seção vale para os dois.
Vários caminhos em disco ainda carregam o nome antigo — não é engano.

Não há configuração por projeto: é sempre global. Em **Settings** →
**Windsurf Settings** → **Tools** → **Add Server** → **View Raw Config**, ou
pelo Command Palette em **Configure MCP Servers**. O arquivo fica em
`~/.codeium/windsurf/mcp_config.json`
(`%USERPROFILE%\.codeium\windsurf\mcp_config.json` no Windows). Cole dentro do
`mcpServers`, trocando o caminho pelo do seu clone:

```json
{
  "mcpServers": {
    "mcp-unified": {
      "command": "mcp-unified",
      "args": [
        "--profile", "sre-agent",
        "--env-file", "/caminho/absoluto/para/mcp-unified/.env"
      ]
    }
  }
}
```

O caminho **precisa** ser absoluto aqui: sendo configuração global, o Windsurf
não lança o servidor de dentro do projeto.

### Claude Code

`.mcp.json` já está no repositório; `/mcp` confirma que subiu. É o único cliente
que também carrega as skills de `skills/` e o subagente de `agents/` — via
plugin:

```
/plugin marketplace add dalmosantos/mcp-unified
/plugin install mcp-unified-sre
```

Se o servidor não aparecer, verifique se ele não está desabilitado em
`.claude/settings.local.json` (chave `disabledMcpjsonServers`).

### Devin — são três produtos, e só um roda na nuvem

Confundi-los custa tempo, porque o caminho de configuração é diferente em cada:

| Produto | Onde roda | Configuração |
|---|---|---|
| **Devin CLI** | sua máquina | `.devin/mcp_config.json` — **já está no repositório** |
| **Devin Desktop** | sua máquina | é a Windsurf renomeada; veja a seção acima |
| **Devin** (nuvem) | VM da Cognition | só pela interface, e precisa de HTTP |

**Devin CLI.** Nada a fazer: o [`.devin/mcp_config.json`](../.devin/mcp_config.json)
versionado é o escopo de projeto. Rode `devin` dentro do clone. Para sobrescrever
sem sujar o repositório, use `.devin/mcp_config.local.json`, que já nasce
ignorado pelo git. O escopo de usuário fica em `~/.config/devin/mcp_config.json`
(`%APPDATA%\devin\mcp_config.json` no Windows).

**Devin Desktop.** É a Windsurf de antes do rebranding, então vale a mesma
receita — inclusive o caminho global, que continua carregando o nome antigo:
`~/.codeium/windsurf/mcp_config.json`.

**Devin na nuvem.** Aqui sim o binário teria que existir dentro da VM da
Cognition, e stdio local não alcança. Ou você instala o pacote no snapshot da
máquina, ou expõe o servidor por HTTP num host que o Devin alcance:

```bash
mcp-unified --transport streamable-http --profile sre-agent --port 8080 --env-file /caminho/.env
```

O registro é pela interface, sem arquivo: **Settings** → **MCP Marketplace** →
**Add Your Own**. Antes de expor, leia a seção de segurança do
[`README.md`](../README.md) — o modo HTTP tem autenticação por token e
`SAFE_MODE`, e nenhum dos dois está ligado por padrão. Para qualquer cliente
remoto, considere `--safe-mode`, que remove todos os toolsets de escrita
independentemente do perfil.

---

## As duas formas do arquivo

Se a sua IDE não estiver na lista, ela quase certamente usa uma destas. A
diferença é só o nome da chave de fora:

```jsonc
// Windsurf, Devin Desktop, Devin CLI, Antigravity, Claude Code, Cursor
{ "mcpServers": { "mcp-unified": { "command": "...", "args": [...] } } }

// VS Code e Copilot — chave `servers`, e pede `type`
{ "servers": { "mcp-unified": { "type": "stdio", "command": "...", "args": [...] } } }
```

O `command` e o `args` são idênticos nos dois casos.

---

## As skills fora do Claude Code

As tools são MCP e funcionam em qualquer cliente. As skills de
[`skills/`](../skills/) são outra coisa — ensinam *qual* tool usar para qual
pergunta — e dependem do cliente saber lê-las.

O padrão `.agents/skills/` cobre parte disso, e o repositório já expõe as cinco
por lá, como links simbólicos para `skills/` (uma cópia só, nada para manter em
sincronia):

| Cliente | Lê as skills? | De onde |
|---|---|---|
| Claude Code | sim | `skills/`, via plugin |
| Devin CLI | sim | `.agents/skills/` — também aceita `.devin/skills/` |
| Antigravity | sim | `.agents/skills/` |
| VS Code, Copilot, Windsurf, Devin Desktop | não | — |

Nos que não leem, o servidor funciona igual: você ganha as 54 tools, mas não o
julgamento embutido. `sre-business-impact` é a que mais perde, porque o valor
dela está na tradução para a gerência, não na chamada de tool.

> **Windows:** links simbólicos exigem `git config core.symlinks true` no clone.
> Sem isso, os arquivos em `.agents/skills/` viram texto com o caminho dentro.
> `test_skills_estao_expostas_no_padrao_agents` detecta exatamente esse estado.

---

## Qual perfil escolher

Todas as configurações acima usam `sre-agent` — 54 tools, a união exata do que
as cinco skills de SRE precisam. Vale mesmo para uso interativo, apesar do nome.

| Perfil | Tools | Quando |
|---|---|---|
| `ide` | 32 | contexto apertado; cobre triagem e impacto no usuário, **não** impacto de negócio nem post-mortem |
| `sre-agent` | 54 | padrão das configurações de IDE, e o que as cinco skills exigem |
| `all` | 73 | inspeção e testes; inclui escrita na FullStory e a camada LLM |

Trocar é editar uma palavra no `args`. Para um recorte fora dos perfis, use
`--toolsets fullstory-core,datadog-core,correlation` — tem precedência sobre
`--profile`.

---

## Quando não funciona

Antes de mexer na IDE, confirme que o servidor sobe sozinho. Este comando não
inicia nada — só imprime o que seria registrado, e **por que** cada provedor
ausente está desabilitado:

```bash
mcp-unified --profile sre-agent --env-file .env --list-tools
```

| Sintoma | Causa quase sempre |
|---|---|
| a IDE não lista o servidor | `mcp-unified` não está no `PATH` — use o caminho absoluto do `.venv/bin/mcp-unified` |
| sobe, mas com pouquíssimas tools | `--env-file` apontando para o lugar errado; sem credencial, o toolset não é registrado |
| as tools de funil e conversão não existem | perfil `ide` em vez de `sre-agent` |
| conecta e cai logo depois | algo escreveu no stdout; no stdio o stdout **é** o canal do protocolo |

Para testar sem conta nenhuma, o modo demo do [`README.md`](../README.md) sobe
um upstream falso e exercita a correlação de verdade.
