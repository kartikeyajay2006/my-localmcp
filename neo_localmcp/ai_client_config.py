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
    # An explicit launcher (the installer passes the managed venv's
    # neo-localmcp-server executable) always wins; only the legacy CLI path with
    # no injected value falls back to the old side-by-side ``bin/`` shim.
    if server_command is not None:
        return str(server_command)
    return _default_server_command()


def _config_value(config_path: str | Path | None = None) -> str:
    return str(config_path) if config_path is not None else str(CONFIG_PATH)


def _toml_string(value: str | Path) -> str:
    return str(value).replace("\\", "\\\\").replace('"', '\\"')


def _read_config_for_edit(path: Path) -> tuple[str, str]:
    # Return (LF-normalized text, detected newline style). Detection reads raw
    # bytes because text-mode reads translate CRLF to LF before we can see it.
    if not path.exists():
        return "", "\n"
    raw = path.read_bytes()
    newline = "\r\n" if b"\r\n" in raw else "\n"
    text = raw.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
    return text, newline


def _atomic_write_text(path: Path, text: str, newline: str = "\n") -> None:
    # Write user-owned config transactionally so a crash mid-write cannot leave a
    # half-written registration, and re-expand to the file's own newline style so
    # editing a CRLF config on POSIX (or vice versa) does not rewrite every line.
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
    if start in old and end in old:
        before = old.split(start, 1)[0].rstrip()
        after = old.split(end, 1)[1].lstrip()
        prefix = before + "\n\n" if before else ""
        suffix = "\n" + after if after else ""
        return prefix + block + suffix
    return old.rstrip() + "\n\n" + block if old.strip() else block


def _strip_marked_block(old: str, start: str = "# BEGIN neo-localmcp", end: str = "# END neo-localmcp") -> str:
    # Inverse of _replace_marked_block: remove our marked region entirely, preserving
    # whatever the user had around it and collapsing the gap to a single blank line.
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
    """Which scope `claude mcp get` reported the server registered under, if any.

    Shared by setup_claude_code's migration path and remove_claude_code's removal
    path -- both need to detect the same way (#33, finding 8.3).
    """
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
    block = _codex_block(server_command, config_path)
    if apply:
        old, newline = _read_config_for_edit(path)
        _atomic_write_text(path, _replace_marked_block(old, block), newline)
    return {"config_path": str(path), "exists_after": path.exists(), "block": block}


def _migrate_claude_code_registration(claude: str, launcher: str) -> list[str]:
    """Register neo-localmcp under Claude Code's user scope, migrating away from any
    other scope it might already be registered under.

    Bounded retry (3): each iteration removes one stale-scope registration and
    re-checks, so a registration under an unexpected scope still converges instead
    of needing a second manual run. Extracted from setup_claude_code, which used to
    inline this as one dense `for _ in range(3)` block with nested breaks (#33,
    finding 8.2).
    """
    actions: list[str] = []
    # User scope is best for repeatable Claude Code sessions. If the CLI is older and does not support
    # --scope, fall back to the classic command.
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


def setup_codex_cli(
    apply: bool = True,
    *,
    server_command: str | Path | None = None,
    config_path: str | Path | None = None,
) -> dict[str, Any]:
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
    result = setup_codex_cli(apply, server_command=server_command, config_path=config_path)
    return {**result, "client": "codex-desktop", "shared_with_cli": True, "restart_required": True}


def setup_codex(
    apply: bool = True,
    *,
    server_command: str | Path | None = None,
    config_path: str | Path | None = None,
) -> dict[str, Any]:
    return {
        "client": "codex",
        "applied": apply,
        "shared_config": True,
        "result": setup_codex_cli(apply, server_command=server_command, config_path=config_path),
    }


def remove_claude_code(apply: bool = True) -> dict[str, Any]:
    # Inverse of setup_claude_code: deregister the MCP server from whatever scope
    # it is actually registered in (detected the same way the migration path in
    # setup_claude_code does) and delete the slash-command directory.
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
    # Inverse of setup_codex*: strip our marked block from the shared config.toml,
    # leaving any of the user's own config intact.
    path = _codex_cli_config_path()
    existed = path.exists()
    block_present = existed and "# BEGIN neo-localmcp" in path.read_text(encoding="utf-8")
    # block_present_after and ok are derived from `new` (already in memory from the
    # write below), not a fresh disk read -- the post-write content is exactly what
    # was just written, so re-reading the file twice more to ask the same question
    # was redundant disk I/O, not a distinct check (#33).
    empty_after = False
    if apply and block_present:
        old, newline = _read_config_for_edit(path)
        new = _strip_marked_block(old)
        _atomic_write_text(path, new, newline)
        block_present_after = "# BEGIN neo-localmcp" in new
        empty_after = new.strip() == ""
        ok = not block_present_after
    else:
        # Nothing was attempted (dry run, or there was no block to remove), so the
        # file is unchanged and there is nothing to fail.
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
    # By design Claude Desktop cannot be automated (see setup_claude_desktop's note):
    # removal is a manual action in Claude Desktop's own Extensions UI.
    return {
        "client": "claude-desktop",
        "ok": False,
        "applied": False,
        "manual_removal_required": True,
        "instructions": "In Claude Desktop open Settings > Extensions and uninstall neo-localmcp. If that hangs, run `python setup.py uninstall` from the checkout first.",
        "note": "Direct claude_desktop_config.json editing is intentionally never performed.",
    }


def remove_client(client: str, apply: bool = True) -> dict[str, Any]:
    key = client.lower().replace("_", "-")
    if key in {"claude-code", "claude"}:
        return remove_claude_code(apply=apply)
    if key in {"codex", "codex-cli", "codex-desktop"}:
        return remove_codex(apply=apply)
    if key in {"claude-desktop", "desktop"}:
        return remove_claude_desktop(apply=apply)
    raise ValueError(f"Unknown client: {client}. Expected claude-code, claude-desktop, or codex.")


def remove_clients(clients: list[str] | None = None, apply: bool = True) -> list[dict[str, Any]]:
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
