from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
from importlib.resources import files
from pathlib import Path
from typing import Any

from .config import APP_DIR, CONFIG_PATH, ensure_config
from .identity import IDENTITY


def _command_templates() -> list[tuple[str, str]]:
    base = files("neo_localmcp") / "templates" / "claude-code" / "commands" / IDENTITY.slash_prefix
    out: list[tuple[str, str]] = []
    for item in sorted(base.iterdir(), key=lambda p: p.name):
        if item.name.endswith(".md"):
            out.append((item.name, item.read_text(encoding="utf-8")))
    return out


def _python_command() -> str:
    return sys.executable


def _toml_string(value: str | Path) -> str:
    return str(value).replace("\\", "\\\\").replace('"', '\\"')


def _mcp_server_block() -> dict[str, Any]:
    return {
        IDENTITY.mcp_server_name: {
            "command": _python_command(),
            "args": ["-m", "neo_localmcp.server"],
            "env": {"NEO_LOCALMCP_CONFIG": str(CONFIG_PATH)},
        }
    }


def _codex_block() -> str:
    return f"""
# BEGIN neo-localmcp
[mcp_servers.{IDENTITY.mcp_server_name}]
command = "{_toml_string(_python_command())}"
args = ["-m", "neo_localmcp.server"]

[mcp_servers.{IDENTITY.mcp_server_name}.env]
NEO_LOCALMCP_CONFIG = "{_toml_string(CONFIG_PATH)}"
# END neo-localmcp
""".strip() + "\n"


def _replace_marked_block(old: str, block: str, start: str = "# BEGIN neo-localmcp", end: str = "# END neo-localmcp") -> str:
    if start in old and end in old:
        before = old.split(start, 1)[0].rstrip()
        after = old.split(end, 1)[1].lstrip()
        prefix = before + "\n\n" if before else ""
        suffix = "\n" + after if after else ""
        return prefix + block + suffix
    return old.rstrip() + "\n\n" + block if old.strip() else block


def _write_codex_config(path: Path, apply: bool) -> dict[str, Any]:
    block = _codex_block()
    if apply:
        path.parent.mkdir(parents=True, exist_ok=True)
        old = path.read_text(encoding="utf-8") if path.exists() else ""
        path.write_text(_replace_marked_block(old, block), encoding="utf-8")
    return {"config_path": str(path), "exists_after": path.exists() if apply else path.exists(), "block": block}


def setup_claude_code(apply: bool = True) -> dict[str, Any]:
    ensure_config()
    target = Path.home() / ".claude" / "commands" / IDENTITY.slash_prefix
    actions: list[str] = []
    if apply:
        target.mkdir(parents=True, exist_ok=True)
        for name, text in _command_templates():
            (target / name).write_text(text, encoding="utf-8")
        actions.append(f"installed slash commands to {target}")
        claude = shutil.which("claude")
        if claude:
            # User scope is best for repeatable Claude Code sessions. If the CLI is older and does not support
            # --scope, fall back to the classic command.
            cmd = [claude, "mcp", "add", "--scope", "user", IDENTITY.mcp_server_name, "--", _python_command(), "-m", "neo_localmcp.server"]
            result = subprocess.run(cmd, capture_output=True, text=True, errors="replace")
            if result.returncode != 0:
                fallback = [claude, "mcp", "add", IDENTITY.mcp_server_name, "--", _python_command(), "-m", "neo_localmcp.server"]
                result = subprocess.run(fallback, capture_output=True, text=True, errors="replace")
                actions.append(f"ran claude mcp add fallback: exit {result.returncode}")
            else:
                actions.append(f"ran claude mcp add --scope user: exit {result.returncode}")
            if result.stderr.strip():
                actions.append(result.stderr.strip()[:500])
        else:
            actions.append("claude CLI not found; slash commands were still installed")
    return {
        "client": "claude-code",
        "applied": apply,
        "target": str(target),
        "actions": actions,
        "manual_mcp_user": f"claude mcp add --scope user {IDENTITY.mcp_server_name} -- {_python_command()} -m neo_localmcp.server",
        "manual_mcp_fallback": f"claude mcp add {IDENTITY.mcp_server_name} -- {_python_command()} -m neo_localmcp.server",
    }


