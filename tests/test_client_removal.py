"""Coverage for the 1.0.9 (P9f) client-removal functions -- the inverse of the
setup_* registration path. These prove that removal strips exactly what setup wrote
and leaves the user's own surrounding config untouched, without needing a real
Claude/Codex CLI present."""

from __future__ import annotations

from pathlib import Path

from neo_localmcp import client_setup


def test_strip_marked_block_inverts_replace_marked_block():
    original = '[existing]\nkey = "value"\n'
    with_block = client_setup._replace_marked_block(original, client_setup._codex_block())
    assert "# BEGIN neo-localmcp" in with_block
    stripped = client_setup._strip_marked_block(with_block)
    assert "neo-localmcp" not in stripped
    assert "[existing]" in stripped
    assert 'key = "value"' in stripped


def test_strip_marked_block_only_our_block_yields_empty():
    only_ours = client_setup._codex_block()
    assert client_setup._strip_marked_block(only_ours).strip() == ""


def test_strip_marked_block_without_markers_is_noop():
    untouched = '[user]\nfoo = "bar"\n'
    assert client_setup._strip_marked_block(untouched) == untouched


def test_setup_then_remove_codex_roundtrips_config(tmp_path, monkeypatch):
    cfg = tmp_path / "config.toml"
    cfg.write_text('[existing]\nkey = "value"\n', encoding="utf-8")
    monkeypatch.setattr(client_setup, "_codex_cli_config_path", lambda: cfg)

    client_setup.setup_codex_cli(apply=True)
    assert "# BEGIN neo-localmcp" in cfg.read_text(encoding="utf-8")

    result = client_setup.remove_codex(apply=True)
    text = cfg.read_text(encoding="utf-8")
    assert result["block_present"] is True
    assert result["block_present_after"] is False
    assert "neo-localmcp" not in text
    assert '[existing]' in text and 'key = "value"' in text


def test_remove_codex_dry_run_does_not_modify(tmp_path, monkeypatch):
    cfg = tmp_path / "config.toml"
    monkeypatch.setattr(client_setup, "_codex_cli_config_path", lambda: cfg)
    client_setup.setup_codex_cli(apply=True)
    before = cfg.read_text(encoding="utf-8")

    result = client_setup.remove_codex(apply=False)
    assert result["applied"] is False
    assert result["block_present"] is True
    assert cfg.read_text(encoding="utf-8") == before


def test_remove_codex_without_existing_block_is_safe(tmp_path, monkeypatch):
    cfg = tmp_path / "config.toml"
    cfg.write_text('[user]\nfoo = "bar"\n', encoding="utf-8")
    monkeypatch.setattr(client_setup, "_codex_cli_config_path", lambda: cfg)

    result = client_setup.remove_codex(apply=True)
    assert result["block_present"] is False
    assert cfg.read_text(encoding="utf-8") == '[user]\nfoo = "bar"\n'


def test_remove_claude_code_removes_slash_commands(tmp_path, monkeypatch):
    monkeypatch.setattr(client_setup.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(client_setup.shutil, "which", lambda name: None)  # no claude CLI
    commands = tmp_path / ".claude" / "commands" / client_setup.IDENTITY.slash_prefix
    commands.mkdir(parents=True)
    (commands / "context.md").write_text("x", encoding="utf-8")

    result = client_setup.remove_claude_code(apply=True)
    assert result["commands_dir_exists_after"] is False
    assert not commands.exists()
    assert any("claude CLI not found" in a for a in result["actions"])


def test_remove_claude_desktop_is_manual_only():
    result = client_setup.remove_claude_desktop(apply=True)
    assert result["applied"] is False
    assert result["manual_removal_required"] is True


def test_remove_clients_all_expands_to_three_surfaces(tmp_path, monkeypatch):
    monkeypatch.setattr(client_setup.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(client_setup.shutil, "which", lambda name: None)
    results = client_setup.remove_clients(["all"], apply=False)
    clients = {r["client"] for r in results}
    assert clients == {"claude-code", "codex", "claude-desktop"}
