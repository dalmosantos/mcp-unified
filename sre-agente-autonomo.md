# Agente Autônomo de SRE — Da Operação Reativa à Inteligência Proativa

> **Audiência:** times de Produto e Engenharia avaliando automação de operações
> **Objetivo:** Descrever uma arquitetura de referência para um agente de SRE nível L1, embasada no paper de IA do Google SRE, aproveitando ativos de dados que a maioria das organizações já possui (ITSM, observabilidade, base de conhecimento, chat).
> **Versão 2.0** — revisada para corrigir promessas de métrica, antecipar a linha de base e reaproveitar a camada de dados do `mcp-unified`.
>
> ⚠️ **Documento de referência.** Nomes de serviço, identificadores de incidente, volumes e valores são **ilustrativos**, não dados de nenhuma organização real. Ao adaptar, substitua pelos seus.

---

## O problema que estamos resolvendo

Hoje, quando um sistema falha, o processo é manual e reativo:

1. O Datadog dispara um alerta
2. Alguém no time recebe a notificação
3. Essa pessoa abre o ServiceNow, consulta o histórico no SharePoint, busca conversas antigas no Teams
4. Formula uma hipótese de causa e age

**O custo disso é mensurável.** Cada minuto de indisponibilidade tem impacto direto em conversão, NPS e receita. E o conhecimento sobre como resolver cada tipo de incidente está disperso em quatro sistemas — e na cabeça das pessoas.

> *"Enquanto os assistentes de codificação com IA aceleram dramaticamente a geração e o ritmo de implantação de código, as práticas manuais tradicionais estão se tornando insustentáveis."*
> — Google SRE, **AI Engineering Reliable Operations** (2025)

---

## O que é SRE e por que isso importa para Produto

**Site Reliability Engineering (SRE)** é a disciplina que garante que os sistemas funcionem de forma confiável para os usuários. Nasceu no Google há mais de 20 anos e hoje é padrão da indústria.

Para o time de Produto, SRE se traduz em três métricas que afetam diretamente o negócio:

| Métrica | O que mede | Este projeto afeta? |
|---|---|---|
| **MTTD** — Mean Time to Detect | Quanto tempo para perceber que algo quebrou | **Não.** Quem detecta continua sendo o Datadog |
| **MTTR** — Mean Time to Resolve | Quanto tempo para resolver após detectar | **Sim.** É o alvo do projeto |
| **Error Budget** | Quanto de instabilidade o SLO permite por mês | Indiretamente, via MTTR menor |

### Sobre MTTD — sendo precisos

A versão anterior deste documento prometia reduzir MTTD. **Isso estava errado e vale corrigir antes de virar meta de time.**

Quem detecta a falha é o monitor do Datadog, e isso não muda. O agente *consome* o alerta que o Datadog já gerou. Pior: por decisão de projeto ele espera de 3 a 5 minutos de debounce antes de analisar, justamente para não reagir a alertas que oscilam. Ou seja, **o agente adiciona alguns minutos entre a detecção e a notificação enriquecida.**

Isso é aceitável porque a notificação crua do Datadog continua chegando no Teams em paralelo, sem atraso. O agente é **aditivo**, não substituto: o time recebe o alerta imediato como sempre recebeu, e minutos depois recebe o contexto que hoje levaria 15 minutos para reunir à mão.

O ganho está inteiramente em **MTTR** — o tempo entre saber que quebrou e saber o que fazer. É onde o trabalho manual está hoje, e é o suficiente para justificar o projeto.

---

## A referência: o que o Google está fazendo

Em 2025, o Google publicou o paper **"AI in SRE: How Google is Engineering the Future of Reliable Operations"**, descrevendo como estão reinventando a operação com agentes de IA.

O paper define cinco **níveis de autonomia** para operações com IA:

```
┌──────────────────────────────────────────────────────────────────┐
│                    Níveis de Autonomia SRE                       │
├──────────┬─────────────┬─────────────┬────────────┬─────────────┤
│  Nível   │  Monitorar  │ Investigar  │  Mitigar   │  Executar   │
├──────────┼─────────────┼─────────────┼────────────┼─────────────┤
│ L0 Manual│  Automação  │   Humano    │   Humano   │   Humano    │
│ L1 Assist│  Automação  │  Automação  │   Humano   │   Humano    │
│ L2 Parcial│ Automação  │  Automação  │   Humano   │  Automação  │
│ L3 Alto  │  Automação  │  Automação  │  Automação │  Automação  │
│ L4 Total │  Automação  │  Automação  │  Automação │  Automação  │
└──────────┴─────────────┴─────────────┴────────────┴─────────────┘

                    📍 Onde estamos hoje: L0
                    🎯 Meta desta iniciativa: L1
```

**Onde estamos:** L0 — tudo é feito manualmente após o alerta.

**Meta imediata:** L1 — o agente investiga e entrega ao humano uma hipótese contextualizada. O humano ainda decide e age.

### Quais números do paper se aplicam a nós

O paper reporta ganhos em várias frentes. **Nem todos correspondem ao que vamos construir**, e misturá-los na apresentação cria expectativa que a entrega não cobre:

| Número do paper | Aplica aqui? |
|---|---|
| **−10% no MTTR** com hipótese automatizada (L1) | **Sim** — é exatamente o escopo deste projeto |
| **+195%** em findings de anomalias | **Não.** Vem de detecção baseada em ML, que não estamos construindo. Consumimos alertas que o Datadog já gerou |
| **−44%** no MTTR com dashboards inteligentes | **Não.** Outra capacidade, fora do escopo L1 |

E uma ressalva sobre o próprio −10%: é o número do Google, no contexto do Google. Serve como referência de ordem de grandeza, não como previsão. **O número que vale é o nosso**, e por isso a Semana 0 existe (ver adiante).

---

## Nosso diferencial: 3 anos de memória institucional

A maioria das iniciativas de IA em operações começa do zero. **Uma organização com histórico de incidentes documentado, não.**

O cenário assumido aqui — e comum em empresas de médio e grande porte — são três anos de incidentes registrados em quatro sistemas que, combinados, formam um dataset difícil de replicar:

```
┌─────────────────────────────────────────────────────────────────────┐
│                    Fontes de dados existentes                        │
│                                                                      │
│  ┌──────────────────┐         ┌──────────────────┐                  │
│  │   SharePoint     │         │  Microsoft Teams  │                  │
│  │                  │         │                   │                  │
│  │ • Post-mortems   │         │ • Chats durante   │                  │
│  │ • Runbooks       │         │   incidentes      │                  │
│  │ • Procedimentos  │         │ • Decisões em     │                  │
│  │   manuais        │         │   tempo real      │                  │
│  └────────┬─────────┘         └─────────┬─────────┘                 │
│           │                             │                            │
│           └──────────────┬──────────────┘                           │
│                          ▼                                           │
│              ┌───────────────────────┐                              │
│              │   Vector Store        │                              │
│              │   Incidente Unificado │                              │
│              │   (memória do agente) │                              │
│              └───────────┬───────────┘                              │
│                          ▲                                           │
│           ┌──────────────┴──────────────┐                           │
│           │                             │                            │
│  ┌────────┴─────────┐         ┌─────────┴────────┐                  │
│  │   ServiceNow     │         │    Datadog        │                  │
│  │                  │         │                   │                  │
│  │ • Tickets        │         │ • Alertas         │                  │
│  │ • Severidade     │         │ • Métricas        │                  │
│  │ • MTTR histórico │         │ • Timestamps      │                  │
│  │ • Responsáveis   │         │   precisos        │                  │
│  └──────────────────┘         └───────────────────┘                 │
└─────────────────────────────────────────────────────────────────────┘
```

O ServiceNow é a **chave de junção**: o número do ticket (`INC0000000`) permite cruzar o alerta do Datadog, o chat do Teams e o post-mortem do SharePoint em um único registro unificado.

