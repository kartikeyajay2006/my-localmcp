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
from .branding import IDENTITY
from .repo_utils import hidden_subprocess_kwargs


def _command_templates() -> list[tuple[str, str]]:
    # packaged slash-command markdown -> (filename, text) pairs, for installing into ~/.claude/commands
    base = files("neo_localmcp") / "templates" / "claude-code" / "commands" / IDENTITY.slash_prefix
    out: list[tuple[str, str]] = []
    for item in sorted(base.iterdir(), key=lambda p: p.name):
        if item.name.endswith(".md"):
            out.append((item.name, item.read_text(encoding="utf-8")))
    return out


def _python_command() -> str:
    return sys.executable


def _default_server_command() -> str:
    suffix = ".cmd" if platform.system().lower() == "windows" else ""
    return str(APP_DIR / "bin" / f"neo-localmcp-server{suffix}")


def _server_command(server_command: str | Path | None = None) -> str:
    # explicit launcher (installer passes the managed venv's executable) wins; only the legacy no-injected-value CLI path falls back to the old bin/ shim
    if server_command is not None:
        return str(server_command)
    return _default_server_command()


def _config_value(config_path: str | Path | None = None) -> str:
    return str(config_path) if config_path is not None else str(CONFIG_PATH)


def _toml_string(value: str | Path) -> str:
    # escapes a value for embedding in a TOML double-quoted string
    return str(value).replace("\\", "\\\\").replace('"', '\\"')


def _read_config_for_edit(path: Path) -> tuple[str, str]:
    # (LF-normalized text, detected newline style); reads raw bytes since text-mode would translate CRLF to LF before we could detect it
    if not path.exists():
        return "", "\n"
    raw = path.read_bytes()
    newline = "\r\n" if b"\r\n" in raw else "\n"
    text = raw.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
    return text, newline


def _atomic_write_text(path: Path, text: str, newline: str = "\n") -> None:
    # tmp-write + os.replace so a crash mid-write can't leave a half-written registration; re-expands to the file's own newline style to avoid rewriting every line on a CRLF/LF mismatch
    path.parent.mkdir(parents=True, exist_ok=True)
    data = text.replace("\n", newline) if newline != "\n" else text
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(data.encode("utf-8"))
    if path.exists():
        try:
            shutil.copymode(path, temporary)
        except OSError:
            pass
    os.replace(temporary, path)


def _mcp_server_block(
    server_command: str | Path | None = None,
    config_path: str | Path | None = None,
) -> dict[str, Any]:
    # the JSON MCP server entry shape Claude Code/Desktop expect
    return {
        IDENTITY.mcp_server_name: {
            "command": _server_command(server_command),
            "args": [],
            "env": {"NEO_LOCALMCP_CONFIG": _config_value(config_path)},
        }
    }


def _codex_block(
    server_command: str | Path | None = None,
    config_path: str | Path | None = None,
) -> str:
    # same server registration as _mcp_server_block, but as a marked TOML block for config.toml
    return f"""
# BEGIN neo-localmcp
[mcp_servers.{IDENTITY.mcp_server_name}]
command = "{_toml_string(_server_command(server_command))}"
args = []

[mcp_servers.{IDENTITY.mcp_server_name}.env]
NEO_LOCALMCP_CONFIG = "{_toml_string(_config_value(config_path))}"
# END neo-localmcp
""".strip() + "\n"


def _replace_marked_block(old: str, block: str, start: str = "# BEGIN neo-localmcp", end: str = "# END neo-localmcp") -> str:
    # markers found -> replace only the marked region, preserving surrounding user config; else -> append the block to the end
    if start in old and end in old:
        before = old.split(start, 1)[0].rstrip()
        after = old.split(end, 1)[1].lstrip()
        prefix = before + "\n\n" if before else ""
        suffix = "\n" + after if after else ""
        return prefix + block + suffix
    return old.rstrip() + "\n\n" + block if old.strip() else block


def _strip_marked_block(old: str, start: str = "# BEGIN neo-localmcp", end: str = "# END neo-localmcp") -> str:
    # inverse of _replace_marked_block: removes the marked region entirely, collapsing the gap to a single blank line, preserving the rest of the user's config
    if start not in old or end not in old:
        return old
    before = old.split(start, 1)[0].rstrip()
    after = old.split(end, 1)[1].strip()
    if before and after:
        return before + "\n\n" + after + "\n"
    if before:
        return before + "\n"
    if after:
        return after + "\n"
    return ""


