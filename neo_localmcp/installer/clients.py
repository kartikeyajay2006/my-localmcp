"""Snapshot, remove, restore, and verify neo-localmcp client registrations.

A reinstall replaces the managed ``venv/`` at a new path, and a default uninstall
removes it entirely. Either way, any client (Claude Code, Codex, Claude Desktop)
whose config still names the old launcher is left with a broken command. These
primitives record which surfaces neo-localmcp is registered on (``clients/
registrations.json``), remove those registrations before the runtime is replaced,
and restore them afterward pointing at the promoted launcher.

The records are the durable memory of "what was connected"; the client-side config
files are the mutable truth. Default uninstall removes the live registrations but
keeps the records so a later reinstall can reconnect the same surfaces.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable, Sequence

from .. import ai_client_config as client_setup
from ..branding import IDENTITY
from ..repo_utils import hidden_subprocess_kwargs
from .paths import ManagedPaths

REGISTRATIONS_SCHEMA_VERSION = 1

# Canonical client keys we persist and act on. Codex CLI/Desktop share one config,
# so they collapse to a single "codex" record.
CLAUDE_CODE = "claude-code"
CODEX = "codex"
CLAUDE_DESKTOP = "claude-desktop"

_BEGIN = "# BEGIN neo-localmcp"
_END = "# END neo-localmcp"
_COMMAND_RE = re.compile(r'command\s*=\s*"((?:[^"\\]|\\.)*)"')


@dataclass(frozen=True)
class ClientRegistrationRecord:
    """One client's recorded registration state."""

    client: str
    active: bool
    manual: bool
    server_command: str | None
    config_path: str | None
    detail: str = ""

    def to_json(self) -> dict[str, Any]:
        return {
            "client": self.client,
            "active": self.active,
            "manual": self.manual,
            "server_command": self.server_command,
            "config_path": self.config_path,
            "detail": self.detail,
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "ClientRegistrationRecord":
        return cls(
            client=str(data["client"]),
            active=bool(data.get("active", False)),
            manual=bool(data.get("manual", False)),
            server_command=data.get("server_command"),
            config_path=data.get("config_path"),
            detail=str(data.get("detail", "")),
        )


@dataclass(frozen=True)
class RegistrationCheck:
    client: str
    ok: bool
    detail: str


# --------------------------------------------------------------------------- #
# Persistence
# --------------------------------------------------------------------------- #


def registrations_path(paths: ManagedPaths) -> Path:
    return paths.clients / "registrations.json"


def read_registrations(paths: ManagedPaths) -> tuple[ClientRegistrationRecord, ...]:
    # missing/corrupt/wrong-schema file -> empty tuple, never raises; a lifecycle op must not crash on bad registration records
    path = registrations_path(paths)
    if not path.exists():
        return ()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ()
    if not isinstance(payload, dict):
        return ()
    if payload.get("schema_version") != REGISTRATIONS_SCHEMA_VERSION:
        return ()
    records = payload.get("records")
    if not isinstance(records, list):
        return ()
    return tuple(
        ClientRegistrationRecord.from_json(item)
        for item in records
        if isinstance(item, dict) and item.get("client")
    )


def write_registrations(
    paths: ManagedPaths, records: tuple[ClientRegistrationRecord, ...]
) -> None:
    # tmp-write + os.replace -> atomic; full overwrite of the records list, not a merge
    path = registrations_path(paths)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": REGISTRATIONS_SCHEMA_VERSION,
        "records": [record.to_json() for record in records],
    }
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def delete_registrations(paths: ManagedPaths) -> None:
    # drops the records file entirely -- clean install / full wipe only
    registrations_path(paths).unlink(missing_ok=True)


# --------------------------------------------------------------------------- #
# Detection helpers
# --------------------------------------------------------------------------- #


def _unescape_toml(value: str) -> str:
    return value.replace('\\"', '"').replace("\\\\", "\\")


def _codex_marked_region(text: str) -> str | None:
    # text between the BEGIN/END markers, or None if either marker is missing
    if _BEGIN not in text or _END not in text:
        return None
    return text.split(_BEGIN, 1)[1].split(_END, 1)[0]


def _detect_codex(paths: ManagedPaths) -> ClientRegistrationRecord | None:
    # config.toml marked block present -> active registration record; absent -> None
    path = client_setup._codex_cli_config_path()
    if not path.exists():
        return None
    text = path.read_text(encoding="utf-8")
    region = _codex_marked_region(text)
    if region is None:
        return None
    match = _COMMAND_RE.search(region)
    server_command = _unescape_toml(match.group(1)) if match else None
    return ClientRegistrationRecord(
        client=CODEX,
        active=True,
        manual=False,
        server_command=server_command,
        config_path=str(path),
        detail="codex config.toml marked block present",
    )


def _claude_commands_dir() -> Path:
    return Path.home() / ".claude" / "commands" / IDENTITY.slash_prefix