Esse conjunto indexado é o que o Google chama de **"Golden Data"** — dados verificados por humanos que formam a base de confiança do agente.

> ⚠️ **A junção é incompleta, e isso é uma característica do dado, não um bug a corrigir.** Nem todo alerta do Datadog vira ticket no ServiceNow — normalmente só os escalados. Incidentes menores ficam sem chave e não entram no registro unificado. Consequência prática: o corpus histórico é **enviesado para incidentes graves**. Isso é ótimo para hipótese de causa em alertas críticos, e ruim como linha de base de jornadas não-críticas. O relatório diário precisa levar isso em conta ao comparar volumes.

---

## O que muda em relação à versão anterior: investigação ao vivo

O desenho original investigava **só o passado** — o RAG busca incidentes similares no histórico e o agente conclui a partir deles. Isso é metade da investigação.

A outra metade é o presente: *quantos clientes reais estão travados neste exato momento, e em qual passo da jornada?* Essa resposta existe no FullStory, que não aparecia no plano anterior, e vem pronta pela camada de dados (ver seção de arquitetura).

A diferença na notificação é substancial:

| Só histórico (versão anterior) | Histórico + ao vivo (esta versão) |
|---|---|
| "Esse padrão ocorreu 8 vezes; 6x foi timeout do parceiro" | "Esse padrão ocorreu 8 vezes; 6x foi timeout do parceiro — **e neste momento 47 clientes estão travados na tela de confirmação do PIX**" |

A primeira dá uma hipótese. A segunda dá hipótese **e** dimensão de impacto — que é o que define se alguém acorda às 3h da manhã.

---

## Arquitetura em duas camadas

O ponto mais importante de revisão nesta versão: **o agente não escreve cliente de API.**

O projeto `mcp-unified` (ver `PLAN.md`) já constrói a camada de acesso a todas as quatro fontes, com retry, backoff, tratamento de rate limit, validação e sanitização. Duplicar isso dentro do agente seria escrever duas vezes o mesmo código, com dois lugares para corrigir bug.

```
┌────────────────────────────────────────────────────────────┐
│  Agente SRE  (este documento)                              │
│                                                             │
│  • Vector store FAISS + RAG sobre 3 anos de histórico      │
│  • Classificação de alertas (crítico / não-crítico)        │
│  • Debounce, deduplicação, circuit breaker                 │
│  • Notificação no Teams · relatório diário                 │
└───────────────────────┬────────────────────────────────────┘
                        │ MCP (HTTP)
                        ▼
┌────────────────────────────────────────────────────────────┐
│  mcp-unified  (PLAN.md)                                     │
│                                                             │
│  Datadog · FullStory · ServiceNow · SharePoint/Teams       │
│  + correlação: timeline unificada, incidente → sessões     │
└────────────────────────────────────────────────────────────┘
```

**O que o agente ganha de graça:**

- Clientes de API dos quatro sistemas, já com retry e backoff
- Correlação ao vivo (`build_unified_timeline`, `find_sessions_for_incident`)
- Camada de LLM agnóstica de provedor, com redação de PII
- Degradação limpa: se a permissão do Teams não sair a tempo, aquele provedor fica desabilitado e o resto funciona

**O que continua sendo do agente:** vector store, RAG, prompt, classificação, agendamento e notificação. Isso é lógica de agente, não superfície de dados.

---

## Como o agente vai funcionar no dia a dia

### Fluxo de um incidente com o agente L1

```
  Datadog detecta anomalia
          │
          ├──────────────► Alerta cru vai para o Teams (imediato, como hoje)
          │
          ▼
  Debounce 3–5 min
  (ignora alertas transitórios)
          │
          ▼
  Agente consulta em paralelo:
   • vector store → "últimas vezes que esse monitor disparou"
   • mcp-unified → quem está sendo afetado agora
          │
          ▼
  LLM classifica:
  • Crítico ou não-crítico?
  • Qual jornada afetada?
  • Hipótese de causa?
  • Quantos clientes reais?
  • Runbook sugerido?
          │
     ┌────┴─────┐
     ▼          ▼
  CRÍTICO    NÃO-CRÍTICO
     │          │
     ▼          ▼
 Notificação  Acumula no
 enriquecida  relatório
 no Teams     diário
```

