from __future__ import annotations

import asyncio
import io
import json
import re
import sys
import zipfile
from pathlib import Path

from neo_localmcp import client_setup
from neo_localmcp import context_worker
from neo_localmcp.server import mcp


def test_codex_app_and_cli_share_config():
    assert client_setup._codex_cli_config_path() == client_setup._codex_desktop_config_path()


def test_client_configs_use_stable_server_launcher():
    block = client_setup._mcp_server_block()["neo-localmcp"]
    assert "neo-localmcp-server" in block["command"]
    assert block["args"] == []
    assert "neo-localmcp-server" in client_setup._codex_block()


def test_claude_manual_command_uses_stable_launcher(monkeypatch):
    monkeypatch.setattr(client_setup.shutil, "which", lambda name: None)
    result = client_setup.setup_claude_code(apply=False)
    assert "neo-localmcp-server" in result["manual_mcp_user"]


def test_mcp_surface_is_small_and_intentional():
    names = asyncio.run(_tool_names())
    assert names == {
        "prepare_context", "context_prepare", "file_excerpts", "repo_lookup",
        "record_change", "ollama_status", "ollama_ensure",
    }


async def _tool_names() -> set[str]:
    return {tool.name for tool in await mcp.list_tools()}


def test_built_mcpb_contains_valid_manifest():
    bundle = Path(__file__).parents[1] / "packages" / "claude-desktop" / "neo-localmcp.mcpb"
    assert bundle.exists()
    with zipfile.ZipFile(bundle) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        names = set(archive.namelist())
    assert manifest["manifest_version"] == "0.4"
    assert manifest["server"]["type"] == "uv"
    assert "server.py" in names
    assert "neo_localmcp/server.py" in {name.replace("\\", "/") for name in names}


def test_context_worker_forces_utf8_stdout(monkeypatch):
    raw = io.BytesIO()
    stdout = io.TextIOWrapper(raw, encoding="cp1252")
    monkeypatch.setattr(sys, "stdout", stdout)

    context_worker._configure_utf8_stdio()
    sys.stdout.write("source → target")
    sys.stdout.flush()

    assert sys.stdout.encoding.lower() == "utf-8"
    assert raw.getvalue().decode("utf-8") == "source → target"


def test_claude_commands_have_distinct_menu_metadata():
    root = Path(__file__).parents[1]
    templates = root / "neo_localmcp" / "templates" / "claude-code" / "commands" / "neo-localmcp"
    distribution = root / "packages" / "claude-code" / "commands" / "neo-localmcp"
    descriptions = []

    for template in sorted(templates.glob("*.md")):
        text = template.read_text(encoding="utf-8")
        match = re.match(r'^---\ndescription: (.+)\nargument-hint: "(.+)"\n---\n', text)
        assert match, f"Missing Claude command menu metadata: {template.name}"
        descriptions.append(match.group(1))
        assert (distribution / template.name).read_text(encoding="utf-8") == text

    assert len(descriptions) == 10
    assert len(set(descriptions)) == len(descriptions)
