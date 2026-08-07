# Skills de SRE para o mcp-unified

Quatro skills que ensinam um agente a **usar bem** as 73 tools do servidor. Elas
não adicionam capacidade — adicionam julgamento: qual tool para qual pergunta,
e quais armadilhas evitar.

## A divisão é por pergunta, não por provedor

| Skill | Responde | Fontes |
|---|---|---|
| [`sre-triage`](sre-triage/SKILL.md) | "o monitor disparou, o que está acontecendo?" | Datadog |
| [`sre-user-impact`](sre-user-impact/SKILL.md) | "quem foi afetado e o que a pessoa viveu?" | correlação + FullStory |
| [`sre-postmortem`](sre-postmortem/SKILL.md) | "como registro isso para a próxima vez?" | ServiceNow, SharePoint/Teams |
| [`sre-setup`](sre-setup/SKILL.md) | "por que voltou vazio?" · "o que está configurado?" | diagnóstico |

Dividir por provedor — uma skill "Datadog", outra "FullStory" — seria repetir a
fragmentação que o servidor existe para eliminar. A correlação é o produto;
ela não pertence a nenhum dos dois lados.

`sre-setup` é a exceção de forma: não responde uma pergunta de investigação, e
sim a pergunta que vem *antes* dela. Existe porque a falha nº 1 deste servidor
é configuração — e ela se disfarça de resultado vazio, que parece "não houve
impacto".

## Instalação

As skills vivem no repositório. Para usá-las com o Claude Code, copie para o
diretório de skills do projeto ou do usuário:

```bash
mkdir -p .claude/skills
cp -r skills/sre-* .claude/skills/
```

Elas assumem que o servidor `mcp-unified` está registrado. Ver
[README.md](../README.md#registro-na-ide).

## Convenções seguidas

Herdadas de [datadog-labs/agent-skills](https://github.com/datadog-labs/agent-skills),
[softaworks/agent-toolkit](https://github.com/softaworks/agent-toolkit) e
[BagelHole/DevOps-Security-Agent-Skills](https://github.com/BagelHole/DevOps-Security-Agent-Skills):

- **Detecção de disponibilidade no topo** — a skill confere quais tools existem
  antes de propor um caminho, porque credencial ausente é o caso comum aqui.
- **`description` com frases-gatilho literais** — é o que faz a skill disparar
  na hora certa.
- **Modo anunciado, nunca perguntado** — a skill decide e informa, em vez de
  interromper com pergunta.
- **Metodologia em fases nomeadas** — dá ao agente um esqueleto para seguir sob
  pressão.
- **Divulgação progressiva** — o detalhe fica em `references/`, carregado só
  quando necessário.