def _detect_registered_scope(combined: str) -> str | None:
    # `claude mcp get` output -> reported scope, or None; shared by setup_claude_code's migration and remove_claude_code's removal so both detect the same way
    if "scope: local" in combined:
        return "local"
    if "scope: user" in combined:
        return "user"
    if "scope: project" in combined:
        return "project"
    return None


def _write_codex_config(
    path: Path,
    apply: bool,
    *,
    server_command: str | Path | None = None,
    config_path: str | Path | None = None,
) -> dict[str, Any]:
    # builds the codex block, writes it via the marked-block replace only if apply=True (dry-run just previews)
    block = _codex_block(server_command, config_path)
    if apply:
        old, newline = _read_config_for_edit(path)
        _atomic_write_text(path, _replace_marked_block(old, block), newline)
    return {"config_path": str(path), "exists_after": path.exists(), "block": block}


def _migrate_claude_code_registration(claude: str, launcher: str) -> list[str]:
    # registers under user scope, migrating away from any other scope already registered
    # bounded retry (3): each pass removes one stale-scope registration and re-checks, so an unexpected scope still converges without a second manual run
    actions: list[str] = []
    # user scope is best for repeatable sessions; falls back to the classic command if the CLI is too old for --scope
    cmd = [claude, "mcp", "add", "--scope", "user", IDENTITY.mcp_server_name, "--", launcher]
    configured = False
    existing = None
    for _ in range(3):
        existing = subprocess.run([claude, "mcp", "get", IDENTITY.mcp_server_name], stdin=subprocess.DEVNULL, capture_output=True, text=True, errors="replace", **hidden_subprocess_kwargs())
        combined = f"{existing.stdout}\n{existing.stderr}".lower()
        if existing.returncode != 0:
            break
        if launcher.lower() in combined and "scope: user" in combined:
            actions.append("Claude Code user-scope registration already uses the stable launcher")
            configured = True
            break
        existing_scope = _detect_registered_scope(combined)
        if not existing_scope:
            break
        removed = subprocess.run([claude, "mcp", "remove", IDENTITY.mcp_server_name, "--scope", existing_scope], stdin=subprocess.DEVNULL, capture_output=True, text=True, errors="replace", **hidden_subprocess_kwargs())
        actions.append(f"removed existing {existing_scope}-scope registration for migration: exit {removed.returncode}")
        if removed.returncode != 0:
            break
    result = subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True, text=True, errors="replace", **hidden_subprocess_kwargs()) if not configured else existing
    if not configured and result.returncode != 0:
        fallback = [claude, "mcp", "add", IDENTITY.mcp_server_name, "--", launcher]
        result = subprocess.run(fallback, stdin=subprocess.DEVNULL, capture_output=True, text=True, errors="replace", **hidden_subprocess_kwargs())
        actions.append(f"ran claude mcp add fallback: exit {result.returncode}")
    elif not configured:
        actions.append(f"ran claude mcp add --scope user: exit {result.returncode}")
    if result.stderr.strip():
        actions.append(result.stderr.strip()[:500])
    return actions


def setup_claude_code(
    apply: bool = True,
    *,
    server_command: str | Path | None = None,
) -> dict[str, Any]:
    # installs slash-command templates -> if the claude CLI is present, also migrates the MCP registration to user scope
    ensure_config()
    launcher = _server_command(server_command)
    target = Path.home() / ".claude" / "commands" / IDENTITY.slash_prefix
    actions: list[str] = []
    if apply:
        target.mkdir(parents=True, exist_ok=True)
        for name, text in _command_templates():
            (target / name).write_text(text, encoding="utf-8")
        actions.append(f"installed slash commands to {target}")
        claude = shutil.which("claude")
        if claude:
            actions.extend(_migrate_claude_code_registration(claude, launcher))
        else:
            actions.append("claude CLI not found; slash commands were still installed")
    return {
        "client": "claude-code",
        "applied": apply,
        "target": str(target),
        "server_command": launcher,
        "actions": actions,
        "manual_mcp_user": f"claude mcp add --scope user {IDENTITY.mcp_server_name} -- {launcher}",
        "manual_mcp_fallback": f"claude mcp add {IDENTITY.mcp_server_name} -- {launcher}",
    }


def _claude_desktop_config_path() -> Path:
    # OS -> Claude Desktop's config.json location (Windows/macOS/Linux)
    system = platform.system().lower()
    if system == "windows":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
        return base / "Claude" / "claude_desktop_config.json"
    if system == "darwin":
        return Path.home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
    return Path.home() / ".config" / "Claude" / "claude_desktop_config.json"


