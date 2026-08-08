"""As skills não podem citar tool que não existe.

Skill é documentação que o modelo lê como verdade. Uma tool renomeada e uma
skill esquecida produzem um agente que tenta chamar algo inexistente e conclui
que a ferramenta está quebrada. Este teste liga as duas coisas.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from mcp import Client

from mcp_unified.server import build_server

SKILLS_DIR = Path(__file__).parent.parent / "skills"

# Nome completo entre crases ou seguido de `(`. O prefixo restringe a coisas
# que se parecem com tool deste servidor, e não com parâmetro ou campo.
_CITATION = re.compile(r"`([a-z][a-z0-9_]{6,})`|\b([a-z][a-z0-9_]{6,})\(")
_TOOL_PREFIXES = {
    "datadog",
    "fullstory",
    "servicenow",
    "msgraph",
    "build",
    "find",
    "analyze",
    "correlate",
    "nl",
}

ALL_ENV = {
    "FULLSTORY_API_KEY": "x",
    "DD_API_KEY": "x",
    "DD_APP_KEY": "x",
    "SNOW_INSTANCE": "x",
    "SNOW_USERNAME": "x",
    "SNOW_PASSWORD": "x",
    "MSGRAPH_TENANT_ID": "x",
    "MSGRAPH_CLIENT_ID": "x",
    "MSGRAPH_CLIENT_SECRET": "x",
    "MCP_LLM_PROVIDER": "openai-compat",
    "MCP_LLM_BASE_URL": "http://fake/v1",
}


def skill_files() -> list[Path]:
    return sorted(SKILLS_DIR.glob("*/SKILL.md"))


def cited_tools(text: str) -> set[str]:
    found = set()
    for match in _CITATION.finditer(text):
        name = match.group(1) or match.group(2)
        if "_" in name and not name.endswith("_") and name.split("_")[0] in _TOOL_PREFIXES:
            found.add(name)
    return found


def test_existem_skills():
    assert skill_files(), "nenhuma skill encontrada em skills/"


@pytest.mark.parametrize("path", skill_files(), ids=lambda p: p.parent.name)
def test_frontmatter_valido(path: Path):
    text = path.read_text(encoding="utf-8")
    match = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    assert match, f"{path}: sem frontmatter YAML"

    front = match.group(1)
    name = re.search(r"^name:\s*(.+)$", front, re.M)
    description = re.search(r"^description:\s*(.+)$", front, re.M)

    assert name, f"{path}: frontmatter sem `name`"
    assert description, f"{path}: frontmatter sem `description`"
    assert name.group(1).strip() == path.parent.name, (
        f"{path}: `name` precisa bater com o nome da pasta"
    )
    # A description é o que faz a skill disparar na hora certa; curta demais
    # significa gatilho fraco.
    assert len(description.group(1)) >= 120, (
        f"{path}: description curta demais para servir de gatilho"
    )


@pytest.mark.parametrize("path", skill_files(), ids=lambda p: p.parent.name)
async def test_tools_citadas_existem(path: Path, monkeypatch: pytest.MonkeyPatch):
    """O teste que impede a skill de apodrecer junto com um rename."""
    for key, value in ALL_ENV.items():
        monkeypatch.setenv(key, value)

    async with Client(build_server(profile="all")) as client:
        real = {t.name for t in (await client.list_tools()).tools}

    missing = sorted(cited_tools(path.read_text(encoding="utf-8")) - real)
    assert not missing, (
        f"{path.parent.name} cita tools inexistentes: {missing}. "
        "Renomeou uma tool? Atualize a skill."
    )


@pytest.mark.parametrize("path", skill_files(), ids=lambda p: p.parent.name)
async def test_skill_cabe_no_perfil_que_as_ides_carregam(path: Path, monkeypatch):
    """Uma skill não pode depender de tool fora do perfil que as IDEs sobem.

    `.mcp.json`, `.vscode/mcp.json` e `.agents/mcp_config.json` carregam
    `sre-agent`. Uma skill que cite tool de fora dele passa no teste de
    existência e ainda assim não funciona para o time — foi exatamente o que
    aconteceu quando `sre-business-impact` chegou pedindo Product Analytics.

    A exceção são os dois toolsets legitimamente opcionais: `datadog-apm` (nem
    toda conta tem APM) e `llm` (exige provedor de modelo configurado). Skills
    podem citá-los, mas só sob condicional — e por isso o allowlist é derivado
    dos toolsets, não escrito à mão.
    """
    for key, value in ALL_ENV.items():
        monkeypatch.setenv(key, value)

    async with Client(build_server(profile="sre-agent")) as client:
        base = {t.name for t in (await client.list_tools()).tools}
    async with Client(build_server(toolsets="datadog-apm,llm")) as client:
        opcionais = {t.name for t in (await client.list_tools()).tools}

    fora = sorted(cited_tools(path.read_text(encoding="utf-8")) - base - opcionais)
    assert not fora, (
        f"{path.parent.name} depende de tools fora do perfil `sre-agent`: {fora}. "
        "Inclua o toolset no perfil (toolsets.py) ou torne o uso condicional."
    )


def test_skills_estao_expostas_no_padrao_agents():
    """`.agents/skills/` é o que leva as skills para fora do Claude Code.

    Devin CLI e Antigravity leem esse diretório. Como são links simbólicos para
    `skills/`, o modo de falha real não é divergência de conteúdo — é um clone
    no Windows sem `core.symlinks`, onde o link vira um arquivo de texto com o
    caminho dentro. Comparar o conteúdo pega os dois casos de uma vez.
    """
    espelho = Path(__file__).parent.parent / ".agents" / "skills"
    assert espelho.is_dir(), ".agents/skills/ não existe — as skills não saem do Claude Code"

    for origem in skill_files():
        copia = espelho / origem.parent.name / "SKILL.md"
        assert copia.is_file(), (
            f"{origem.parent.name} não aparece em .agents/skills/. "
            "Adicionou uma skill? Crie o link: "
            f"ln -s ../../skills/{origem.parent.name} .agents/skills/{origem.parent.name}"
        )
        assert copia.read_text(encoding="utf-8") == origem.read_text(encoding="utf-8"), (
            f".agents/skills/{origem.parent.name} divergiu de skills/. "
            "Num clone Windows sem `git config core.symlinks true`, o link vira "
            "um arquivo de texto com o caminho dentro."
        )


def test_readme_das_skills_lista_todas():
    readme = (SKILLS_DIR / "README.md").read_text(encoding="utf-8")
    for path in skill_files():
        assert path.parent.name in readme, f"{path.parent.name} não aparece em skills/README.md"


# ------------------------------------------------- empacotamento como plugin

PLUGIN_FILES = [
    Path(__file__).parent.parent / ".claude-plugin" / "plugin.json",
    Path(__file__).parent.parent / ".claude-plugin" / "marketplace.json",
    Path(__file__).parent.parent / ".mcp.json",
]

# Configurações versionadas, uma por cliente MCP. A chave externa muda entre
# eles — o VS Code usa `servers`, o resto usa `mcpServers`.
IDE_CONFIGS = {
    ".mcp.json": "mcpServers",
    ".vscode/mcp.json": "servers",
    ".agents/mcp_config.json": "mcpServers",
    ".devin/mcp_config.json": "mcpServers",
}


@pytest.mark.parametrize("path", PLUGIN_FILES, ids=lambda p: p.name)
def test_manifest_e_json_valido(path: Path):
    import json

    assert path.exists(), f"{path} não existe"
    json.loads(path.read_text(encoding="utf-8"))


def test_plugin_e_marketplace_concordam():
    import json

    root = Path(__file__).parent.parent
    plugin = json.loads((root / ".claude-plugin" / "plugin.json").read_text())
    market = json.loads((root / ".claude-plugin" / "marketplace.json").read_text())
    listed = {p["name"] for p in market["plugins"]}
    assert plugin["name"] in listed, (
        f"plugin.json declara '{plugin['name']}', ausente do marketplace.json"
    )


@pytest.mark.parametrize("rel,chave", sorted(IDE_CONFIGS.items()))
def test_config_de_ide_aponta_para_o_perfil_certo(rel: str, chave: str):
    """As três configurações versionadas precisam concordar entre si.

    Divergir aqui é caro: o time descobre pela ausência de tools no meio de um
    incidente, não por um erro na subida.
    """
    import json

    path = Path(__file__).parent.parent / rel
    assert path.exists(), f"{rel} não existe"

    servers = json.loads(path.read_text(encoding="utf-8"))[chave]
    assert "mcp-unified" in servers, f"{rel}: servidor não se chama 'mcp-unified'"
    args = servers["mcp-unified"]["args"]

    assert args[args.index("--profile") + 1] == "sre-agent", (
        f"{rel}: perfil precisa ser `sre-agent` — é o que as cinco skills exigem"
    )
    assert "--env-file" in args, (
        f"{rel}: sem --env-file, o servidor depende do diretório de trabalho "
        "que a IDE escolher e sobe sem credencial nenhuma"
    )


@pytest.mark.parametrize(
    "path",
    sorted((Path(__file__).parent.parent / "agents").glob("*.md")),
    ids=lambda p: p.stem,
)
async def test_subagente_declara_tools_que_existem(path: Path, monkeypatch):
    """Um subagente que declara tool inexistente falha em silêncio no runtime."""
    for key, value in ALL_ENV.items():
        monkeypatch.setenv(key, value)

    front = re.match(r"^---\n(.*?)\n---\n", path.read_text(encoding="utf-8"), re.S)
    assert front, f"{path}: sem frontmatter"
    declared = re.findall(r"^\s+-\s+([a-z][a-z0-9_]+)\s*$", front.group(1), re.M)
    assert declared, f"{path}: nenhuma tool declarada"

    async with Client(build_server(profile="all")) as client:
        real = {t.name for t in (await client.list_tools()).tools}

    missing = sorted(set(declared) - real)
    assert not missing, f"{path.stem} declara tools inexistentes: {missing}"
