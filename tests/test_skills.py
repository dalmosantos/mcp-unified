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
    "datadog", "fullstory", "servicenow", "msgraph",
    "build", "find", "analyze", "correlate", "nl",
}

ALL_ENV = {
    "FULLSTORY_API_KEY": "x", "DD_API_KEY": "x", "DD_APP_KEY": "x",
    "SNOW_INSTANCE": "x", "SNOW_USERNAME": "x", "SNOW_PASSWORD": "x",
    "MSGRAPH_TENANT_ID": "x", "MSGRAPH_CLIENT_ID": "x", "MSGRAPH_CLIENT_SECRET": "x",
    "MCP_LLM_PROVIDER": "openai-compat", "MCP_LLM_BASE_URL": "http://fake/v1",
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


def test_readme_das_skills_lista_todas():
    readme = (SKILLS_DIR / "README.md").read_text(encoding="utf-8")
    for path in skill_files():
        assert path.parent.name in readme, (
            f"{path.parent.name} não aparece em skills/README.md"
        )


# ------------------------------------------------- empacotamento como plugin

PLUGIN_FILES = [
    Path(__file__).parent.parent / ".claude-plugin" / "plugin.json",
    Path(__file__).parent.parent / ".claude-plugin" / "marketplace.json",
    Path(__file__).parent.parent / ".mcp.json",
]


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


@pytest.mark.parametrize(
    "path", sorted((Path(__file__).parent.parent / "agents").glob("*.md")),
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
