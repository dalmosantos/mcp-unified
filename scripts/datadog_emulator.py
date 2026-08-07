#!/usr/bin/env python3
"""Emulador da API do Datadog para teste local.

**Por que isto existe, e não um Datadog Agent.** O Agent é um remetente: ele
coleta telemetria e envia para a nuvem. Ele não expõe API de leitura. Este
servidor consulta `api.datadoghq.com` atrás de logs, monitores e spans — um
Agent local não cria esse endpoint. Não existe "Datadog local consultável".

Diferente de `demo_upstream.py`, que devolve fixture ignorando o pedido, este
emulador **valida a requisição**:

- exige `DD-API-KEY` e `DD-APPLICATION-KEY`, e devolve 403 no formato real
- interpreta `filter.query`, incluindo o filtro de identidade `@usr.id:<x>`
- respeita a janela `from`/`to` e só gera eventos dentro dela
- honra `group_by` na agregação
- pode simular 429 com `Retry-After` e 500, para exercitar o retry

Ou seja: ele prova que **construímos a requisição certa**, não só que sabemos
ler uma resposta.

    python scripts/datadog_emulator.py                # normal
    python scripts/datadog_emulator.py --fail-mode 429  # exercita o backoff
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

PORT = 8932

# Cenário: falha de transferência PIX. Os clientes têm volumes diferentes de
# propósito, para que a ordenação por ocorrências seja verificável.
INCIDENT_START = datetime(2026, 8, 7, 14, 31, 0, tzinfo=timezone.utc)
AFFECTED = {"cliente-4471": 12, "cliente-8890": 7, "cliente-2013": 3}
SERVICE = "servico-transferencia"

STATE = {"fail_mode": None, "requests": [], "fail_countdown": 0}


# ------------------------------------------------------------------ helpers


def parse_moment(value: str | None, default: datetime) -> datetime:
    """Aceita ISO8601 e o formato relativo do Datadog (`now-15m`)."""
    if not value:
        return default
    text = str(value).strip()
    relative = re.match(r"^now-(\d+)([smhd])$", text)
    if relative:
        amount, unit = int(relative.group(1)), relative.group(2)
        delta = {"s": "seconds", "m": "minutes", "h": "hours", "d": "days"}[unit]
        return datetime.now(timezone.utc) - timedelta(**{delta: amount})
    if text == "now":
        return datetime.now(timezone.utc)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return default
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def uid_in_query(query: str) -> str | None:
    """Extrai o filtro de identidade, se a consulta trouxer um."""
    match = re.search(r"@usr\.id:(\S+)", query or "")
    return match.group(1) if match else None


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def make_logs(query: str, start: datetime, end: datetime, limit: int) -> list[dict]:
    """Gera logs coerentes com a consulta e a janela.

    Se a consulta filtrar por identidade, só o cliente pedido aparece — é isso
    que faz o teste de `correlation_mode` significar alguma coisa.
    """
    wanted_uid = uid_in_query(query)
    only_errors = "status:error" in (query or "")
    clients = [wanted_uid] if wanted_uid else list(AFFECTED)

    logs: list[dict] = []
    for client in clients:
        if client not in AFFECTED:
            continue  # cliente pedido não faz parte do incidente
        for i in range(AFFECTED[client]):
            ts = INCIDENT_START + timedelta(seconds=i * 7)
            if not (start <= ts <= end):
                continue
            status = "error" if i % 3 != 2 else "info"
            if only_errors and status != "error":
                continue
            logs.append(
                {
                    "id": f"log-{client}-{i}",
                    "type": "log",
                    "attributes": {
                        "timestamp": iso(ts),
                        "service": SERVICE,
                        "status": status,
                        "message": (
                            f"timeout ao consultar SPI apos 8000ms"
                            if status == "error"
                            else "transferencia processada"
                        ),
                        "attributes": {"usr": {"id": client}},
                    },
                }
            )
    logs.sort(key=lambda item: item["attributes"]["timestamp"])
    return logs[:limit]


# ------------------------------------------------------------------ handler


class Handler(BaseHTTPRequestHandler):
    def _json(self, payload: dict, status: int = 200, headers: dict | None = None) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self) -> bool:
        """As duas chaves são exigidas, como na API real."""
        return bool(
            self.headers.get("DD-API-KEY") and self.headers.get("DD-APPLICATION-KEY")
        )

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length))
        except ValueError:
            return {}

    def _should_fail(self) -> tuple[int, dict, dict] | None:
        """Modo de falha para exercitar o retry do cliente."""
        mode = STATE["fail_mode"]
        if not mode or STATE["fail_countdown"] <= 0:
            return None
        STATE["fail_countdown"] -= 1
        if mode == "429":
            return 429, {"errors": ["Rate limit exceeded"]}, {"Retry-After": "0"}
        if mode == "500":
            return 500, {"errors": ["Internal error"]}, {}
        return None

    def _handle(self) -> None:
        parsed = urlparse(self.path)
        path, params = parsed.path, parse_qs(parsed.query)
        body = self._body() if self.command in ("POST", "PUT") else {}
        STATE["requests"].append({"method": self.command, "path": path, "body": body})

        if not self._authorized():
            print(f"  ✗ {self.command:5} {path}  →  403 (faltou chave)")
            self._json(
                {"errors": ["Forbidden: missing or invalid DD-APPLICATION-KEY"]}, 403
            )
            return

        failure = self._should_fail()
        if failure:
            status, payload, headers = failure
            print(f"  ⟳ {self.command:5} {path}  →  {status} (modo de falha)")
            self._json(payload, status, headers)
            return

        handler = self._route(path)
        if handler is None:
            print(f"  ? {self.command:5} {path}  →  404")
            self._json({"errors": [f"no route: {path}"]}, 404)
            return

        payload, note = handler(params, body)
        print(f"  ✓ {self.command:5} {path}  →  {note}")
        self._json(payload)

    def _route(self, path: str):
        return {
            "/api/v1/validate": self._validate,
            "/api/v1/monitor": self._monitors,
            "/api/v1/events": self._events,
            "/api/v2/logs/events/search": self._logs_search,
            "/api/v2/logs/analytics/aggregate": self._logs_aggregate,
            "/api/v2/spans/events/search": self._spans,
            "/api/v2/rum/events/search": lambda p, b: ({"data": []}, "0 eventos de RUM"),
            "/api/v2/incidents": lambda p, b: ({"data": []}, "0 incidentes"),
        }.get(path)

    # ------------------------------------------------------------- endpoints

    def _validate(self, params, body):
        return {"valid": True}, "credencial aceita"

    def _monitors(self, params, body):
        states = params.get("group_states", [""])[0]
        monitors = [
            {
                "id": 4711,
                "name": f"[{SERVICE}] taxa de erro acima do limite",
                "overall_state": "Alert",
                "type": "log alert",
                "query": f"logs('service:{SERVICE} status:error').index('*').rollup('count').last('5m') > 10",
                "tags": [f"service:{SERVICE}", "team:pagamentos"],
            },
            {
                "id": 4712,
                "name": "[canal-digital] latência p99",
                "overall_state": "OK",
                "type": "metric alert",
                "tags": ["service:canal-digital"],
            },
        ]
        if "alert" in states:
            monitors = [m for m in monitors if m["overall_state"] == "Alert"]
        return monitors, f"{len(monitors)} monitores (filtro: {states or 'nenhum'})"

    def _events(self, params, body):
        start = datetime.fromtimestamp(int(params.get("start", ["0"])[0]), tz=timezone.utc)
        end = datetime.fromtimestamp(int(params.get("end", ["0"])[0]), tz=timezone.utc)
        deploy = INCIDENT_START - timedelta(minutes=9)
        events = (
            [
                {
                    "id": 991,
                    "date_happened": int(deploy.timestamp()),
                    "alert_type": "info",
                    "title": f"Deploy {SERVICE} v2.14",
                    "tags": [f"service:{SERVICE}"],
                }
            ]
            if start <= deploy <= end
            else []
        )
        return {"events": events}, f"{len(events)} evento(s) na janela"

    def _logs_search(self, params, body):
        f = body.get("filter", {})
        query = f.get("query", "*")
        start = parse_moment(f.get("from"), INCIDENT_START - timedelta(hours=1))
        end = parse_moment(f.get("to"), INCIDENT_START + timedelta(hours=1))
        limit = int((body.get("page") or {}).get("limit", 100))
        logs = make_logs(query, start, end, limit)
        uid = uid_in_query(query)
        return (
            {"data": logs, "meta": {"page": {"after": None}}},
            f"{len(logs)} logs | query={query!r}"
            + (f" | identidade={uid}" if uid else " | sem filtro de identidade"),
        )

    def _logs_aggregate(self, params, body):
        f = body.get("filter", {})
        start = parse_moment(f.get("from"), INCIDENT_START - timedelta(hours=1))
        end = parse_moment(f.get("to"), INCIDENT_START + timedelta(hours=1))
        group_by = body.get("group_by") or []
        facet = group_by[0].get("facet") if group_by else None
        limit = group_by[0].get("limit", 10) if group_by else 10

        if facet != "@usr.id":
            return {"data": []}, f"faceta {facet!r} não suportada pelo emulador"

        buckets = [
            {"by": {"@usr.id": client}, "computes": {"c0": count}}
            for client, count in AFFECTED.items()
            if start <= INCIDENT_START <= end
        ]
        buckets.sort(key=lambda b: b["computes"]["c0"], reverse=True)
        buckets = buckets[:limit]
        return {"data": buckets}, f"{len(buckets)} grupos por {facet} (limit={limit})"

    def _spans(self, params, body):
        attrs = (body.get("data") or {}).get("attributes") or {}
        f = attrs.get("filter", {})
        start = parse_moment(f.get("from"), INCIDENT_START - timedelta(hours=1))
        end = parse_moment(f.get("to"), INCIDENT_START + timedelta(hours=1))
        ts = INCIDENT_START + timedelta(seconds=1)
        spans = (
            [
                {
                    "id": "span-1",
                    "type": "spans",
                    "attributes": {
                        "start_timestamp": iso(ts),
                        "service": SERVICE,
                        "resource_name": "POST /api/pagamentos/transferencia",
                        "duration": 8_200_000_000,
                    },
                }
            ]
            if start <= ts <= end
            else []
        )
        return {"data": spans}, f"{len(spans)} span(s) na janela"

    do_GET = _handle
    do_POST = _handle
    do_PUT = _handle

    def log_message(self, *args: object) -> None:
        """Silencia o log padrão; o que interessa já é impresso em `_handle`."""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--fail-mode",
        choices=["429", "500"],
        help="devolve erro nas primeiras N requisições, para exercitar o retry",
    )
    parser.add_argument("--fail-count", type=int, default=2)
    args = parser.parse_args()

    STATE["fail_mode"] = args.fail_mode
    STATE["fail_countdown"] = args.fail_count if args.fail_mode else 0

    print(f"emulador da API do Datadog em http://127.0.0.1:{PORT}")
    print(f"cenário: falha de PIX em {iso(INCIDENT_START)}")
    print(f"clientes afetados: {AFFECTED}")
    if args.fail_mode:
        print(f"modo de falha: {args.fail_mode} nas primeiras {args.fail_count} requisições")
    print("\nexige DD-API-KEY e DD-APPLICATION-KEY — sem elas, 403\n")

    with ThreadingHTTPServer(("127.0.0.1", PORT), Handler) as server:
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print(f"\nencerrado. {len(STATE['requests'])} requisições recebidas.")


if __name__ == "__main__":
    main()
