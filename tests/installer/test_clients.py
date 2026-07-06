from __future__ import annotations

from pathlib import Path

import pytest

from neo_localmcp import client_setup
from neo_localmcp.installer import clients
from neo_localmcp.installer.clients import ClientRegistrationRecord
from neo_localmcp.installer.paths import ManagedPaths


def _paths(tmp_path: Path, platform: str = "posix") -> ManagedPaths:
    return ManagedPaths(
        root=tmp_path / ".neo-localmcp",
        platform=platform,  # type: ignore[arg-type]
        home=tmp_path,
        allow_test_root=True,
    )


@pytest.fixture
def client_home(tmp_path, monkeypatch):
    """Isolate all client-owned config under a disposable home, no real CLIs."""
    home = tmp_path / "home"
    home.mkdir()
    codex = home / ".codex" / "config.toml"
    monkeypatch.setattr(client_setup.Path, "home", staticmethod(lambda: home))
    monkeypatch.setattr(client_setup, "_codex_cli_config_path", lambda: codex)
    monkeypatch.setattr(client_setup, "ensure_config", lambda *a, **k: None)
    monkeypatch.setattr(client_setup.shutil, "which", lambda name: None)
    return home


# --------------------------------------------------------------------------- #
# Record + persistence
# --------------------------------------------------------------------------- #


def test_record_json_roundtrips():
    record = ClientRegistrationRecord("codex", True, False, "/venv/bin/neo-localmcp-server", "/x/config.toml", "note")
    assert ClientRegistrationRecord.from_json(record.to_json()) == record


def test_read_registrations_missing_is_empty(tmp_path):
    assert clients.read_registrations(_paths(tmp_path)) == ()


def test_read_registrations_corrupt_is_empty(tmp_path):
    paths = _paths(tmp_path)
    path = clients.registrations_path(paths)
    path.parent.mkdir(parents=True)
    path.write_text("{ not json", encoding="utf-8")
    assert clients.read_registrations(paths) == ()


def test_write_read_roundtrip(tmp_path):
    paths = _paths(tmp_path)
    records = (
        ClientRegistrationRecord("codex", True, False, "/venv/bin/neo-localmcp-server", "/c.toml", "x"),
        ClientRegistrationRecord("claude-desktop", True, True, None, None, "manual"),
    )
    clients.write_registrations(paths, records)
    assert clients.read_registrations(paths) == records


def test_delete_registrations_removes_file(tmp_path):
    paths = _paths(tmp_path)
    clients.write_registrations(paths, (ClientRegistrationRecord("codex", True, False, "x", "y", ""),))
    assert clients.registrations_path(paths).exists()
    clients.delete_registrations(paths)
    assert not clients.registrations_path(paths).exists()


# --------------------------------------------------------------------------- #
# Executable-target injection (Step 2)
# --------------------------------------------------------------------------- #


def _toml_path(path: Path) -> str:
    return str(path).replace("\\", "\\\\")


def test_codex_block_uses_injected_posix_venv_launcher(tmp_path):
    paths = _paths(tmp_path, "posix")
    block = client_setup._codex_block(server_command=paths.server_executable, config_path=paths.config / "config.yaml")
    assert paths.server_executable.as_posix().endswith("venv/bin/neo-localmcp-server")
    assert _toml_path(paths.server_executable) in block


def test_mcp_block_uses_injected_windows_venv_launcher(tmp_path):
    paths = _paths(tmp_path, "windows")
    block = client_setup._mcp_server_block(server_command=paths.server_executable, config_path=paths.config / "config.yaml")
    command = block["neo-localmcp"]["command"]
    assert Path(command) == paths.server_executable
    assert Path(block["neo-localmcp"]["env"]["NEO_LOCALMCP_CONFIG"]) == paths.config / "config.yaml"


def test_setup_claude_code_manual_uses_injected_launcher(client_home, monkeypatch, tmp_path):
    paths = _paths(tmp_path, "posix")
    result = client_setup.setup_claude_code(apply=False, server_command=paths.server_executable)
    assert str(paths.server_executable) in result["manual_mcp_user"]
    assert result["server_command"] == str(paths.server_executable)


def test_injected_launcher_never_infers_legacy_bin_shim(tmp_path):
    paths = _paths(tmp_path, "posix")
    block = client_setup._codex_block(server_command=paths.server_executable)
    # The legacy side-by-side layout wrote the launcher under <root>/bin; the
    # injected venv launcher must be used verbatim instead.
    assert _toml_path(paths.root / "bin" / "neo-localmcp-server") not in block
    assert _toml_path(paths.server_executable) in block


# --------------------------------------------------------------------------- #
# snapshot
# --------------------------------------------------------------------------- #