def _detect_claude_code(paths: ManagedPaths) -> ClientRegistrationRecord | None:
    # claude CLI available -> confirm via `claude mcp get`; else fall back to slash-commands-dir presence alone
    commands_dir = _claude_commands_dir()
    claude = client_setup.shutil.which("claude")
    if claude:
        result = subprocess.run(
            [claude, "mcp", "get", IDENTITY.mcp_server_name],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            errors="replace",
            **hidden_subprocess_kwargs(),
        )
        combined = f"{result.stdout}\n{result.stderr}"
        if result.returncode == 0 and IDENTITY.mcp_server_name.lower() in combined.lower():
            match = _COMMAND_RE.search(combined)
            server_command = match.group(1) if match else _parse_claude_command(combined)
            return ClientRegistrationRecord(
                client=CLAUDE_CODE,
                active=True,
                manual=False,
                server_command=server_command,
                config_path=str(commands_dir),
                detail="claude mcp registration present",
            )
    if commands_dir.exists():
        # Slash commands are ours even when the CLI is unavailable to confirm the
        # MCP registration; record so uninstall still cleans them up.
        return ClientRegistrationRecord(
            client=CLAUDE_CODE,
            active=True,
            manual=False,
            server_command=None,
            config_path=str(commands_dir),
            detail="slash commands present; claude CLI unavailable to confirm registration",
        )
    return None


def _parse_claude_command(combined: str) -> str | None:
    # `claude mcp get` prints a human line rather than TOML; look for the launcher
    # token so verification/restore can compare against the managed path.
    for token in combined.replace("\n", " ").split():
        if "neo-localmcp-server" in token:
            return token
    return None


# --------------------------------------------------------------------------- #
# Public primitives
# --------------------------------------------------------------------------- #


def snapshot_clients(paths: ManagedPaths) -> tuple[ClientRegistrationRecord, ...]:
    # detects Codex/Claude Code from disk/CLI; Claude Desktop can't be probed (manual extension install), so a prior desktop record is carried forward instead of dropped
    prior = {record.client: record for record in read_registrations(paths)}
    detected: list[ClientRegistrationRecord] = []
    for detector in (_detect_claude_code, _detect_codex):
        record = detector(paths)
        if record is not None:
            detected.append(record)
    detected_keys = {record.client for record in detected}
    if CLAUDE_DESKTOP in prior and CLAUDE_DESKTOP not in detected_keys:
        detected.append(prior[CLAUDE_DESKTOP])
    records = tuple(detected)
    write_registrations(paths, records)
    return records


def record_selection(
    paths: ManagedPaths, clients: list[str]
) -> tuple[ClientRegistrationRecord, ...]:
    # trusts the caller's chosen surfaces rather than probing disk (unlike snapshot_clients), so a fresh install can record intent before any registration exists
    records: list[ClientRegistrationRecord] = []
    for client in clients:
        key = client.lower().replace("_", "-")
        if key in {"claude-code", "claude"}:
            records.append(ClientRegistrationRecord(CLAUDE_CODE, True, False, None, None, "selected"))
        elif key in {"codex", "codex-cli", "codex-desktop"}:
            records.append(ClientRegistrationRecord(CODEX, True, False, None, None, "selected"))
        elif key in {"claude-desktop", "desktop"}:
            records.append(ClientRegistrationRecord(CLAUDE_DESKTOP, True, True, None, None, "selected (manual)"))
    # De-duplicate while preserving order (codex-cli + codex-desktop → one record).
    seen: set[str] = set()
    unique = tuple(r for r in records if not (r.client in seen or seen.add(r.client)))
    write_registrations(paths, unique)
    return unique


def remove_active_registrations(
    paths: ManagedPaths, *, apply: bool = True
) -> tuple[dict[str, Any], ...]:
    # removes only the live registrations; the records themselves stay on disk so a later reinstall can reconnect the same surfaces
    results: list[dict[str, Any]] = []
    for record in read_registrations(paths):
        if record.client == CLAUDE_DESKTOP:
            results.append(client_setup.remove_claude_desktop(apply=apply))
            continue
        if not record.active:
            continue
        results.append(client_setup.remove_client(record.client, apply=apply))
    return tuple(results)


def restore_recorded_registrations(
    paths: ManagedPaths,
    *,
    server_command: Path,
    neo_config_path: Path,
    apply: bool = True,
) -> tuple[dict[str, Any], ...]:
    # re-applies each recorded registration pointing at the newly promoted launcher, then persists the updated (active, server_command) back to the records
    records = read_registrations(paths)
    results: list[dict[str, Any]] = []
    updated: list[ClientRegistrationRecord] = []
    for record in records:
        if record.client == CLAUDE_DESKTOP:
            results.append(client_setup.setup_claude_desktop(apply=apply))
            updated.append(replace(record, server_command=str(server_command)))
            continue
        result = client_setup.setup_client(
            record.client,
            apply=apply,
            server_command=server_command,
            config_path=neo_config_path,
        )
        results.append(result)
        updated.append(
            replace(
                record,
                active=True,
                server_command=str(server_command),
                detail="restored" if apply else record.detail,
            )
        )
    if apply and updated:
        write_registrations(paths, tuple(updated))
    return tuple(results)