def _claude_desktop_config_path() -> Path:
    system = platform.system().lower()
    if system == "windows":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
        return base / "Claude" / "claude_desktop_config.json"
    if system == "darwin":
        return Path.home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
    return Path.home() / ".config" / "Claude" / "claude_desktop_config.json"


def setup_claude_desktop(apply: bool = True) -> dict[str, Any]:
    ensure_config()
    package_path = APP_DIR / "neo-localmcp.mcpb"
    return {
        "client": "claude-desktop",
        "applied": False,
        "package_path": str(package_path),
        "package_exists": package_path.exists(),
        "manual_install_required": True,
        "instructions": "In Claude Desktop open Settings > Extensions > Advanced settings > Install Extension, then select neo-localmcp.mcpb.",
        "note": "Direct claude_desktop_config.json editing is intentionally no longer performed.",
    }


def _codex_cli_config_path() -> Path:
    return Path.home() / ".codex" / "config.toml"


def _codex_desktop_config_path() -> Path:
    # Codex app, CLI, and IDE share CODEX_HOME/config.toml.
    return _codex_cli_config_path()


def setup_codex_cli(apply: bool = True) -> dict[str, Any]:
    ensure_config()
    path = _codex_cli_config_path()
    return {"client": "codex-cli", "applied": apply, **_write_codex_config(path, apply)}


def setup_codex_desktop(apply: bool = True) -> dict[str, Any]:
    result = setup_codex_cli(apply)
    return {**result, "client": "codex-desktop", "shared_with_cli": True, "restart_required": True}


def setup_codex(apply: bool = True) -> dict[str, Any]:
    return {"client": "codex", "applied": apply, "shared_config": True, "result": setup_codex_cli(apply)}


def client_status() -> dict[str, Any]:
    claude_cli = shutil.which("claude")
    codex_cli = shutil.which("codex")
    paths = {
        "claude_code_commands": str(Path.home() / ".claude" / "commands" / IDENTITY.slash_prefix),
        "claude_desktop_config": str(_claude_desktop_config_path()),
        "codex_cli_config": str(_codex_cli_config_path()),
        "codex_desktop_config": str(_codex_desktop_config_path()),
    }
    return {
        "product": IDENTITY.product_name,
        "python_command": _python_command(),
        "config_path": str(CONFIG_PATH),
        "commands_found": {"claude": claude_cli, "codex": codex_cli},
        "paths": {name: {"path": path, "exists": Path(path).exists()} for name, path in paths.items()},
        "mcp_server_block": _mcp_server_block(),
        "codex_block": _codex_block(),
    }


def setup_client(client: str, apply: bool = True) -> dict[str, Any]:
    key = client.lower().replace("_", "-")
    if key == "all":
        return {"client": "all", "applied": apply, "results": setup_clients(None, apply=apply)}
    if key in {"claude-code", "claude"}:
        return setup_claude_code(apply=apply)
    if key in {"claude-desktop", "desktop"}:
        return setup_claude_desktop(apply=apply)
    if key == "codex":
        return setup_codex(apply=apply)
    if key == "codex-cli":
        return setup_codex_cli(apply=apply)
    if key == "codex-desktop":
        return setup_codex_desktop(apply=apply)
    raise ValueError(f"Unknown client: {client}. Expected all, claude-code, claude-desktop, codex, codex-cli, or codex-desktop.")


def setup_clients(clients: list[str] | None = None, apply: bool = True) -> list[dict[str, Any]]:
    selected = clients or ["claude-code", "claude-desktop", "codex"]
    if any(str(client).lower().replace("_", "-") == "all" for client in selected):
        selected = ["claude-code", "claude-desktop", "codex"]
    results = []
    for client in selected:
        try:
            results.append(setup_client(client, apply=apply))
        except Exception as exc:
            results.append({"client": client, "applied": apply, "ok": False, "error": str(exc)})
    return results
