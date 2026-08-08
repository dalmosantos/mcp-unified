"""CLI do servidor."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

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
            + "\n".join(f"  {name:<28} {info.description}" for name, info in ALL_TOOLSETS.items())
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
        "--env-file",
        help=(
            "arquivo de credenciais a carregar. Use nas IDEs: cada cliente MCP "
            "injeta variável de ambiente de um jeito e o diretório de trabalho "
            "do processo é imprevisível. Padrão: .env no diretório atual"
        ),
    )
    parser.add_argument(
        "--secrets-dir",
        help=(
            "diretório com um arquivo por credencial (DD_API_KEY, ...), como "
            "Docker e Kubernetes montam em /run/secrets. Use no transporte HTTP, "
            "onde um .env em texto plano não é aceitável. Ganha do --env-file; "
            "perde para variável de ambiente"
        ),
    )
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

    # Falhar aqui, alto e claro. Um caminho errado carregaria zero credencial e
    # o sintoma seria "todos os toolsets desabilitados" — diagnóstico caro numa
    # IDE, onde o stderr do servidor costuma estar escondido.
    if args.env_file and not Path(args.env_file).is_file():
        print(f"erro: --env-file não encontrado: {args.env_file}", file=sys.stderr)
        return 2
    if args.secrets_dir and not Path(args.secrets_dir).is_dir():
        print(f"erro: --secrets-dir não é um diretório: {args.secrets_dir}", file=sys.stderr)
        return 2

    try:
        settings = Settings(env_file=args.env_file, secrets_dir=args.secrets_dir)
        server = build_server(
            profile=args.profile,
            toolsets=args.toolsets,
            safe_mode=args.safe_mode,
            settings=settings,
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