@dataclass(frozen=True)
class ClientChangeOutcome:
    """Result of reconciling live client registrations to a target set."""

    ok: bool
    connected: tuple[str, ...]
    added: tuple[str, ...]
    removed: tuple[str, ...]
    manual: tuple[str, ...]
    failures: tuple[str, ...]


def apply_client_selection(
    paths: ManagedPaths,
    target: Sequence[str],
    *,
    server_command: str | Path,
    on_event: Callable[[str, str], None] | None = None,
    label_fn: Callable[[str], str] | None = None,
) -> ClientChangeOutcome:
    # target vs currently recorded clients -> diff -> connect newcomers (setup_client), disconnect dropped ones (remove_client), persist the new target
    # shared by the wizard's "Manage connected clients" and setup.py manage-clients, so both reconcile identically
    # label_fn maps a client key to a display label for event messages only; this module can't import wizard/ (where CLIENT_LABELS lives), so callers supply their own mapping -- defaults to raw keys
    def emit(level: str, message: str) -> None:
        if on_event is not None:
            on_event(level, message)

    label = label_fn or (lambda client: client)
    known = {CLAUDE_CODE, CODEX, CLAUDE_DESKTOP}
    current = {r.client for r in read_registrations(paths) if r.client in known}
    target_list = list(dict.fromkeys(target))  # de-dupe, preserve order
    add = [c for c in target_list if c not in current]
    remove = [c for c in current if c not in target_list]
    failures: list[str] = []
    manual: list[str] = []

    for client in add:
        emit("action", f"Connecting {label(client)} ...")
        try:
            result = client_setup.setup_client(client, apply=True, server_command=server_command)
            if isinstance(result, dict) and result.get("manual_install_required"):
                note = str(result.get("instructions") or "Manual install required.")
                emit("warning", f"  {note}")
                manual.append(f"{client}: {note}")
        except Exception as exc:  # noqa: BLE001 - surfaced as a failure, never raised
            failures.append(f"{client}: {exc}")
            emit("error", f"  failed: {exc}")

    for client in remove:
        emit("action", f"Disconnecting {label(client)} ...")
        try:
            client_setup.remove_client(client, apply=True)
        except Exception as exc:  # noqa: BLE001
            failures.append(f"{client}: {exc}")
            emit("error", f"  failed: {exc}")

    if not add and not remove:
        emit("info", "No client changes to apply.")

    try:
        record_selection(paths, target_list)
    except Exception as exc:  # noqa: BLE001 - registration record is best-effort
        emit("warning", f"Could not update registration record: {exc}")

    return ClientChangeOutcome(
        ok=not failures,
        connected=tuple(target_list),
        added=tuple(add),
        removed=tuple(remove),
        manual=tuple(manual),
        failures=tuple(failures),
    )


def verify_registrations(
    paths: ManagedPaths, *, expected_server_command: Path
) -> tuple[RegistrationCheck, ...]:
    # per recorded client, confirms its live config actually names the managed launcher path; Claude Desktop is always ok (not machine-verifiable)
    expected = str(expected_server_command)
    checks: list[RegistrationCheck] = []
    for record in read_registrations(paths):
        if record.client == CODEX:
            config = Path(record.config_path) if record.config_path else client_setup._codex_cli_config_path()
            text = config.read_text(encoding="utf-8") if config.exists() else ""
            region = _codex_marked_region(text) or ""
            match = _COMMAND_RE.search(region)
            configured = _unescape_toml(match.group(1)) if match else None
            ok = configured == expected
            checks.append(
                RegistrationCheck(CODEX, ok, "launcher matches" if ok else f"expected {expected} in codex config")
            )
        elif record.client == CLAUDE_CODE:
            claude = client_setup.shutil.which("claude")
            if not claude:
                checks.append(RegistrationCheck(CLAUDE_CODE, True, "claude CLI unavailable; skipped verification"))
                continue
            result = subprocess.run(
                [claude, "mcp", "get", IDENTITY.mcp_server_name],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                errors="replace",
                **hidden_subprocess_kwargs(),
            )
            combined = f"{result.stdout}\n{result.stderr}"
            ok = result.returncode == 0 and expected in combined
            checks.append(
                RegistrationCheck(CLAUDE_CODE, ok, "launcher matches" if ok else "claude registration missing or stale")
            )
        elif record.client == CLAUDE_DESKTOP:
            checks.append(RegistrationCheck(CLAUDE_DESKTOP, True, "manual extension; not machine-verifiable"))
    return tuple(checks)
