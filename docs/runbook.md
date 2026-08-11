# Runbook de operação

Este documento é para quem está **operando** o `mcp-unified` quando ele quebra —
não para quem o desenvolve (isso é o `AGENTS.md`) nem para quem o instala numa
IDE (isso é o `docs/ide-setup.md`).

A organização é por **assinatura de falha**: a mensagem, o código de status ou o
sintoma que você tem na mão. Vá direto à seção correspondente; não há ordem de
leitura.

O foco é a topologia HTTP descrita em [`arquitetura.md`](arquitetura.md#como-agente-autônomo--http-não-stdio)
— processo de vida longa, compartilhado, consumido por um agente. Onde o
comportamento no stdio difere, está dito.

## Índice de assinaturas

| Assinatura | Seção |
|---|---|
| `sources_failed` não vazio na resposta de correlação | [Timeline incompleta](#timeline-incompleta-sources_failed-não-vazio) |
| `credencial inválida ou expirada (401)` | [401 do provedor](#401-do-provedor) |
| `acesso negado (403)` / `permissão insuficiente (403)` | [403 do provedor](#403-do-provedor) |
| `rate limit atingido e retentativas esgotadas` | [429 do provedor](#429-do-provedor) |
| `Limite de chamadas excedido. Aguarde ~Ns` | [Rate limit do próprio servidor](#rate-limit-do-próprio-servidor) |
| `timeout após 4 tentativas` | [Timeout do provedor](#timeout-do-provedor) |
| `falha de rede: ...` | [Timeout do provedor](#timeout-do-provedor) |
| Tool não aparece na lista do cliente | [Provedor desabilitado](#provedor-desabilitado-tool-sumiu) |
| Cliente MCP não conecta; 401 antes de qualquer tool | [401 do próprio servidor](#401-do-próprio-servidor-autenticação-jwt) |
| `token rejeitado: ...` no log | [401 do próprio servidor](#401-do-próprio-servidor-autenticação-jwt) |
| Handshake falha no stdio; JSON inválido | [stdout contaminado](#stdout-contaminado-só-no-stdio) |
| Healthcheck do container falhando | [Healthcheck](#healthcheck-do-container-falhando) |
| Resultado vazio, sem erro nenhum | [não é este runbook](#o-que-este-runbook-não-cobre) |

## O primeiro minuto

Antes de abrir qualquer seção, três comandos que respondem "o que está no ar":

```bash
# 1. O que o servidor acha que tem — sem subir nada, sem tocar em rede.
mcp-unified --list-tools --profile sre-agent

# 2. Por que um provedor está de fora (a coluna de motivo vem do ctx.disabled).
mcp-unified --list-tools 2>&1 | sed -n '/provedores:/,/^$/p'

# 3. O processo está vivo e falando MCP?
curl -sS -o /dev/null -w '%{http_code}\n' http://localhost:8080/mcp
```

O `--list-tools` é a sonda mais barata que existe aqui: ele constrói o servidor
inteiro — lê credenciais, resolve toolsets, registra provedores — e sai sem
abrir socket nenhum (`__main__.py:101`). Um provedor marcado com `·` em vez de
`✓` já responde metade das perguntas deste runbook.

---

## Timeline incompleta (`sources_failed` não vazio)

**Sintoma.** Uma tool de correlação responde com sucesso, mas o envelope traz
`sources_failed` preenchido e a timeline tem menos fontes do que deveria.

**Isto é comportamento correto, não bug.** `gather_entries` consulta todas as
fontes em paralelo e uma que estoure vira entrada em `sources_failed` em vez de
derrubar a chamada (`correlation/timeline.py:29-31`). A decisão está registrada
como invariante: uma timeline incompleta e declarada é melhor que nenhuma
timeline — e muito melhor que uma silenciosamente incompleta.

**Confirmar.** O valor de cada chave em `sources_failed` é a mensagem da exceção
original. Ela já diz qual é o problema:

| Mensagem contém | Vá para |
|---|---|
| `(401)` | [401 do provedor](#401-do-provedor) |
| `(403)` | [403 do provedor](#403-do-provedor) |
| `rate limit atingido` | [429 do provedor](#429-do-provedor) |
| `timeout após` / `falha de rede` | [Timeout do provedor](#timeout-do-provedor) |

**Ação.** Trate a causa na seção correspondente. **Não** reexecute a correlação
esperando resultado diferente: as fontes que funcionaram já responderam, e o
custo de repetir é pago nas quatro APIs.

**O que não fazer.** Não interprete uma fonte ausente como "não houve evento
nessa fonte". É a confusão mais cara possível num incidente — `sources_failed`
existe exatamente para tornar essa diferença visível, e ignorá-lo devolve o
problema que ele resolve.

---

## 401 do provedor

**Sintoma.** `credencial inválida ou expirada (401)` (`http.py:135-141`).

**Causa.** A credencial chegou ao provedor e foi recusada. Quase sempre é uma
das três: chave rotacionada e não atualizada aqui; credencial certa apontando
para o ambiente errado (sandbox × produção); ou — o caso silencioso — o
servidor está lendo uma credencial de origem diferente da que você editou.

**Confirmar a origem antes de mexer na credencial.** A precedência é
**ambiente > cofre > arquivo > default** (`config.py`, `settings_customise_sources`),
e ela **inverte o padrão do `pydantic-settings`** de propósito. O sintoma clássico:
você atualizou o `.env`, mas há `/run/secrets` montado e o cofre ganha; ou há
variável de ambiente no container e ela ganha de tudo.

```bash
# Qual origem está realmente valendo?
docker compose exec mcp-unified sh -c 'ls /run/secrets 2>/dev/null; env | grep -c DD_'
```

**Ação.** Corrija na origem que está ganhando, não na que você editou por
hábito. Depois, reinicie: as credenciais são lidas uma vez, na construção do
`Settings` (`__main__.py:90`) — não há recarga a quente.

**Rotação sem janela de indisponibilidade.** Escreva o novo segredo em
`/run/secrets/<NOME>`, suba uma réplica nova, drene a antiga. O nome do arquivo
precisa bater exatamente com o nome da variável (`/run/secrets/DD_API_KEY`, não
`datadog-api-key`) — está anotado no `docker-compose.yml:54`.

**Verificar.** `mcp-unified --list-tools` deve mostrar `✓` no provedor. Um `✓`
só prova que a credencial *existe*; para provar que ela *funciona*, chame uma
tool de leitura barata do provedor.

---

## 403 do provedor

**Sintoma.** `acesso negado (403)` ou `permissão insuficiente (403)`.

**A diferença que importa: 401 é identidade, 403 é escopo.** A credencial está
correta e foi aceita; o que falta é permissão. Reemitir a chave não resolve —
resolve conceder o escopo.

Cada provedor sobrescreve a mensagem com a causa mais provável, e vale ler a
mensagem literal antes de investigar:

| Provedor | O que quase sempre é | Onde |
|---|---|---|
| Datadog | App Key sem escopo de leitura. A API Key sozinha permite **enviar**, não ler. | `providers/datadog/client.py:37` |
| Microsoft Graph | `Sites.Read.All` (SharePoint) ou `ChannelMessage.Read.All` (Teams), esta com consentimento de administrador | `providers/msgraph/client.py:38` |
| ServiceNow | ACL de leitura em `incident` / `change_request` para o usuário configurado | `providers/servicenow/client.py:40` |
| FullStory | escopos de leitura de sessão/usuário em Settings > API Keys | `providers/fullstory/client.py:48` |

**O caso do Teams merece tratamento próprio.** `ChannelMessage.Read.All` é
permissão de aplicação ampla, passa por revisão de segurança e consentimento de
administrador, e historicamente leva **semanas** (`PLAN.md:512`). Se as tools de
SharePoint funcionam e só as de Teams dão 403, o diagnóstico está fechado: não é
configuração, é um pedido de permissão pendente. A ação certa é degradar
explicitamente — remova o toolset `msgraph` do perfil em uso ou aceite as duas
tools de Teams falhando em `sources_failed` — e escalar o pedido, não continuar
investigando o código.

**Verificar.** Depois da concessão, propagação no Azure AD não é instantânea;
espere e repita antes de concluir que não funcionou.

---

## 429 do provedor

**Sintoma.** `rate limit atingido e retentativas esgotadas` (`http.py:156-162`).

**Leia com atenção: "esgotadas".** O `429` está em `_RETRYABLE_STATUS`
(`http.py:23`), então o cliente já tentou 4 vezes com backoff exponencial antes
de levantar esse erro. Você não está vendo o primeiro 429 — está vendo o quarto.
Repetir a chamada à mão não vai ajudar.

**O backoff que já rodou.** `min(2^tentativa, 30s)` mais jitter de até 0,5 s, e
`Retry-After` numérico é respeitado quando maior que o cálculo
(`http.py:109-119`). Para os padrões: ~1 s, ~2 s, ~4 s — cerca de 7 s de espera
acumulada antes de desistir. `Retry-After` em formato de data HTTP é ignorado de
propósito; nesse caso vale só o backoff calculado.

**Causa mais comum no modo HTTP.** Um consumidor em laço queimando a cota da API
que os outros também usam. É o item 3 da lista de "o que muda ao sair do stdio"
(`arquitetura.md:171`).

**Ação, em ordem:**

1. Identifique o consumidor. Ingestão de histórico longo é a suspeita padrão —
   ela precisa de paginação com backoff, não de rajada.
2. Reduza a concorrência na origem. Aumentar `MCP_HTTP_MAX_RETRIES` só estica a
   espera; não cria cota.
3. Se for legítimo e recorrente, o problema é de cota contratada, não de código.

**Cuidado com o Graph.** Ele aplica throttling agressivo com `429` +
`Retry-After`. O `BaseApiClient` respeita, mas ingestão de três anos de Teams
não cabe numa janela curta por mais retentativa que se configure.

---

## Rate limit do próprio servidor

**Sintoma.** `Limite de chamadas excedido. Aguarde ~Ns antes de chamar
novamente. Ajuste com RATE_LIMIT_TOOL_MAX_REQUESTS.`

**Não confunda com o anterior.** Esta mensagem vem do `SecurityMiddleware`
(`security/middleware.py:51`) — o limite é **seu**, não do provedor, e nenhuma
API externa foi tocada. A chamada foi rejeitada antes de sair.

**Parâmetros.** `RATE_LIMIT_TOOL_MAX_REQUESTS` (padrão 60/min, `config.py:194`) e
`RATE_LIMIT_ENABLED`. É token bucket com reabastecimento contínuo: o `~Ns` da
mensagem é o tempo real até haver token, não uma janela fixa.

**A chave do bucket é a sessão** (`middleware.py:64-72`): `id(session)` quando
existe, `"global"` quando não. Em stdio há uma sessão só, então o limite é
efetivamente global — que é o desejado no uso local. Em HTTP, cada sessão MCP
tem seu próprio bucket.

**Duas armadilhas de operação:**

- **O bucket é em memória** (`security/rate_limit.py:1-5`). Com N réplicas atrás
  de um balanceador, o limite efetivo é N × 60/min, não 60/min. Não há Redis
  nesta versão — a interface está pronta, a implementação não.
- **Reiniciar zera todos os buckets.** Um restart é, na prática, um reset de
  limite. Isso ajuda no incidente e atrapalha na medição.

**Ação.** Se o consumidor é legítimo e o limite é o gargalo, suba
`RATE_LIMIT_TOOL_MAX_REQUESTS` e reinicie. Se o consumidor está em laço,
consertar o laço é mais barato que subir o limite.

---

## Timeout do provedor

**Sintoma.** `timeout após 4 tentativas` ou `falha de rede: <detalhe>`.

**A diferença entre os dois é diagnóstica, e vale usá-la:**

- `timeout após 4 tentativas` — houve conexão, a resposta é que não veio a tempo.
  O provedor está lento ou degradado. As 4 tentativas já aconteceram
  (`http.py:90-97`).
- `falha de rede: ...` — **não houve retentativa nenhuma.** Erros de HTTP que não
  são timeout são levantados na primeira ocorrência (`http.py:98-99`), porque
  DNS quebrado ou TLS recusado não melhora tentando de novo. Isto aponta para a
  sua rede, não para o provedor: egress bloqueado, DNS, proxy, certificado.

**Parâmetros.** `MCP_HTTP_TIMEOUT` (padrão 30 s) e `MCP_HTTP_MAX_RETRIES`
(padrão 3, que dá 4 tentativas — `config.py:205-206`).

**Ação.** Para `falha de rede`, teste a conectividade de dentro do container, não
da sua máquina:

```bash
docker compose exec mcp-unified python -c \
  "import httpx; print(httpx.get('https://api.datadoghq.com/api/v1/validate', timeout=5).status_code)"
```

Para `timeout após`, verifique o status do provedor antes de aumentar
`MCP_HTTP_TIMEOUT`. Aumentar o timeout num provedor degradado transfere a
lentidão para quem chama: cada tool passa a segurar a resposta por mais tempo, e
numa timeline com quatro fontes em paralelo o efeito é o pior tempo, não a média.

---

## Provedor desabilitado (tool sumiu)

**Sintoma.** Uma tool que existia não aparece mais na lista do cliente, ou o
agente relata que a ferramenta não existe.

**Causa.** Falta de credencial **desabilita, não derruba** — é invariante do
projeto (`AGENTS.md:97`). O provedor é marcado via `ctx.disable(nome, motivo)`
(`providers/registry.py:67`) e o motivo fica registrado para aparecer nas
respostas de correlação.

**Confirmar.**

```bash
mcp-unified --list-tools --profile sre-agent
```

A seção `provedores:` mostra `·` e o motivo ao lado. Não há ambiguidade aqui:
ou a credencial não foi encontrada, ou o toolset não está no perfil.

**As duas causas, e como distinguir:**

| Observação | Causa | Ação |
|---|---|---|
| `· provedor — <motivo>` | credencial ausente | veja a origem da credencial em [401](#401-do-provedor) |
| `✓ provedor` mas a tool não está na lista | toolset fora do perfil, ou `--safe-mode` | ajuste `--profile` / `--toolsets` |

**`--safe-mode` remove todo toolset de escrita**, independente do perfil
(`toolsets.py:119`). Se as tools de leitura estão lá e só as de escrita sumiram,
é isso — e provavelmente é intencional: `arquitetura.md:169` recomenda
`--safe-mode` no modo HTTP, e o `AGENTS.md:321` mantém ServiceNow e Graph em
read-only até que haja revisão específica.

---

## 401 do próprio servidor (autenticação JWT)

**Sintoma.** O cliente MCP não conecta; `401` antes de qualquer tool; no log do
servidor, `token rejeitado: <motivo>` (`security/oauth.py:67`) ou
`token sem escopos necessários: [...]` (`:74`).

**Este é o único 401 que não é de provedor.** Ele acontece na fronteira HTTP,
antes de o servidor tocar em qualquer API externa.

**O motivo já está no log.** O `verify_token` registra a exceção do `jwt.decode`,
que nomeia a causa: assinatura inválida, expirado, `aud` errado, `iss` errado.

**A causa mais comum, e a mais fácil de errar:** `aud` incorreto. O token é
validado contra `MCP_SERVER_CANONICAL_URI` (`oauth.py:62`) e essa validação é
deliberada — é o que impede o ataque de *confused deputy*, em que um token
emitido para outro serviço é reaproveitado contra este. Se o emissor está
colocando outra audiência no token, o servidor **deve** recusar. Corrija no
emissor; não relaxe a validação.

**Diagnóstico rápido.** Decodifique o payload do token (sem verificar) e compare
com a configuração:

```bash
# claims do token que o cliente está enviando
cut -d. -f2 <<<"$TOKEN" | base64 -d 2>/dev/null | python -m json.tool

# o que o servidor exige
docker compose exec mcp-unified sh -c \
  'env | grep -E "MCP_AUTH_|MCP_SERVER_CANONICAL_URI"'
```

Confira, nesta ordem: `aud` × `MCP_SERVER_CANONICAL_URI`; `iss` ×
`MCP_AUTH_SERVER_URL`; `exp` × agora; `scope` × `MCP_AUTH_REQUIRED_SCOPES`.

**Erro de configuração na subida.** Com `MCP_AUTH_ENABLED=true`, faltar
`MCP_SERVER_CANONICAL_URI` ou `MCP_AUTH_JWKS_URL` derruba o servidor no boot com
mensagem explícita (`oauth.py:29-35`) — falha ruidosa, de propósito: um servidor
que sobe sem validar audiência aceitaria tokens de outros serviços.

**JWKS.** O `PyJWKClient` busca as chaves do emissor. Se o JWKS estiver
inacessível a partir do container, todo token é rejeitado — e o sintoma é
idêntico ao de token inválido. Se as claims batem e mesmo assim é recusado,
teste o alcance do `MCP_AUTH_JWKS_URL` de dentro do container.

---

## stdout contaminado (só no stdio)

**Sintoma.** Handshake falha na IDE; o cliente reclama de JSON inválido; a
conexão cai logo após abrir.

**Causa.** No stdio, o stdout **é** o canal do protocolo. Um `print` de
depuração, um aviso de biblioteca ou qualquer escrita fora do protocolo corrompe
o fluxo. É a invariante nº 1 do projeto (`AGENTS.md:64`): nada vai para o stdout
além do protocolo — log vai para stderr.

**Não afeta o modo HTTP.** Ali o protocolo trafega no socket e o stdout é só log.

**A exceção legítima.** `--list-tools` escreve em stdout de propósito, porque o
servidor não vai subir (`__main__.py:113`).

**Ação.** Reproduza fora da IDE, onde o stderr fica visível:

```bash
mcp-unified --profile ide --env-file .env --log-level DEBUG
```

Se houver saída que não seja JSON-RPC no stdout, é essa a causa.

---

## Healthcheck do container falhando

**Sintoma.** O container reinicia em laço ou fica `unhealthy`.

**O que o healthcheck testa.** Um GET em `http://localhost:8080/mcp`, passando
se o status for `< 500` (`docker-compose.yml:34`). Isso prova **liveness** — o
processo subiu e está atendendo — e mais nada.

**O que ele não prova, e isso importa num incidente:** não valida credencial, não
verifica provedor, não testa alcance das APIs externas. Um container `healthy`
com as quatro credenciais erradas é um estado perfeitamente possível. Para saúde
funcional, use `--list-tools`.

**Falha na subida.** Duas causas derrubam o processo cedo, ambas com mensagem
clara em stderr: caminho inválido em `--env-file` / `--secrets-dir`, que sai com
código 2 (`__main__.py:82-87`), e configuração de autenticação incompleta
(seção acima). Comece pelo log:

```bash
docker compose logs --tail=50 mcp-unified
```

**`start_period` é 10 s.** Se a subida legitimamente demorar mais no seu
ambiente, o healthcheck mata o container antes de ele ficar pronto e o sintoma
parece outra coisa.

---

## O que este runbook não cobre

**Resultado vazio sem erro.** Uma tool que responde com sucesso e devolve zero
registros não é falha de operação — é a pergunta ou a configuração de
identidade. Isso é o território da skill
[`sre-setup`](../skills/sre-setup/SKILL.md), que existe justamente porque a
falha nº 1 deste servidor é configuração e ela se disfarça de "não houve
impacto". Não duplique aquele diagnóstico aqui.

**Investigação de incidente no sistema-alvo.** As cinco skills de
[`skills/`](../skills/README.md) cobrem triagem, impacto no usuário, impacto no
negócio e post-mortem. Este runbook é sobre o `mcp-unified` estar de pé, não
sobre o que ele revela.

**Desenvolvimento.** Invariantes, como adicionar provedor ou tool, e o que é
regressão: [`AGENTS.md`](../AGENTS.md).

---

## Nota sobre o formato

As seções acima são indexadas por assinatura de falha porque é assim que a
informação chega em um incidente — você tem a mensagem, não o nome do
subsistema. Se este runbook algum dia for consumido por um agente em vez de por
uma pessoa, o passo natural é quebrar cada seção em um arquivo próprio
(`401.md`, `403.md`, `429.md`, `timeout.md`), que é a convenção que agentes de
plantão usam para localizar o procedimento a partir da assinatura. Enquanto o
leitor for humano, um arquivo com índice é mais fácil de manter coerente.