def setup_claude_desktop(apply: bool = True) -> dict[str, Any]:
    # always manual: never writes claude_desktop_config.json, just reports the .mcpb path and install instructions
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
    # honor CODEX_HOME (codex app/CLI/IDE all resolve config.toml from it) before the ~/.codex default
    codex_home = os.environ.get("CODEX_HOME")
    base = Path(codex_home) if codex_home else Path.home() / ".codex"
    return base / "config.toml"


def _codex_desktop_config_path() -> Path:
    # codex app, CLI, and IDE all share the same CODEX_HOME/config.toml
    return _codex_cli_config_path()


def setup_codex_cli(
    apply: bool = True,
    *,
    server_command: str | Path | None = None,
    config_path: str | Path | None = None,
) -> dict[str, Any]:
    # writes the shared codex config.toml marked block
    ensure_config()
    path = _codex_cli_config_path()
    return {
        "client": "codex-cli",
        "applied": apply,
        **_write_codex_config(path, apply, server_command=server_command, config_path=config_path),
    }


def setup_codex_desktop(
    apply: bool = True,
    *,
    server_command: str | Path | None = None,
    config_path: str | Path | None = None,
) -> dict[str, Any]:
    # same write as setup_codex_cli -- shared config file, just a different reported client label
    result = setup_codex_cli(apply, server_command=server_command, config_path=config_path)
    return {**result, "client": "codex-desktop", "shared_with_cli": True, "restart_required": True}


def setup_codex(
    apply: bool = True,
    *,
    server_command: str | Path | None = None,
    config_path: str | Path | None = None,
) -> dict[str, Any]:
    # umbrella label over setup_codex_cli, since CLI/Desktop share one config
    return {
        "client": "codex",
        "applied": apply,
        "shared_config": True,
        "result": setup_codex_cli(apply, server_command=server_command, config_path=config_path),
    }


def remove_claude_code(apply: bool = True) -> dict[str, Any]:
    # inverse of setup_claude_code: detect the actual registered scope, deregister from it, delete the slash-command directory
    target = Path.home() / ".claude" / "commands" / IDENTITY.slash_prefix
    actions: list[str] = []
    ok = True
    error = ""
    if apply:
        claude = shutil.which("claude")
        if claude:
            existing = subprocess.run(
                [claude, "mcp", "get", IDENTITY.mcp_server_name],
                stdin=subprocess.DEVNULL, capture_output=True, text=True, errors="replace",
                **hidden_subprocess_kwargs(),
            )
            combined = f"{existing.stdout}\n{existing.stderr}".lower()
            scope = None
            if existing.returncode == 0:
                scope = _detect_registered_scope(combined)
            if scope:
                removed = subprocess.run(
                    [claude, "mcp", "remove", IDENTITY.mcp_server_name, "--scope", scope],
                    stdin=subprocess.DEVNULL, capture_output=True, text=True, errors="replace",
                    **hidden_subprocess_kwargs(),
                )
                actions.append(f"removed {scope}-scope MCP registration: exit {removed.returncode}")
                if removed.stderr.strip():
                    actions.append(removed.stderr.strip()[:500])
                if removed.returncode != 0:
                    ok = False
                    error = removed.stderr.strip() or removed.stdout.strip() or (
                        f"claude mcp remove exited {removed.returncode}"
                    )
            else:
                actions.append("no MCP registration found via 'claude mcp get'; nothing to deregister")
        else:
            actions.append("claude CLI not found; skipped MCP deregistration (slash commands still removed)")
            ok = False
            error = "claude CLI not found; could not verify MCP deregistration"
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)
            actions.append(f"removed slash-command directory {target}")
        else:
            actions.append(f"slash-command directory not present: {target}")
    return {
        "client": "claude-code",
        "ok": ok,
        "error": error,
        "applied": apply,
        "commands_dir": str(target),
        "commands_dir_exists_after": target.exists(),
        "actions": actions,
    }


def remove_codex(apply: bool = True) -> dict[str, Any]:
    # inverse of setup_codex*: strips the marked block from the shared config.toml, leaving the rest of the user's config intact
    path = _codex_cli_config_path()
    existed = path.exists()
    block_present = existed and "# BEGIN neo-localmcp" in path.read_text(encoding="utf-8")
    empty_after = False
    if apply and block_present:
        old, newline = _read_config_for_edit(path)
        new = _strip_marked_block(old)
        _atomic_write_text(path, new, newline)
        # checked against `new` (already in memory), not a fresh disk re-read -- it's exactly what was just written
        block_present_after = "# BEGIN neo-localmcp" in new
        empty_after = new.strip() == ""
        ok = not block_present_after
    else:
        # dry run, or nothing to remove -> file unchanged, nothing can fail
        block_present_after = block_present
        ok = True
    return {
        "client": "codex",
        "ok": ok,
        "applied": apply,
        "config_path": str(path),
        "config_existed": existed,
        "block_present": block_present,
        "block_present_after": block_present_after,
        "config_empty_after": empty_after,
    }