### O que muda para o time de plantão

**Antes:** alerta chega no Teams → pessoa abre 4 sistemas → busca contexto → formula hipótese → age.
**Depois:** o alerta cru chega como sempre, e minutos depois chega o contexto:

```
🚨 ALERTA CRÍTICO — Latência no Checkout

Monitor: servico-checkout-p99-latency
Serviço: checkout / Jornada: compra

👥 Impacto agora:
  47 sessões ativas travadas na confirmação de pagamento
  (últimos 10 min · fonte: FullStory)

📊 Histórico (3 anos):
  Esse padrão ocorreu 8 vezes.
  • 6x causa: serviço de pagamento externo com timeout
  • 2x causa: deploy com regressão de performance

🔧 Mudanças na janela:
  CHG0000045 — deploy servico-pagamento v2.14 (há 22 min)

🎯 Hipótese mais provável:
  Timeout no parceiro de pagamento (confiança: 82%)

📋 Incidente mais similar:
  INC0000123 — março/2024 — resolvido em 47min
  Ação: restart do pod servico-pagamento + chamado no parceiro

🔗 Post-mortem: [link]   🔗 Replay de sessão afetada: [link]
```

O humano ainda decide e executa — mas chega na decisão em segundos, não em minutos.

### Relatório diário para produto e analytics

Todo dia às 07h, o time recebe um resumo das jornadas não-críticas no canal do Teams:

```
📊 RELATÓRIO SRE — Segunda-feira, 16/06/2026

Jornadas monitoradas: Cadastro · Checkout · Pagamento · Pós-venda

┌──────────────┬──────────┬─────────┬────────────┬─────────────────┐
│ Jornada      │ Erros    │ vs D-1  │ vs sem.ant │ Observação      │
├──────────────┼──────────┼─────────┼────────────┼─────────────────┤
│ Checkout     │ 142      │ ▲ +18%  │ ▼ -5%      │ Pico esperado   │
│ Pagamento    │  38      │ ▼ -12%  │ ▼ -22%     │ Melhora contínua│
│ Cadastro     │  21      │ → =     │ ▲ +4%      │ Normal          │
│ Pós-venda    │  67      │ ▲ +31%  │ ▲ +28%     │ ⚠ Investigar   │
└──────────────┴──────────┴─────────┴────────────┴─────────────────┘

⚠ Atenção: Pós-venda com aumento acima do esperado para a sazonalidade.
  Padrão similar foi observado antes do incidente INC0000087 (out/2023).
```

Esse relatório usa os 3 anos de histórico para distinguir picos sazonais esperados de anomalias reais — sem alarmar o time com variações normais. Para as jornadas não-críticas, a linha de base vem do **Datadog e do FullStory**, não do ServiceNow, justamente por causa do viés de junção descrito acima.

---

## Como vamos provar que funcionou

Esta seção é nova, e é a que separa um projeto de IA que entrega de um que só demonstra.

### Semana 0 — medir antes de mudar

Sem linha de base, `−10% de MTTR` é infalsificável: não dá para provar sucesso nem fracasso, e a discussão vira opinião. Antes de escrever código, extrair do ServiceNow os últimos 12 meses e registrar:

| Medida | Por que |
|---|---|
| MTTR mediano e p90, por severidade | Mediana é o alvo; p90 mostra os casos que doem |
| Volume de incidentes por mês e por jornada | Denominador de tudo |
| % de incidentes com post-mortem preenchido | Mede a qualidade real do corpus |
| Tempo médio entre alerta e primeira ação humana | **É este número que o agente ataca diretamente** |

O último é o mais importante e o menos medido. É o intervalo em que o humano está abrindo quatro sistemas — exatamente o trabalho que o agente elimina.

