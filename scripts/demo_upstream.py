#!/usr/bin/env python3
"""Servidor falso das APIs upstream, para exercitar o projeto sem conta real.

Serve os mesmos fixtures usados nos testes — o cenário de falha de PIX — nos
caminhos que a FullStory, o Datadog e o ServiceNow usariam. Com ele, dá para
chamar as tools de correlação de verdade e ver a timeline sair, sem credencial
nenhuma.

    # terminal 1
    python scripts/demo_upstream.py

    # terminal 2
    source scripts/demo.env
    mcp-unified --profile all --list-tools

Não é mock de teste: é um servidor HTTP de verdade, e o cliente do projeto faz
requisições de verdade contra ele. O que muda é só para onde aponta.
"""

from __future__ import annotations

import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

FIXTURES = Path(__file__).resolve().parent.parent / "tests" / "fixtures"
PORT = 8931


def load(*parts: str) -> dict:
    return json.loads(FIXTURES.joinpath(*parts).read_text(encoding="utf-8"))


# Caminho (regex) → fixture. A ordem importa: o primeiro que casar vence.
ROUTES: list[tuple[re.Pattern[str], tuple[str, ...]]] = [
    (re.compile(r"/v2/sessions/.+/events"), ("fullstory", "session_pix_failure.json")),
    (re.compile(r"/v1/sessions"), ("fullstory", "sessions_list.json")),
    (re.compile(r"/api/v2/logs/events/search"), ("datadog", "logs_pix_errors.json")),
    (re.compile(r"/api/v2/logs/analytics/aggregate"), ("datadog", "aggregate_logs_by_user.json")),
    (re.compile(r"/api/v2/spans/events/search"), ("datadog", "spans_pix_slow.json")),
    (re.compile(r"/api/v1/events"), ("datadog", "events_deploy.json")),
    (re.compile(r"/api/now/table/change_request"), ("servicenow", "change_request_pix.json")),
    (re.compile(r"/api/now/table/incident"), ("servicenow", "incident_pix.json")),
]

EMPTY_BY_PREFIX: dict[str, dict] = {
    "/api/v2/rum": {"data": []},
    "/api/now/table": {"result": []},
    "/segments/v1": {"segments": []},
    "/api/v2": {"data": []},
    "/api/v1": {"events": []},
}


class Handler(BaseHTTPRequestHandler):
    def _respond(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _handle(self) -> None:
        path = self.path.split("?", 1)[0]

        for pattern, fixture in ROUTES:
            if pattern.search(path):
                self._respond(load(*fixture))
                print(f"  {self.command:6} {path}  →  {'/'.join(fixture)}")
                return

        for prefix, empty in EMPTY_BY_PREFIX.items():
            if path.startswith(prefix):
                self._respond(empty)
                print(f"  {self.command:6} {path}  →  (vazio)")
                return

        self._respond({"error": "sem fixture para este caminho", "path": path}, status=404)
        print(f"  {self.command:6} {path}  →  404")

    do_GET = _handle
    do_POST = _handle
    do_PUT = _handle
    do_DELETE = _handle

    def log_message(self, *args: object) -> None:
        """Silencia o log padrão; já imprimimos o que interessa em `_handle`."""


def main() -> None:
    print(f"upstream de demonstração em http://127.0.0.1:{PORT}")
    print("cenário: falha de transferência PIX (os mesmos fixtures dos testes)\n")
    print("noutro terminal:  source scripts/demo.env\n")
    with ThreadingHTTPServer(("127.0.0.1", PORT), Handler) as server:
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("\nencerrado.")


if __name__ == "__main__":
    main()
