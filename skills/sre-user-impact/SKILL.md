---
name: sre-user-impact
description: Liga a falha técnica à experiência real do cliente, nas duas direções — de uma sessão para a telemetria de backend, ou de um incidente para os clientes afetados. Use quando o usuário disser "quantos clientes foram afetados", "o que aconteceu com esse usuário", "o cliente reclamou que não conseguiu", "quem sentiu esse incidente", "me mostra a sessão", "por que a transferência falhou para esse cliente", ou colar um ID de sessão. Termina com a narrativa do que a pessoa viveu e o que o sistema fez enquanto isso.
license: MIT
metadata:
  version: "1.0"
  repository: https://github.com/dalmosantos/mcp-unified
  tags: sre,fullstory,correlação,impacto,sessão,experiência
---

# Impacto no Usuário

A pergunta que a telemetria de backend sozinha não responde: **uma pessoa real
estava do outro lado, e o que ela viveu?**

## Por que esta skill existe

Log diz que houve timeout. Não diz que o cliente clicou três vezes em
"Confirmar", viu a tela travar, e desistiu. Essa segunda metade é o que
determina severidade — e é a que costuma faltar na sala de guerra.

As tools de correlação deste servidor derivam a janela temporal da sessão
automaticamente. **Você nunca deveria copiar timestamp de uma tool para outra
manualmente.** Se estiver fazendo isso, está usando a ferramenta errada.

## Disponibilidade — confira antes

1. `build_unified_timeline` e `find_sessions_for_incident` na sua lista? Sem
   elas não há correlação — diga o que falta e pare.
2. Provedor de sessão configurado? As tools que partem de uma sessão exigem
   FullStory (`FULLSTORY_API_KEY`). Sem ele, só a direção inversa funciona, e
   ainda assim sem link de replay.
3. Toda resposta de correlação traz `providers_unavailable`. **Leia esse campo
   e repasse ao usuário** quando estiver preenchido: significa que a timeline
   está incompleta, e a conclusão precisa levar isso em conta.

## Duas direções — escolha e anuncie

Não pergunte ao usuário qual direção; deduza do que ele trouxe e diga qual vai
seguir em uma linha.

| Você tem | Direção | Tool de entrada |
|---|---|---|
| ID de sessão, ou reclamação de um cliente | sessão → backend | `build_unified_timeline` |
| Janela de incidente, ou query de erro | incidente → clientes | `find_sessions_for_incident` |

---

## Direção A — de uma sessão para o backend

**Pergunta típica:** *"o cliente diz que não conseguiu transferir às 14h30"*

```
build_unified_timeline(
  user_id="<device id>",
  session_id="<id da sessão>",
)
```

Isso funde numa linha do tempo única: eventos da sessão, logs, RUM, spans de
APM, deploys e mudanças aprovadas — cada entrada marcada com a origem.

### Leia a timeline como narrativa causal

O padrão que você procura é uma **cadeia**, não entradas isoladas:

```
14:31:00  fullstory       click: Confirmar transferência     ← a pessoa agiu
14:31:01  datadog-spans   ⚠ POST /api/… (8200ms)             ← o sistema demorou
14:31:09  datadog-logs    ⚠ timeout ao consultar SPI         ← e falhou
14:31:30  fullstory       ⚠ mouse_thrash                     ← a pessoa insistiu
14:31:50  fullstory       ⚠ exception                        ← e viu o erro
```

O marcador ⚠ sinaliza entrada notável: erro, span lento, sinal de frustração.
Entradas 🔧 são mudanças; 🎫 são chamados.

### As duas armadilhas

**`padding_seconds` — a causa costuma ficar fora da janela padrão.**

A janela é derivada dos eventos da sessão mais 60 segundos de folga. Um deploy
que causou a falha normalmente aconteceu **antes da sessão começar**, e por
isso não aparece. Se a timeline não mostra nenhuma mudança e você suspeita de
uma, amplie:

```
build_unified_timeline(..., padding_seconds=900)   # 15 min de cada lado
```

Não conclua "nenhuma mudança recente" sem ter ampliado ao menos uma vez.

**`correlation_mode` — leia `fallback_reason` antes de concluir.**

O modo padrão `both` tenta filtrar pela identidade do usuário; se não achar
nada, cai para a janela temporal inteira. Isso é útil, mas o resultado passa a
conter **atividade de outros clientes**.

A resposta declara isso em `fallback_reason`. Se estiver preenchido, você não
pode dizer "esse cliente sofreu X" — só "houve X na janela". Diga qual dos dois
está afirmando.

| Modo | Use quando |
|---|---|
| `both` (padrão) | investigação normal |
| `identity` | precisa de certeza sobre este cliente; vazio é resposta legítima |
| `time` | o app não loga identidade, ou você quer o contexto completo |

### Aprofundar

Depois da timeline, se precisar de detalhe:

- `fullstory_get_session_events` — o transcript completo, sem filtro
- `fullstory_get_session_insights` — clusters de comportamento, pontos de abandono
- `fullstory_get_session_link` — **a URL do replay**, para o humano assistir

O replay é o caminho para inspeção visual: não existe API pública de
screenshot. Ofereça o link sempre que a discussão virar "mas o que exatamente
apareceu na tela".

---

## Direção B — de um incidente para os clientes

**Pergunta típica:** *"quantos clientes o incidente das 14h afetou?"*

```
find_sessions_for_incident(
  query="service:<serviço> status:error",
  from_="2026-08-07T14:00:00Z",
  to="2026-08-07T15:00:00Z",
  max_users=10,
)
```

Agrega os logs pelo atributo de identidade, devolve os clientes mais afetados
com contagem de ocorrências, e busca as sessões de cada um — com link de replay.

### Quando vier vazio

Não é necessariamente "ninguém foi afetado". O campo `hint` na resposta explica
o motivo real, quase sempre um destes:

- **Os logs não carregam o atributo de identidade.** O padrão é `@usr.id`; se o
  app usa outro nome, é preciso ajustar `FS_DD_USER_ATTR`. Diga isso ao
  usuário — é configuração, não ausência de impacto.
- **A janela está errada.** Confira contra o horário do alerta.

`max_users` limita o fan-out de propósito, para não estourar o rate limit da
FullStory. Aumentar dá mais cobertura e mais lentidão; comece com 10.

---

## Fechamento

Uma boa resposta desta skill tem três partes:

1. **O que a pessoa viveu** — em português, não em nomes de evento. "Clicou em
   confirmar, esperou 8 segundos, viu erro, tentou mais duas vezes" é melhor
   que "click, span, exception, mouse_thrash ×2".
2. **O que o sistema fez** — a cadeia técnica correspondente.
3. **Dimensão** — quantos clientes, em qual janela, e com qual confiança
   (lembre do `fallback_reason`).

Ofereça o link de replay quando existir. É o que transforma uma discussão sobre
severidade em consenso em trinta segundos.

## Quando parar e escalar

Volte para `sre-triage` se a investigação revelar que o problema é mais amplo
do que os clientes observados — o que você viu pode ser sintoma de algo
sistêmico.

Passe para `sre-postmortem` quando a pergunta virar registro.