### Golden dataset — na Semana 1, não na 4

O plano anterior criava o golden dataset na Semana 4, **depois** do classificador (S2) e das notificações em produção (S3). Isso deixa duas semanas de iteração de prompt sem nenhuma medida objetiva de qualidade.

O golden dataset é o **critério de aceite** do classificador, então tem que existir antes dele. Como sai do mesmo histórico que a ingestão já processa, o custo marginal de antecipá-lo é baixo.

São 50 incidentes representativos com classificação conhecida (severidade, jornada, causa raiz), revisados por quem viveu os incidentes. Toda alteração de prompt roda contra esse gabarito e reporta se a precisão subiu ou caiu — é a proteção contra *prompt drift*.

### Critério de sucesso do L1

O projeto é considerado bem-sucedido quando, ao fim de 60 dias em produção:

1. O agente acerta a classificação crítico/não-crítico em **≥ 90%** dos casos do golden dataset
2. A hipótese de causa é considerada útil pelo plantonista em **≥ 60%** dos alertas críticos (pesquisa de um clique na própria notificação)
3. O tempo entre alerta e primeira ação humana cai de forma mensurável em relação à linha de base da Semana 0

O item 2 é subjetivo de propósito: no L1 quem julga a utilidade é quem está de plantão.

---

## Não precisamos treinar nenhum modelo

Essa é uma das perguntas mais comuns — e a resposta muda completamente a percepção de esforço e custo do projeto.

Treinar ou fazer fine-tuning de um LLM exige tempo, infraestrutura especializada e custo elevado. **Não vamos fazer isso.** A inteligência do agente vem de três técnicas que não tocam nos pesos do modelo:

### RAG — Retrieval Augmented Generation

É o coração do sistema. Em vez de "ensinar" o modelo sobre nossos incidentes, buscamos os incidentes relevantes do vector store em tempo real e os incluímos no prompt junto com o alerta atual. O modelo lê e raciocina sobre eles na hora. Os 3 anos de histórico viram **contexto dinâmico**, não peso de modelo.

```
  Alerta do Datadog chega
          │
          ▼
  Busca no vector store:
  "5 incidentes mais similares"
          │
          ▼
  Consulta mcp-unified:
  impacto ao vivo + mudanças na janela
          │
          ▼
  Monta o prompt:
    [system: você é um agente SRE...]
    [context: incidentes similares...]
    [context: situação atual...]
    [user: novo alerta é esse...]
          │
          ▼
  LLM responde com JSON
  estruturado e validado
          │
          ▼
  Notificação enriquecida
  vai para o Teams
```

### Prompt engineering

Definimos no prompt quem o agente é, o que deve fazer, qual formato de resposta queremos e quais critérios usar para classificar crítico vs não-crítico. Isso é **texto puro** — editável a qualquer momento sem redeployar código, sem retreinar nada. É também o que o golden dataset protege contra regressão.

### Few-shot examples

Dentro do próprio prompt, colocamos 3 a 5 exemplos reais do tipo "alerta X → classificação Y", extraídos do histórico da própria organização. O modelo aprende o padrão local só lendo os exemplos — sem nenhum processo de treinamento.

### O que de fato evolui com o tempo

O modelo nunca muda. O que cresce e melhora é o **vector store** — que fica mais rico a cada incidente novo indexado. É o feedback loop: o agente fica mais preciso porque a memória institucional cresce, não porque o modelo foi retreinado.

| O que NÃO fazemos | O que fazemos |
|---|---|
| Fine-tuning do modelo | RAG com vector store |
| Infraestrutura de GPU | API do modelo (pay-per-use) ou modelo local |
| Dataset rotulado manualmente | Histórico existente + golden dataset de 50 casos |
| Meses de preparação | 6 semanas até o L1 em produção |

---

## Arquitetura técnica e infraestrutura (para o Tech Lead)

Abordagem de menor custo possível na AWS. O armazenamento vetorial usa **FAISS** com o índice persistido no **S3**, dispensando bancos de dados adicionais.

