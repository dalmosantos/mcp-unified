"""CLI do servidor."""

from __future__ import annotations

import argparse
import sys

from .config import Settings
from .server import build_server, configure_logging, get_context
from .toolsets import ALL_TOOLSETS, PROFILES, ToolsetResolutionError


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mcp-unified",
        description=(
            "Servidor MCP unificado sobre FullStory, Datadog, ServiceNow e Microsoft Graph, "
            "com correlação entre sessão de usuário e telemetria de backend."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "perfis disponíveis:\n"
            + "\n".join(f"  {name:<12} {', '.join(sets)}" for name, sets in PROFILES.items())
            + "\n\ntoolsets disponíveis:\n"
            + "\n".join(
                f"  {name:<28} {info.description}" for name, info in ALL_TOOLSETS.items()
            )
        ),
    )
    parser.add_argument(
        "--transport",
        choices=["stdio", "streamable-http", "sse"],
        default="stdio",
        help="stdio para a IDE (padrão); streamable-http para consumo remoto",
    )
    parser.add_argument("--profile", help="perfil de toolsets (padrão: ide)")
    parser.add_argument(
        "--toolsets",
        help="lista explícita separada por vírgula; tem precedência sobre --profile",
    )
    parser.add_argument("--host", default="0.0.0.0", help="host no modo HTTP")
    parser.add_argument("--port", type=int, default=8080, help="porta no modo HTTP")
    parser.add_argument(
        "--safe-mode",
        action="store_true",
        default=None,
        help="remove todos os toolsets de escrita, independente do perfil",
    )
    parser.add_argument("--log-level", default="INFO", help="DEBUG, INFO, WARNING, ERROR")
    parser.add_argument(
        "--list-tools",
        action="store_true",
        help="lista o que seria registrado e sai, sem subir o servidor",
    )
    return parser


def main() -> int:
    args = _parser().parse_args()
    configure_logging(args.log_level)

    try:
        server = build_server(
            profile=args.profile, toolsets=args.toolsets, safe_mode=args.safe_mode
        )
    except ToolsetResolutionError as exc:
        print(f"erro: {exc}", file=sys.stderr)
        return 2

    if args.list_tools:
        _print_inventory(server)
        return 0

    if args.transport == "stdio":
        server.run(transport="stdio")
    else:
        server.run(transport=args.transport, host=args.host, port=args.port)
    return 0


def _print_inventory(server: object) -> None:
    """Relatório em stdout — aqui é seguro, porque o servidor não vai subir."""
    ctx = get_context(server)  # type: ignore[arg-type]
    settings: Settings = ctx.settings

    print(f"toolsets ativos : {', '.join(sorted(ctx.enabled_toolsets)) or 'nenhum'}")
    print(f"tools registradas: {len(ctx.registered_tools)}")
    print(f"fontes de timeline: {', '.join(ctx.timeline_source_names()) or 'nenhuma'}")
    print(
        f"resolvedores de identidade: "
        f"{', '.join(r.source_name for r in ctx.subject_resolvers) or 'nenhum'}"
    )

    print("\nprovedores:")
    for name, configured in settings.configured_providers().items():
        mark = "✓" if configured else "·"
        reason = ctx.disabled.get(name, "")
        print(f"  {mark} {name:<12}{(' — ' + reason) if reason else ''}")

    if ctx.registered_tools:
        print("\ntools:")
        for name in sorted(ctx.registered_tools):
            print(f"  {name}")


if __name__ == "__main__":
    raise SystemExit(main())