def remove_claude_desktop(apply: bool = True) -> dict[str, Any]:
    # by design, cannot be automated (mirrors setup_claude_desktop) -- removal is a manual action in Claude Desktop's own Extensions UI
    return {
        "client": "claude-desktop",
        "ok": False,
        "applied": False,
        "manual_removal_required": True,
        "instructions": "In Claude Desktop open Settings > Extensions and uninstall neo-localmcp. If that hangs, run `python setup.py uninstall` from the checkout first.",
        "note": "Direct claude_desktop_config.json editing is intentionally never performed.",
    }


def remove_client(client: str, apply: bool = True) -> dict[str, Any]:
    # client name -> matching remove_* fn; unrecognized name -> raises (caller decides how to report)
    key = client.lower().replace("_", "-")
    if key in {"claude-code", "claude"}:
        return remove_claude_code(apply=apply)
    if key in {"codex", "codex-cli", "codex-desktop"}:
        return remove_codex(apply=apply)
    if key in {"claude-desktop", "desktop"}:
        return remove_claude_desktop(apply=apply)
    raise ValueError(f"Unknown client: {client}. Expected claude-code, claude-desktop, or codex.")


def remove_clients(clients: list[str] | None = None, apply: bool = True) -> list[dict[str, Any]]:
    # per-client remove_client call; one client's exception -> per-result error entry, not a failure of the whole batch
    selected = clients or ["claude-code", "codex", "claude-desktop"]
    if any(str(client).lower().replace("_", "-") == "all" for client in selected):
        selected = ["claude-code", "codex", "claude-desktop"]
    results = []
    for client in selected:
        try:
            results.append(remove_client(client, apply=apply))
        except Exception as exc:
            results.append({"client": client, "applied": apply, "ok": False, "error": str(exc)})
    return results


def client_status(
    server_command: str | Path | None = None,
    config_path: str | Path | None = None,
) -> dict[str, Any]:
    # read-only snapshot: which CLIs are found, what each client's config path/existence is, and the exact blocks that would be written
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
        "server_command": _server_command(server_command),
        "config_path": _config_value(config_path),
        "commands_found": {"claude": claude_cli, "codex": codex_cli},
        "paths": {name: {"path": path, "exists": Path(path).exists()} for name, path in paths.items()},
        "mcp_server_block": _mcp_server_block(server_command, config_path),
        "codex_block": _codex_block(server_command, config_path),
    }


def setup_client(
    client: str,
    apply: bool = True,
    *,
    server_command: str | Path | None = None,
    config_path: str | Path | None = None,
) -> dict[str, Any]:
    # client name -> matching setup_* fn; "all" -> recurse into setup_clients
    key = client.lower().replace("_", "-")
    if key == "all":
        return {"client": "all", "applied": apply, "results": setup_clients(None, apply=apply, server_command=server_command, config_path=config_path)}
    if key in {"claude-code", "claude"}:
        return setup_claude_code(apply=apply, server_command=server_command)
    if key in {"claude-desktop", "desktop"}:
        return setup_claude_desktop(apply=apply)
    if key == "codex":
        return setup_codex(apply=apply, server_command=server_command, config_path=config_path)
    if key == "codex-cli":
        return setup_codex_cli(apply=apply, server_command=server_command, config_path=config_path)
    if key == "codex-desktop":
        return setup_codex_desktop(apply=apply, server_command=server_command, config_path=config_path)
    raise ValueError(f"Unknown client: {client}. Expected all, claude-code, claude-desktop, codex, codex-cli, or codex-desktop.")


def setup_clients(
    clients: list[str] | None = None,
    apply: bool = True,
    *,
    server_command: str | Path | None = None,
    config_path: str | Path | None = None,
) -> list[dict[str, Any]]:
    # per-client setup_client call; one client's exception -> per-result error entry, not a failure of the whole batch
    selected = clients or ["claude-code", "claude-desktop", "codex"]
    if any(str(client).lower().replace("_", "-") == "all" for client in selected):
        selected = ["claude-code", "claude-desktop", "codex"]
    results = []
    for client in selected:
        try:
            results.append(setup_client(client, apply=apply, server_command=server_command, config_path=config_path))
        except Exception as exc:
            results.append({"client": client, "applied": apply, "ok": False, "error": str(exc)})
    return results