```
┌──────────────────────────────────────────────────────────────────────┐
│                         AWS Cloud                                     │
│                                                                       │
│  ┌──────────────────────────────────────────────────────────────┐    │
│  │                   EC2 (t4g.medium)                            │    │
│  │                                                               │    │
│  │  ┌──────────────────────┐   ┌──────────────────────────┐    │    │
│  │  │ Container: agente     │   │ Container: mcp-unified    │    │    │
│  │  │                       │──▶│                           │    │    │
│  │  │ • Polling + debounce  │MCP│ • Datadog · FullStory     │    │    │
│  │  │ • FAISS (índice RAM)  │   │ • ServiceNow · MS Graph   │    │    │
│  │  │ • Dedup por hashing   │   │ • Correlação ao vivo      │    │    │
│  │  │ • Circuit breaker     │   │ • Redação de PII          │    │    │
│  │  │ • Chamada ao LLM      │   └──────────────────────────┘    │    │
│  │  │ • Notificação Teams   │                                    │    │
│  │  │ • Job diário (07h)    │   ┌──────────────────────────┐    │    │
│  │  └───────────┬──────────┘   │ Job de ingestão (batch)   │    │    │
│  │              │               │ Presidio + embeddings     │    │    │
│  │              │               │ roda sob demanda, não 24/7│    │    │
│  │              │               └──────────────────────────┘    │    │
│  └──────────────┼────────────────────────────────────────────────┘    │
│                 │  Download / Upload atômico do índice                │
│           ┌─────▼──────────────────┐                                 │
│           │  S3 (versionamento on) │                                 │
│           │  • Índice .faiss       │                                 │
│           │  • Metadados JSON      │                                 │
│           │  • Relatórios diários  │                                 │
│           └────────────────────────┘                                 │
└──────────────────────────────────────────────────────────────────────┘
```

### Correção de dimensionamento

A versão anterior previa `t4g.small` (2 GB). **Não cabe.** O Presidio carrega modelos spaCy — o modelo grande de português passa de 500 MB sozinho — e ainda haveria o índice FAISS em RAM, o runtime Python e o container. Duas mudanças:

1. **Presidio sai do caminho quente.** A sanitização de PII acontece na **ingestão**, num job batch que roda sob demanda e não fica residente. O container do agente não carrega spaCy.
2. **`t4g.medium` (4 GB)** como ponto de partida, revisto depois de medir o tamanho real do índice. A diferença de custo é ~US$ 15/mês, muito abaixo do custo de depurar OOM em produção.

Para PII em **alertas ao vivo** (payloads do Datadog podem trazer amostras de log), quem faz a redação é a camada `redact.py` do `mcp-unified`, baseada em regras — mais leve que NER e suficiente para o formato conhecido dos campos.

**Fontes de dados (modo read-only):** Datadog · FullStory · ServiceNow · SharePoint e Teams (Microsoft Graph API) — todas via `mcp-unified`.

**Stack:** Python · FAISS · Microsoft Presidio (batch) · Pydantic · boto3 · APScheduler
**Deploy:** dois containers Docker na mesma EC2, sem Kubernetes
**Modelo LLM:** **agnóstico de provedor.** O `mcp-unified` expõe a mesma interface para provedor hospedado ou modelo local; a escolha é uma variável de ambiente, por rota. Ver a seção de privacidade adiante.

### Custos mensais estimados

| Componente | Custo |
|---|---|
| EC2 `t4g.medium` (2 vCPU, 4 GB, on-demand) | ~US$ 29 |
| Amazon S3 (índice + relatórios, versionamento ativo) | < US$ 0,50 |
| LLM hospedado (~50 alertas/dia, com cache de prompt) | US$ 15–25 |
| Secrets Manager (7 secrets) | ~US$ 2,80 |
| **Total** | **~US$ 47–57/mês** |

