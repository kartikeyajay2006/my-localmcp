from __future__ import annotations

import asyncio
import io
import json
import os
import re
import sys
import zipfile
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
import pytest

from neo_localmcp import ai_client_config as client_setup
from neo_localmcp.mcp import context_worker
from neo_localmcp import __version__
from neo_localmcp import repo_utils as utils
from neo_localmcp.mcp.server import mcp


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


def test_client_blocks_honor_injected_server_command_and_config():
    launcher = Path("/opt/.neo-localmcp/venv/bin/neo-localmcp-server")
    config = Path("/opt/.neo-localmcp/config/config.yaml")
    block = client_setup._mcp_server_block(server_command=launcher, config_path=config)["neo-localmcp"]
    assert block["command"] == str(launcher)
    assert block["env"]["NEO_LOCALMCP_CONFIG"] == str(config)
    codex = client_setup._codex_block(server_command=launcher, config_path=config)
    assert str(launcher).replace("\\", "\\\\") in codex
    assert str(config).replace("\\", "\\\\") in codex


def test_mcp_surface_is_small_and_intentional():
    names = asyncio.run(_tool_names())
    assert names == {
        "prepare_context", "context_prepare", "file_excerpts", "repo_lookup",
        "record_change", "repo_status", "doctor", "refresh_index", "summarize_file",
        "apply_patch", "ollama_status", "ollama_ensure",
    }


async def _tool_names() -> set[str]:
    return {tool.name for tool in await mcp.list_tools()}


@pytest.mark.serial
def test_repo_tools_respond_over_real_stdio(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "module.py").write_text("def MainViewModel():\n    return 1\n", encoding="utf-8")
    asyncio.run(_assert_repo_tools_respond(repo, tmp_path / "app"))


async def _assert_repo_tools_respond(repo: Path, app_home: Path) -> None:
    root = Path(__file__).parents[1]
    env = {**os.environ, "NEO_LOCALMCP_HOME": str(app_home), "PYTHONPATH": str(root)}
    params = StdioServerParameters(command=sys.executable, args=["-m", "neo_localmcp.mcp.server"], env=env)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            calls = [
                ("refresh_index", {"repo_root": str(repo), "force": False}),
                ("repo_lookup", {"query": "MainViewModel", "repo_root": str(repo), "limit": 5}),
                ("apply_patch", {"patch_text": "not a patch", "repo_root": str(repo), "check_only": True}),
            ]
            for name, arguments in calls:
                result = await asyncio.wait_for(session.call_tool(name, arguments), timeout=5)
                assert result.content, f"{name} returned no MCP content"


def test_built_mcpb_contains_valid_manifest():
    bundle = Path(__file__).parents[1] / "packages" / "claude-desktop" / f"neo-localmcp-v{__version__}.mcpb"
    assert bundle.exists()
    with zipfile.ZipFile(bundle) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        names = set(archive.namelist())
    assert manifest["manifest_version"] == "0.4"
    assert manifest["version"] == __version__
    assert manifest["server"]["type"] == "uv"
    assert "server.py" in names
    assert "neo_localmcp/mcp/server.py" in {name.replace("\\", "/") for name in names}


def test_built_mcpb_embeds_current_package_bytes():
    root = Path(__file__).parents[1]
    bundle = root / "packages" / "claude-desktop" / f"neo-localmcp-v{__version__}.mcpb"
    with zipfile.ZipFile(bundle) as archive:
        names = {name.replace("\\", "/"): name for name in archive.namelist()}
        for source in sorted((root / "neo_localmcp").rglob("*")):
            if not source.is_file() or "__pycache__" in source.parts:
                continue
            relative = source.relative_to(root).as_posix()
            assert relative in names, f"Bundle is missing {relative}"
            packaged = archive.read(names[relative]).replace(b"\r\n", b"\n")
            checkout = source.read_bytes().replace(b"\r\n", b"\n")
            assert packaged == checkout, f"Bundle contains stale bytes for {relative}"


def test_context_worker_forces_utf8_stdout(monkeypatch):
    raw = io.BytesIO()
    stdout = io.TextIOWrapper(raw, encoding="cp1252")
    monkeypatch.setattr(sys, "stdout", stdout)

    context_worker._configure_utf8_stdio()
    sys.stdout.write("source → target")
    sys.stdout.flush()

    assert sys.stdout.encoding.lower() == "utf-8"
    assert raw.getvalue().decode("utf-8") == "source → target"


def test_helper_commands_never_inherit_protocol_stdin(monkeypatch):
    captured = {}

    class Completed:
        returncode = 0
        stdout = "ok"
        stderr = ""

    def fake_run(*args, **kwargs):
        captured.update(kwargs)
        return Completed()

    monkeypatch.setattr(utils.subprocess, "run", fake_run)
    result = utils.run_command(["git", "status"])
    assert result["returncode"] == 0
    assert captured["stdin"] is utils.subprocess.DEVNULL
    if os.name == "nt":
        assert captured["creationflags"] & utils.subprocess.CREATE_NO_WINDOW


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
