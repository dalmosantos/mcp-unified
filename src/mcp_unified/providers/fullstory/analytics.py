"""Análise client-side portada do `Fullstory.js`.

Esta é a parte do provedor que **não** é chamada de API: são ~660 linhas de
heurística que o conector original roda sobre os eventos brutos para produzir
`get_session_insights`, `get_user_profile` e `get_user_analytics`.

O port mantém a forma do output e os limiares numéricos do original. Onde o
JavaScript era ambíguo (coerção de tipo, `undefined` em comparação), a versão
Python escolhe o comportamento defensivo e isso está anotado.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable
from datetime import datetime, timezone
from typing import Any

# Categorias comportamentais e o mapa de tipo de evento → categoria.
# Copiados literalmente do original: mudar isto muda o output das três tools
# derivadas, então qualquer ajuste deveria ser deliberado.
NAVIGATION = "Navigation & Orientation"
INFORMATION = "Information Seeking & Learning"
TASK = "Task Accomplishment & Management"
COMMUNICATION = "Communication & Community"
ENTERTAINMENT = "Entertainment & Leisure"
FEEDBACK = "Feedback & Contribution"
TRANSACTION = "Transaction & Acquisition"

BEHAVIORAL_CATEGORIES: tuple[str, ...] = (
    NAVIGATION,
    INFORMATION,
    TASK,
    COMMUNICATION,
    ENTERTAINMENT,
    FEEDBACK,
    TRANSACTION,
)

EVENT_CATEGORY_MAP: dict[str, str] = {
    "navigate": NAVIGATION,
    "page_view": NAVIGATION,
    "load": NAVIGATION,
    "back_forward": NAVIGATION,
    "reload": NAVIGATION,
    "click": INFORMATION,
    "element_seen": INFORMATION,
    "highlight": INFORMATION,
    "copy": INFORMATION,
    "pinch_gesture": INFORMATION,
    "first_input_delay": INFORMATION,
    "interaction_to_next_paint": INFORMATION,
    "change": TASK,
    "form_abandon": TASK,
    "paste": TASK,
    "keyboard_open": TASK,
    "keyboard_close": TASK,
    "custom": TASK,
    "page_properties": TASK,
    "identify": COMMUNICATION,
    "consent": COMMUNICATION,
    "mouse_thrash": ENTERTAINMENT,
    "cumulative_layout_shift": ENTERTAINMENT,
    "console_message": FEEDBACK,
    "exception": FEEDBACK,
    "request": FEEDBACK,
    "crash": FEEDBACK,
    "low_memory": FEEDBACK,
}

ENGAGEMENT_WEIGHTS: dict[str, int] = {
    "page_view": 1,
    "click": 2,
    "form_interaction": 3,
    "video_play": 4,
    "purchase": 10,
    "signup": 10,
}

FUNNEL_STAGES: tuple[str, ...] = (
    "page_view",
    "product_view",
    "add_to_cart",
    "checkout_start",
    "checkout",
    "purchase",
    "signup",
)

DROPOFF_THRESHOLD_MS = 300_000  # 5 minutos, como no original


# --------------------------------------------------------------------- utils


def event_name(event: dict[str, Any]) -> str:
    """Nome do evento. O original aceita `event_type` ou `name`, nessa ordem."""
    return event.get("event_type") or event.get("name") or "unknown"


def event_timestamp(event: dict[str, Any]) -> datetime | None:
    """Timestamp do evento, aceitando `event_time` ou `timestamp`.

    O original usava `new Date(...)`, que devolve `Invalid Date` em vez de
    falhar. Aqui um valor não parseável vira `None` e o evento é ignorado nos
    cálculos temporais — mais seguro que propagar `NaN`.
    """
    raw = event.get("event_time") or event.get("timestamp")
    if not raw:
        return None
    if isinstance(raw, (int, float)):
        # Heurística: acima de 1e11 é milissegundo.
        seconds = raw / 1000 if raw > 1e11 else raw
        return datetime.fromtimestamp(seconds, tz=timezone.utc)
    try:
        text = str(raw).replace("Z", "+00:00")
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def sort_events(events: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Ordena por timestamp; eventos sem timestamp válido vão para o fim."""
    far_future = datetime.max.replace(tzinfo=timezone.utc)
    return sorted(events, key=lambda e: event_timestamp(e) or far_future)