Se a decisão for rodar modelo local, a linha do LLM zera e a instância precisa ser redimensionada — a conta muda de forma, não de ordem de grandeza. O custo continua irrisório comparado ao impacto de um único incidente mal diagnosticado.

**Segurança:** o agente opera em modo **read-only** em todas as fontes no L1. Nenhuma ação é executada automaticamente — ele lê, classifica e notifica. Isso elimina risco de ação acidental em produção.

---

## Pontos de blindagem técnica

### 1. Privacidade de dados (PII) e a decisão sobre o LLM

Chats do Teams, chamados do ServiceNow e post-mortems contêm dados sensíveis: e-mails, CPFs, tokens de API, senhas em stack traces.

**Duas camadas:**

- **Na ingestão:** pipeline com **Microsoft Presidio** antes da vetorização. Dados reais viram tokens genéricos (`[EMAIL_CLIENTE]`, `[API_KEY_MASCARADA]`, `[IP_INTERNO]`) que preservam o contexto semântico. Stack traces têm só classes de exceção e funções extraídas, sem variáveis de runtime.
- **No caminho quente:** redação por regras no `mcp-unified`, aplicada antes de qualquer prompt sair da máquina.

**Sobre o LLM externo — a decisão que precisa ser explícita.** Sanitização por NER é heurística, não garantia: o Presidio erra, e num contexto de instituição financeira o custo de um vazamento não é proporcional à taxa de erro. Por isso a camada de LLM é agnóstica de provedor:

| Opção | Quando faz sentido |
|---|---|
| Provedor hospedado | Melhor qualidade de raciocínio; exige aval de segurança e conformidade sobre os dados sanitizados |
| Modelo local (mesma máquina) | Nenhum dado sai da rede; qualidade menor, e saída estruturada exige validação mais cuidadosa |

A recomendação é **começar hospedado com o corpus sanitizado** para validar a qualidade do agente, e manter o caminho local pronto — trocar é uma variável de ambiente, não uma refatoração. A decisão final é do time de segurança, e o desenho não a força em nenhuma direção.

### 2. Proteção contra flapping e controle de custo

Alertas que oscilam entre OK e CRITICAL dezenas de vezes por minuto consumiriam o budget de API em horas e gerariam fadiga de alertas.

- **Debounce:** só analisa se o alerta persistir 3–5 minutos. Transitórios são ignorados.
- **Deduplicação por hashing:** assinatura única (`service + monitor + tags`) agrupa múltiplas ocorrências num único incidente lógico, sem novas chamadas ao LLM.
- **Circuit breaker:** em falhas em cascata, o agente pausa análises individuais e envia um único alerta consolidado de "Falha Sistêmica Detectada", com hard limit configurável de chamadas por minuto.

### 3. Confiabilidade do vector store (FAISS + S3)

- **Init seguro:** ao iniciar, o container baixa o índice mais recente do S3 antes de aceitar qualquer alerta.
- **Upload atômico:** adições são sincronizadas de forma assíncrona e atômica — o objeto anterior só é substituído após confirmação de integridade.
- **Versionamento S3 ativo:** cada versão preservada, permitindo rollback imediato se um lote corrompido for processado.

### 4. Qualidade e confiança na saída do LLM

- **Outputs estruturados obrigatórios:** a resposta é validada com **Pydantic** antes de qualquer uso. Fora do schema, rejeita e retenta.
- **Golden dataset de avaliação:** os 50 incidentes da Semana 1 são o gabarito. Toda alteração de prompt é testada contra eles.
- **Fallback de alerta cru:** se o LLM falhar após retentativas, o alerta original é despachado com a tag `[Análise de IA Indisponível]`.

### 5. O agente é aditivo, não substituto

O alerta direto do Datadog para o Teams **continua existindo e não passa pelo agente**. Se a EC2 cair, se o LLM ficar indisponível ou se o índice corromper, o time continua sendo notificado exatamente como é hoje. O agente só adiciona contexto.

Isso não é detalhe de implementação — é o que torna aceitável rodar um sistema de confiabilidade numa única instância sem redundância.