def test_snapshot_with_no_state_records_empty(client_home, tmp_path):
    paths = _paths(tmp_path)
    assert clients.snapshot_clients(paths) == ()
    assert clients.registrations_path(paths).exists()


def test_snapshot_detects_codex_block(client_home, tmp_path):
    paths = _paths(tmp_path)
    client_setup.setup_codex_cli(apply=True, server_command=paths.server_executable, config_path=paths.config / "config.yaml")

    records = clients.snapshot_clients(paths)

    codex = {r.client: r for r in records}["codex"]
    assert codex.active is True
    assert codex.server_command == str(paths.server_executable)


def test_snapshot_detects_claude_code_slash_commands_without_cli(client_home, tmp_path):
    paths = _paths(tmp_path)
    commands = client_home / ".claude" / "commands" / client_setup.IDENTITY.slash_prefix
    commands.mkdir(parents=True)
    (commands / "context.md").write_text("x", encoding="utf-8")

    records = clients.snapshot_clients(paths)

    claude = {r.client: r for r in records}["claude-code"]
    assert claude.active is True
    assert "CLI unavailable" in claude.detail


def test_snapshot_carries_prior_desktop_record(client_home, tmp_path):
    paths = _paths(tmp_path)
    clients.write_registrations(
        paths,
        (ClientRegistrationRecord("claude-desktop", True, True, None, None, "manual"),),
    )
    records = clients.snapshot_clients(paths)
    assert any(r.client == "claude-desktop" and r.manual for r in records)


# --------------------------------------------------------------------------- #
# record_selection
# --------------------------------------------------------------------------- #


def test_record_selection_dedupes_codex_surfaces(client_home, tmp_path):
    paths = _paths(tmp_path)
    records = clients.record_selection(paths, ["codex-cli", "codex-desktop", "claude-code"])
    keys = [r.client for r in records]
    assert keys == ["codex", "claude-code"]


# --------------------------------------------------------------------------- #
# remove / restore
# --------------------------------------------------------------------------- #


def test_remove_active_registrations_strips_codex_block_and_retains_records(client_home, tmp_path):
    paths = _paths(tmp_path)
    codex = client_home / ".codex" / "config.toml"
    codex.parent.mkdir(parents=True)
    codex.write_text('[user]\nkeep = "me"\n', encoding="utf-8")
    client_setup.setup_codex_cli(apply=True, server_command=paths.server_executable, config_path=paths.config / "config.yaml")
    clients.snapshot_clients(paths)

    results = clients.remove_active_registrations(paths, apply=True)

    text = codex.read_text(encoding="utf-8")
    assert "# BEGIN neo-localmcp" not in text
    assert '[user]' in text and 'keep = "me"' in text
    # Records are retained so a later reinstall can reconnect the same surfaces.
    assert clients.registrations_path(paths).exists()
    assert any(r.client == "codex" for r in clients.read_registrations(paths))
    assert results  # a removal was performed


def test_remove_active_registrations_dry_run_mutates_nothing(client_home, tmp_path):
    paths = _paths(tmp_path)
    codex = client_home / ".codex" / "config.toml"
    client_setup.setup_codex_cli(apply=True, server_command=paths.server_executable)
    clients.snapshot_clients(paths)
    before = codex.read_text(encoding="utf-8")

    clients.remove_active_registrations(paths, apply=False)

    assert codex.read_text(encoding="utf-8") == before


def test_restore_points_registrations_at_new_launcher(client_home, tmp_path):
    paths = _paths(tmp_path)
    old_launcher = tmp_path / "old" / "venv" / "bin" / "neo-localmcp-server"
    client_setup.setup_codex_cli(apply=True, server_command=old_launcher, config_path=paths.config / "config.yaml")
    clients.snapshot_clients(paths)
    clients.remove_active_registrations(paths, apply=True)

    new_launcher = paths.server_executable
    clients.restore_recorded_registrations(
        paths, server_command=new_launcher, neo_config_path=paths.config / "config.yaml", apply=True
    )

    codex_text = (client_home / ".codex" / "config.toml").read_text(encoding="utf-8")
    assert _toml_path(new_launcher) in codex_text
    assert _toml_path(old_launcher) not in codex_text
    codex_record = {r.client: r for r in clients.read_registrations(paths)}["codex"]
    assert codex_record.server_command == str(new_launcher)


def test_restore_preserves_user_codex_content_byte_for_byte(client_home, tmp_path):
    paths = _paths(tmp_path)
    codex = client_home / ".codex" / "config.toml"
    codex.parent.mkdir(parents=True)
    codex.write_text('[user]\nkeep = "me"\n', encoding="utf-8")
    clients.write_registrations(
        paths, (ClientRegistrationRecord("codex", True, False, None, str(codex), "x"),)
    )

    clients.restore_recorded_registrations(
        paths, server_command=paths.server_executable, neo_config_path=paths.config / "config.yaml", apply=True
    )

    text = codex.read_text(encoding="utf-8")
    assert '[user]' in text and 'keep = "me"' in text
    assert _toml_path(paths.server_executable) in text


