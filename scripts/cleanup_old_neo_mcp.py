#!/usr/bin/env python3
"""One-time cleanup for old neo/neo-local MCP experiments.

Default is dry-run. Pass --apply to delete/edit.
ZIP files are always preserved.
Run this before installing the V4 fresh baseline if you want a clean machine.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
from pathlib import Path
from typing import Any

OLD_SERVER_KEYS = {"neo", "neo-local", "neo-local-agent", "neo-local-agent-mcp", "neo-localmcp"}
OLD_BLOCK_NAMES = ["neo-localmcp", "neo-local-agent-mcp", "neo-local-agent", "neo-local", "neo"]


def home() -> Path:
    return Path.home()


def candidates() -> list[Path]:
    h = home()
    appdata = Path(os.environ.get("APPDATA", h / "AppData" / "Roaming"))
    localappdata = Path(os.environ.get("LOCALAPPDATA", h / "AppData" / "Local"))
    return [
        h / ".neo-localmcp",
        h / ".neo-local-agent",
        h / ".neo-local-agent-mcp",
        h / ".config" / "neo-localmcp",
        h / ".config" / "neo-local-agent",
        h / ".config" / "neo-local-agent-mcp",
        h / ".local" / "share" / "neo-localmcp",
        h / ".local" / "share" / "neo-local-agent",
        h / ".local" / "share" / "neo-local-agent-mcp",
        h / ".local" / "bin" / "neo",
        h / ".local" / "bin" / "neo-localmcp",
        h / ".local" / "bin" / "neo-local-agent",
        h / ".local" / "bin" / "neo-local-agent-mcp",
        h / ".claude" / "commands" / "neo",
        h / ".claude" / "commands" / "neo-localmcp",
        h / ".claude" / "commands" / "neo-local-agent",
        h / ".claude" / "commands" / "neo-local-agent-mcp",
        appdata / "neo-localmcp",
        appdata / "neo-local-agent",
        appdata / "neo-local-agent-mcp",
        localappdata / "neo-localmcp",
        localappdata / "neo-local-agent",
        localappdata / "neo-local-agent-mcp",
    ]


def claude_desktop_configs() -> list[Path]:
    h = home()
    appdata = Path(os.environ.get("APPDATA", h / "AppData" / "Roaming"))
    return [
        appdata / "Claude" / "claude_desktop_config.json",
        h / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json",
        h / ".config" / "Claude" / "claude_desktop_config.json",
    ]


def codex_configs() -> list[Path]:
    return [home() / ".codex" / "config.toml"]


def backup(path: Path, apply: bool, actions: list[str]) -> None:
    if not path.exists() or not apply:
        return
    bak = path.with_name(path.name + ".pre-v4-cleanup.bak")
    i = 1
    while bak.exists():
        bak = path.with_name(path.name + f".pre-v4-cleanup.{i}.bak")
        i += 1
    shutil.copy2(path, bak)
    actions.append(f"backup: {path} -> {bak}")


def remove_preserving_zips(path: Path, apply: bool, actions: list[str], force: bool = False) -> None:
    if not path.exists():
        return
    if path.is_file() or path.is_symlink():
        if path.suffix.lower() == ".zip":
            actions.append(f"preserve zip: {path}")
            return
        actions.append(f"remove file: {path}")
        if apply:
            try:
                if force:
                    try:
                        path.chmod(0o700)
                    except Exception:
                        pass
                path.unlink(missing_ok=True)
            except PermissionError as exc:
                actions.append(f"skip locked/access denied: {path} ({exc}). Close Python/Claude/MCP processes and rerun.")
            except OSError as exc:
                actions.append(f"skip remove failed: {path} ({exc})")
        return

    for child in sorted(path.iterdir(), key=lambda p: len(p.parts), reverse=True):
        remove_preserving_zips(child, apply, actions, force=force)
    try:
        remaining = list(path.iterdir()) if path.exists() else []
    except OSError:
        remaining = []
    if not remaining:
        actions.append(f"remove empty dir: {path}")
        if apply:
            try:
                path.rmdir()
            except PermissionError as exc:
                actions.append(f"skip locked/access denied dir: {path} ({exc}). Close Python/Claude/MCP processes and rerun.")
            except OSError as exc:
                actions.append(f"skip remove dir failed: {path} ({exc})")
    else:
        actions.append(f"keep dir because preserved files remain: {path}")


def clean_claude_desktop_config(path: Path, apply: bool, actions: list[str]) -> None:
    if not path.exists():
        return
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        actions.append(f"skip invalid JSON: {path} ({exc})")
        return
    servers = data.get("mcpServers")
    if not isinstance(servers, dict):
        return
    removed = []
    for key in list(servers.keys()):
        if key in OLD_SERVER_KEYS:
            removed.append(key)
            del servers[key]
    if removed:
        actions.append(f"remove Claude Desktop MCP entries {removed} from {path}")
        if apply:
            backup(path, apply, actions)
            path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def clean_codex_config(path: Path, apply: bool, actions: list[str]) -> None:
    if not path.exists():
        return
    text = path.read_text(encoding="utf-8")
    original = text
    for name in OLD_BLOCK_NAMES:
        text = re.sub(rf"\n?# BEGIN {re.escape(name)}\n.*?# END {re.escape(name)}\n?", "\n", text, flags=re.S)
    for name in OLD_BLOCK_NAMES:
        escaped = re.escape(name)
        text = re.sub(rf"\n?\[mcp_servers\.{escaped}\]\n(?:[^\[]|\[(?!mcp_servers\.))*", "\n", text, flags=re.S)
    text = re.sub(r"\n{3,}", "\n\n", text).strip() + ("\n" if text.strip() else "")
    if text != original:
        actions.append(f"remove old Codex MCP blocks from {path}")
        if apply:
            backup(path, apply, actions)
            path.write_text(text, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Clean old neo/neo-local MCP files and config references. ZIP files are preserved.")
    parser.add_argument("--apply", action="store_true", help="Actually delete/edit. Without this, only prints the plan.")
    parser.add_argument("--force", action="store_true", help="Try chmod before deleting locked/read-only files. Still preserves .zip files and still skips files Windows reports as actively locked.")
    args = parser.parse_args()
    actions: list[str] = []

    for path in candidates():
        remove_preserving_zips(path.expanduser(), args.apply, actions, force=args.force)
    for path in claude_desktop_configs():
        clean_claude_desktop_config(path.expanduser(), args.apply, actions)
    for path in codex_configs():
        clean_codex_config(path.expanduser(), args.apply, actions)

    print(json.dumps({"applied": args.apply, "zip_files_preserved": True, "force": args.force, "actions": actions}, indent=2))
    if not args.apply:
        print("\nDry run only. Re-run with --apply to delete/edit.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