---

## O que vamos construir — 6 semanas

O plano anterior previa 4 semanas. As duas semanas a mais têm razões concretas: a linha de base não existia no cronograma, o golden dataset estava no fim em vez do começo, e a ingestão de 3 anos de quatro sistemas — incluindo histórico de Teams via Graph API, com throttling e paginação — não cabe em uma semana.

```
Semana 0 — Linha de base e acessos
  • Extrair MTTR, volume e tempo-até-primeira-ação dos últimos 12 meses
  • Abrir pedido de permissões: Azure AD (ChannelMessage.Read.All), ServiceNow, Datadog
  • Validar escopo L1 com tech lead e segurança
  Entrega: números de referência acordados + pedidos de acesso protocolados

Semanas 1–2 — Ingestão do histórico + golden dataset
  • mcp-unified conectando as quatro fontes
  • Unificação por chave ServiceNow (documentando a cobertura real da junção)
  • Sanitização de PII com Presidio (job batch)
  • Embeddings e índice FAISS no S3
  • Curadoria dos 50 incidentes do golden dataset, com revisão humana
  Entrega: base pesquisável com 3 anos + gabarito de avaliação

Semana 3 — Classificador
  • Polling do Datadog com debounce e deduplicação
  • RAG: busca dos incidentes similares + consulta de impacto ao vivo
  • LLM classifica; saída validada com Pydantic
  • Medição contra o golden dataset desde o primeiro dia
  Entrega: classificação em staging com precisão medida

Semana 4 — Notificações enriquecidas
  • Roteamento: crítico → Teams imediato, não-crítico → acumulador
  • Template com histórico, impacto ao vivo e mudanças na janela
  • Circuit breaker + fallback de alerta cru
  Entrega: alertas enriquecidos no canal do Teams

Semana 5 — Relatório diário e produção
  • Job agendado às 07h com comparação sazonal
  • Pesquisa de utilidade de um clique na notificação
  • Corte para produção com monitoramento do próprio agente
  Entrega: relatório diário automatizado + L1 em produção
```

**Dependência crítica:** a permissão `ChannelMessage.Read.All` do Teams costuma ser o item mais lento, e é por isso que o pedido está na Semana 0. Se não sair a tempo, o provedor fica desabilitado e o sistema roda com três fontes — o `mcp-unified` degrada sem quebrar. O Teams entra depois, sem retrabalho.

---

## Próximos passos

1. **Esta semana:** validar com o tech lead o escopo L1 e a arquitetura em duas camadas; abrir os pedidos de acesso
2. **Semana 0:** extrair a linha de base do ServiceNow e acordar os critérios de sucesso com Produto
3. **Semanas 1–2:** ingestão com ServiceNow como âncora, sanitização e golden dataset
4. **Semanas 3–5:** classificador, notificações e relatório diário em produção

**Evolução pós-L1:** após 60 dias medindo precisão contra o golden dataset e utilidade percebida pelo plantão, avaliar a transição para L2 — onde o agente sugere e prepara a remediação, e o humano aprova com um clique. **O gatilho para essa conversa é o critério de sucesso batido, não o calendário.**

---

## Referências

- Google SRE: [AI Engineering Reliable Operations](https://sre.google/resources/practices-and-processes/ai-engineering-reliable-operations/) (2025)
- Google SRE Books: [sre.google/books](https://sre.google/books/)
- Microsoft Graph API: [developer.microsoft.com](https://developer.microsoft.com/en-us/graph)
- ServiceNow REST API: [developer.servicenow.com](https://developer.servicenow.com)
- FAISS: [github.com/facebookresearch/faiss](https://github.com/facebookresearch/faiss)
- Microsoft Presidio: [github.com/microsoft/presidio](https://github.com/microsoft/presidio)
- Camada de dados deste projeto: `PLAN.md` (mcp-unified)

---

*Arquitetura de referência. Versão 2.0 — revisão de escopo e métricas. Dados e identificadores são ilustrativos.*
