"""Opt-in PATH guidance and per-user PATH updates for the managed CLI."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .paths import ManagedPaths

_PATH_MARKER = "# neo-localmcp PATH"


@dataclass(frozen=True)
class PathUpdate:
    """Result of an opt-in PATH update."""

    changed: bool
    target: Path | str


def path_hint(paths: ManagedPaths) -> str:
    """Return the exact shell command that exposes the managed CLI."""
    if paths.platform == "windows":
        return f'setx PATH "%PATH%;{paths.executable_dir}"'
    return f'export PATH="{paths.executable_dir}:$PATH"'


def _shell_rc_file(paths: ManagedPaths, environ: Mapping[str, str]) -> Path:
    shell = Path(environ.get("SHELL", "")).name
    if shell == "zsh":
        return paths.home / ".zshrc"
    if shell == "bash":
        return paths.home / ".bashrc"
    if shell == "fish":
        return paths.home / ".config" / "fish" / "config.fish"
    raise ValueError("Could not identify a supported shell; add the PATH hint manually.")


def append_shell_path(
    paths: ManagedPaths, *, environ: Mapping[str, str] | None = None
) -> PathUpdate:
    """Append one marked PATH export to the detected POSIX shell rc file."""
    if paths.platform != "posix":
        raise ValueError("Shell rc files are only used on POSIX platforms.")
    rc_file = _shell_rc_file(paths, os.environ if environ is None else environ)
    existing = rc_file.read_text(encoding="utf-8") if rc_file.exists() else ""
    if _PATH_MARKER in existing:
        return PathUpdate(changed=False, target=rc_file)
    rc_file.parent.mkdir(parents=True, exist_ok=True)
    with rc_file.open("a", encoding="utf-8") as handle:
        handle.write(f"\n{_PATH_MARKER}\n{path_hint(paths)}\n")
    return PathUpdate(changed=True, target=rc_file)


def add_to_path(
    paths: ManagedPaths,
    *,
    environ: Mapping[str, str] | None = None,
    winreg_module: Any | None = None,
) -> PathUpdate:
    """Persist the managed executable directory in the user's PATH."""
    if paths.platform == "posix":
        return append_shell_path(paths, environ=environ)

    if winreg_module is None:
        import winreg as winreg_module

    target = "HKEY_CURRENT_USER\\Environment\\Path"
    with winreg_module.OpenKey(
        winreg_module.HKEY_CURRENT_USER,
        "Environment",
        0,
        winreg_module.KEY_READ | winreg_module.KEY_SET_VALUE,
    ) as key:
        try:
            current, value_type = winreg_module.QueryValueEx(key, "Path")
        except FileNotFoundError:
            current, value_type = "", winreg_module.REG_EXPAND_SZ
        entries = [entry for entry in str(current).split(";") if entry]
        executable_dir = str(paths.executable_dir)
        if any(entry.casefold() == executable_dir.casefold() for entry in entries):
            return PathUpdate(changed=False, target=target)
        updated = ";".join([*entries, executable_dir])
        winreg_module.SetValueEx(key, "Path", 0, value_type, updated)
    return PathUpdate(changed=True, target=target)
