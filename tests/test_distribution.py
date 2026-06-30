from __future__ import annotations

import asyncio
import json
import zipfile
from pathlib import Path

from neo_localmcp import client_setup
from neo_localmcp.server import mcp


def test_codex_app_and_cli_share_config():
    assert client_setup._codex_cli_config_path() == client_setup._codex_desktop_config_path()


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