# ------------------------------------------------------------- meta da sessão


def extract_session_meta(sorted_events: list[dict[str, Any]]) -> dict[str, Any]:
    """Extrai propriedades de origem, localização e dispositivo.

    Usa o primeiro evento que tenha `source_properties`; se nenhum tiver, o
    primeiro evento da lista.
    """
    empty = {"sourceProperties": {}, "location": {}, "device": {}}
    if not sorted_events:
        return empty

    first_with_meta = next(
        (e for e in sorted_events if e.get("source_properties") or e.get("sourceProperties")),
        sorted_events[0],
    )
    source_props = (
        first_with_meta.get("source_properties")
        or first_with_meta.get("sourceProperties")
        or {}
    )
    if not isinstance(source_props, dict):
        return empty

    return {
        "sourceProperties": {
            k: v for k, v in source_props.items() if k not in ("location", "device")
        },
        "location": source_props.get("location") or {},
        "device": source_props.get("device") or {},
    }


# ------------------------------------------------------------- clusterização


def _categorize(event: dict[str, Any]) -> str:
    """Categoriza um evento por intenção comportamental.

    ⚠️ **Divergência deliberada do original.** No `Fullstory.js`, `'custom'`
    está no mapa de categorias (→ Task), e a checagem do mapa vem antes da
    lógica por nome do evento — o que torna o ramo de eventos custom código
    morto: um `checkout_concluido` era classificado como Task, nunca como
    Transaction.

    A intenção do original é inequívoca (o ramo existe e nomeia purchase,
    checkout, search e filter), então aqui a ordem é invertida: eventos custom
    são classificados pelo nome primeiro, e só caem em Task se o nome não
    casar com nada. Sem isso, a categoria Transaction ficaria sempre vazia em
    qualquer app que use eventos customizados — que é o caso normal.
    """
    name = event_name(event)

    if name == "custom":
        props = event.get("event_properties") or event.get("properties") or {}
        custom_name = str(props.get("event_name", "")).lower() if isinstance(props, dict) else ""
        if any(t in custom_name for t in ("purchase", "buy", "checkout")):
            return TRANSACTION
        if any(t in custom_name for t in ("search", "filter")):
            return INFORMATION
        return TASK

    return EVENT_CATEGORY_MAP.get(name, TASK)


def generate_event_clustering(sorted_events: list[dict[str, Any]]) -> dict[str, Any]:
    """Agrupa eventos por intenção comportamental e deriva clusters."""
    if not sorted_events:
        return {"clusters": [], "eventDistribution": {}, "behavioralCategories": {}}

    total = len(sorted_events)
    categories: dict[str, list[dict[str, Any]]] = {c: [] for c in BEHAVIORAL_CATEGORIES}
    for event in sorted_events:
        categories[_categorize(event)].append(event)

    distribution: dict[str, dict[str, Any]] = {}
    for category_name, category_events in categories.items():
        for event in category_events:
            name = event_name(event)
            entry = distribution.setdefault(name, {"total": 0, "categories": defaultdict(int)})
            entry["total"] += 1
            entry["categories"][category_name] += 1

    for entry in distribution.values():
        entry["categories"] = {
            cat: round(count / entry["total"] * 100)
            for cat, count in entry["categories"].items()
        }

    clusters = []
    for name, data in distribution.items():
        if not data["categories"]:
            continue
        dominant_cat, dominant_pct = max(data["categories"].items(), key=lambda kv: kv[1])
        # Limiar do original: só vira cluster com 70% de concentração.
        if dominant_pct >= 70 and data["total"] >= 1:
            clusters.append(
                {
                    "eventType": name,
                    "category": dominant_cat,
                    "concentration": dominant_pct,
                    "count": data["total"],
                }
            )
    clusters.sort(key=lambda c: c["concentration"], reverse=True)

    return {
        "clusters": clusters,
        "eventDistribution": distribution,
        "behavioralCategories": {
            category: {
                "count": len(evts),
                "percentage": round(len(evts) / total * 100),
                "events": [
                    {"name": event_name(e), "timestamp": e.get("event_time") or e.get("timestamp")}
                    for e in evts
                ],
            }
            for category, evts in categories.items()
        },
        "behavioralInsights": analyze_behavioral_patterns(categories),
        "sessionFlow": analyze_session_flow(sorted_events),
        "totalEvents": total,
        "sessionDuration": calculate_session_duration(sorted_events),
        "eventTypes": len(distribution),
    }