# --------------------------------------------------------------------------- #
# verify
# --------------------------------------------------------------------------- #


def test_verify_registrations_detects_stale_codex_launcher(client_home, tmp_path):
    paths = _paths(tmp_path)
    old_launcher = tmp_path / "old" / "venv" / "bin" / "neo-localmcp-server"
    client_setup.setup_codex_cli(apply=True, server_command=old_launcher, config_path=paths.config / "config.yaml")
    clients.snapshot_clients(paths)

    checks = {c.client: c for c in clients.verify_registrations(paths, expected_server_command=paths.server_executable)}
    assert checks["codex"].ok is False

    checks_ok = {c.client: c for c in clients.verify_registrations(paths, expected_server_command=old_launcher)}
    assert checks_ok["codex"].ok is True


# --------------------------------------------------------------------------- #
# apply_client_selection
# --------------------------------------------------------------------------- #


def test_apply_client_selection_connects_and_disconnects(client_home, tmp_path, monkeypatch):
    from neo_localmcp.installer import clients

    paths = ManagedPaths.from_environment()
    clients.record_selection(paths, ["claude-code"])

    calls = []
    monkeypatch.setattr(
        clients.client_setup, "setup_client",
        lambda client, apply=True, **kw: calls.append(("setup", client)) or {"client": client, "ok": True},
    )
    monkeypatch.setattr(
        clients.client_setup, "remove_client",
        lambda client, apply=True, **kw: calls.append(("remove", client)) or {"client": client, "ok": True},
    )

    events = []
    outcome = clients.apply_client_selection(
        paths, ["codex"], server_command="neo-localmcp-server",
        on_event=lambda level, message: events.append((level, message)),
    )

    assert outcome.ok
    assert outcome.added == ("codex",)
    assert outcome.removed == ("claude-code",)
    assert ("setup", "codex") in calls
    assert ("remove", "claude-code") in calls
    assert any(level == "action" for level, _ in events)


def test_apply_client_selection_uses_label_fn_for_connect_and_disconnect_messages(
    client_home, tmp_path, monkeypatch
):
    from neo_localmcp.installer import clients

    paths = ManagedPaths.from_environment()
    clients.record_selection(paths, ["claude-code"])
    monkeypatch.setattr(
        clients.client_setup, "setup_client",
        lambda client, apply=True, **kw: {"client": client, "ok": True},
    )
    monkeypatch.setattr(
        clients.client_setup, "remove_client",
        lambda client, apply=True, **kw: {"client": client, "ok": True},
    )
    labels = {"claude-code": "Claude Code", "codex": "Codex (CLI / IDE)"}

    events = []
    clients.apply_client_selection(
        paths, ["codex"], server_command="neo-localmcp-server",
        on_event=lambda level, message: events.append((level, message)),
        label_fn=lambda client: labels.get(client, client),
    )

    messages = [message for _, message in events]
    assert any("Connecting Codex (CLI / IDE) ..." == m for m in messages)
    assert any("Disconnecting Claude Code ..." == m for m in messages)


def test_apply_client_selection_without_label_fn_uses_raw_client_keys(
    client_home, tmp_path, monkeypatch
):
    from neo_localmcp.installer import clients

    paths = ManagedPaths.from_environment()
    monkeypatch.setattr(
        clients.client_setup, "setup_client",
        lambda client, apply=True, **kw: {"client": client, "ok": True},
    )

    events = []
    clients.apply_client_selection(
        paths, ["codex"], server_command="neo-localmcp-server",
        on_event=lambda level, message: events.append((level, message)),
    )

    messages = [message for _, message in events]
    assert any("Connecting codex ..." == m for m in messages)


def test_apply_client_selection_records_failures_without_raising(client_home, tmp_path, monkeypatch):
    from neo_localmcp.installer import clients

    paths = ManagedPaths.from_environment()

    def _boom(client, apply=True, **kw):
        raise RuntimeError("simulated failure")

    monkeypatch.setattr(clients.client_setup, "setup_client", _boom)

    outcome = clients.apply_client_selection(paths, ["claude-code"], server_command="neo-localmcp-server")

    assert not outcome.ok
    assert "claude-code" in outcome.failures[0]


def test_apply_client_selection_with_no_changes_still_records_target(client_home, tmp_path, monkeypatch):
    from neo_localmcp.installer import clients

    paths = ManagedPaths.from_environment()
    clients.record_selection(paths, ["claude-code"])

    outcome = clients.apply_client_selection(paths, ["claude-code"], server_command="neo-localmcp-server")

    assert outcome.ok
    assert outcome.added == ()
    assert outcome.removed == ()