def analyze_behavioral_patterns(
    categories: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    """Deriva comportamento primário, confiança, traços e nível de engajamento."""
    total = sum(len(v) for v in categories.values())
    if total == 0:
        return {
            "primaryBehavior": "no_activity",
            "confidence": 0,
            "behaviorTraits": [],
            "engagementLevel": "none",
        }

    dist = {
        cat: {"count": len(evts), "percentage": round(len(evts) / total * 100)}
        for cat, evts in categories.items()
    }
    significant = sorted(
        ((c, d) for c, d in dist.items() if d["percentage"] >= 15),
        key=lambda kv: kv[1]["percentage"],
        reverse=True,
    )

    primary = significant[0][0] if significant else "mixed_activity"
    primary_pct = significant[0][1]["percentage"] if significant else 0

    if total >= 20 and primary_pct >= 50:
        confidence = 0.9
    elif total >= 10 and primary_pct >= 40:
        confidence = 0.7
    elif total >= 5 and primary_pct >= 30:
        confidence = 0.5
    else:
        confidence = 0.3

    traits = [
        {"behavior": cat, "strength": "strong" if data["percentage"] >= 25 else "moderate"}
        for cat, data in significant[1:4]
    ]

    if total >= 50:
        engagement = "high"
    elif total >= 20:
        engagement = "medium"
    elif total >= 5:
        engagement = "moderate"
    else:
        engagement = "low"

    active = sum(1 for d in dist.values() if d["count"] > 0)
    diversity = active / len(BEHAVIORAL_CATEGORIES)

    insights: list[str] = []
    if primary_pct >= 60:
        insights.append(f"Highly focused on {primary.lower()}")
    elif diversity >= 0.6:
        insights.append("Demonstrates diverse behavioral patterns")
    if dist.get(NAVIGATION, {}).get("percentage", 0) >= 30:
        insights.append("Strong exploration and navigation behavior")
    if dist.get(TRANSACTION, {}).get("percentage", 0) >= 20:
        insights.append("Shows commercial intent and conversion behavior")
    if dist.get(INFORMATION, {}).get("percentage", 0) >= 40:
        insights.append("Exhibits research and information-gathering patterns")

    return {
        "primaryBehavior": primary,
        "primaryPercentage": primary_pct,
        "confidence": confidence,
        "behaviorTraits": traits,
        "engagementLevel": engagement,
        "behavioralDiversity": round(diversity, 2),
        "categoryDistribution": dist,
        "insights": insights,
    }


def analyze_session_flow(sorted_events: list[dict[str, Any]]) -> dict[str, Any]:
    """Transições entre eventos, caminhos comuns e pontos de abandono."""
    if len(sorted_events) < 2:
        return {"transitions": [], "commonPaths": [], "dropoffPoints": []}

    transitions: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()

    for current, following in zip(sorted_events, sorted_events[1:], strict=False):
        from_name, to_name = event_name(current), event_name(following)
        t0, t1 = event_timestamp(current), event_timestamp(following)
        delta_ms = (t1 - t0).total_seconds() * 1000 if t0 and t1 else 0
        transitions.append({"from": from_name, "to": to_name, "timeDiff": delta_ms})
        counts[f"{from_name} → {to_name}"] += 1

    return {
        "transitions": transitions,
        "commonPaths": [{"path": p, "count": c} for p, c in counts.most_common(5)],
        "dropoffPoints": [
            {
                "afterEvent": t["from"],
                "beforeEvent": t["to"],
                "delayMinutes": round(t["timeDiff"] / 60_000),
            }
            for t in transitions
            if t["timeDiff"] > DROPOFF_THRESHOLD_MS
        ],
    }


def calculate_session_duration(sorted_events: list[dict[str, Any]]) -> int:
    """Duração em segundos entre o primeiro e o último evento com timestamp."""
    stamps = [ts for ts in (event_timestamp(e) for e in sorted_events) if ts]
    if len(stamps) < 2:
        return 0
    return round((stamps[-1] - stamps[0]).total_seconds())


def process_session_events(events: list[dict[str, Any]] | None) -> dict[str, Any]:
    """Passada única que produz tudo que as tools derivadas precisam."""
    if not events:
        return {
            "eventCount": 0,
            "uniqueEventTypes": 0,
            "sortedEvents": [],
            "sessionMetaInformation": {"sourceProperties": {}, "location": {}, "device": {}},
            "behavioralClustering": {
                "clusters": [],
                "eventDistribution": {},
                "behavioralCategories": {},
            },
        }

    ordered = sort_events(events)
    return {
        "eventCount": len(events),
        "uniqueEventTypes": len({event_name(e) for e in events}),
        "sortedEvents": ordered,
        "sessionMetaInformation": extract_session_meta(ordered),
        "behavioralClustering": generate_event_clustering(ordered),
    }


# ------------------------------------------------------- métricas de usuário


def count_session_events(events: list[dict[str, Any]]) -> int:
    return sum(1 for e in events if event_name(e) in ("session_start", "new_session"))


def calculate_average_session_duration(events: list[dict[str, Any]]) -> int:
    """Média em minutos entre pares `session_start` → `session_end`."""
    durations: list[float] = []
    current_start: datetime | None = None

    for event in events:
        name = event_name(event)
        stamp = event_timestamp(event)
        if name in ("session_start", "new_session"):
            current_start = stamp
        elif name == "session_end" and current_start and stamp:
            durations.append((stamp - current_start).total_seconds())
            current_start = None

    if not durations:
        return 0
    return round(sum(durations) / len(durations) / 60)


def get_most_frequent_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    counts = Counter(event_name(e) for e in events)
    return [{"name": name, "count": count} for name, count in counts.most_common(10)]


def calculate_conversion_funnel(events: list[dict[str, Any]]) -> dict[str, int]:
    """Contagem por estágio de funil.

    Mantém a busca por substring do original (`checkout_start` também casa
    com um evento chamado `checkoutstart`).
    """
    names = [event_name(e) for e in events]
    funnel: dict[str, int] = {}
    for stage in FUNNEL_STAGES:
        collapsed = stage.replace("_", "")
        funnel[stage] = sum(1 for n in names if n == stage or collapsed in n.lower())
    return funnel


def calculate_engagement_score(events: list[dict[str, Any]]) -> int:
    """Score 0–100 ponderado por tipo de evento."""
    if not events:
        return 0
    total = sum(ENGAGEMENT_WEIGHTS.get(event_name(e), 1) for e in events)
    max_possible = len(events) * 10
    return min(100, round(total / max_possible * 100))


def calculate_pattern_confidence(events: list[dict[str, Any]]) -> float:
    count = len(events)
    if count < 5:
        return 0.3
    if count < 20:
        return 0.6
    return 0.8


def analyze_behavior_pattern(events: list[dict[str, Any]]) -> dict[str, Any]:
    """Padrão dominante entre explorer / converter / researcher / social."""
    if not events:
        return {"pattern": "no_data", "confidence": 0, "traits": []}

    names = [event_name(e) for e in events]
    patterns = {
        "explorer": sum(1 for n in names if n == "page_view") > len(events) * 0.7,
        "converter": any(n in ("purchase", "signup", "conversion") for n in names),
        "researcher": sum(1 for n in names if "search" in n or "filter" in n) > 5,
        "social": sum(1 for n in names if "share" in n or "comment" in n) > 2,
    }
    active = [k for k, v in patterns.items() if v]
    dominant = active[0] if active else "casual"

    return {
        "pattern": dominant,
        "confidence": calculate_pattern_confidence(events),
        "traits": active,
    }


def build_user_analytics(events: list[dict[str, Any]]) -> dict[str, Any]:
    """Agregado usado por `fullstory_get_user_analytics`."""
    return {
        "totalEvents": len(events),
        "sessionCount": count_session_events(events),
        "averageSessionDurationMinutes": calculate_average_session_duration(events),
        "mostFrequentEvents": get_most_frequent_events(events),
        "conversionFunnel": calculate_conversion_funnel(events),
        "engagementScore": calculate_engagement_score(events),
        "behaviorPattern": analyze_behavior_pattern(events),
    }
