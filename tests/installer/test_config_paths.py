from __future__ import annotations

import json
from pathlib import Path

from neo_localmcp import config, lifecycle, ollama_client


def test_default_paths_follow_app_dir_dynamically(
    tmp_path: Path, monkeypatch
) -> None:
    app_home = tmp_path / ".neo-localmcp"
    monkeypatch.setattr(config, "APP_DIR", app_home)
    monkeypatch.delenv("NEO_LOCALMCP_CONFIG", raising=False)

    assert config.config_dir() == app_home / "config"
    assert config.config_path() == app_home / "config" / "config.yaml"
    assert config.sqlite_dir() == app_home / "sqlite"
    assert config.default_db_path() == app_home / "sqlite" / "repo-context.sqlite"
    assert config.cache_dir() == app_home / "cache"
    assert config.process_registry_dir() == app_home / "cache" / "processes"


def test_explicit_config_override_is_preserved(
    tmp_path: Path, monkeypatch
) -> None:
    override = tmp_path / "custom" / "neo.json"
    monkeypatch.setenv("NEO_LOCALMCP_CONFIG", str(override))

    assert config.config_path() == override


def test_ensure_config_creates_canonical_parent_and_database_default(
    tmp_path: Path, monkeypatch
) -> None:
    app_home = tmp_path / ".neo-localmcp"
    monkeypatch.setattr(config, "APP_DIR", app_home)
    monkeypatch.delenv("NEO_LOCALMCP_CONFIG", raising=False)

    written = config.ensure_config()
    payload = json.loads(written.read_text(encoding="utf-8"))

    assert written == app_home / "config" / "config.yaml"
    assert payload["memory"]["db_path"] == str(
        app_home / "sqlite" / "repo-context.sqlite"
    )
    assert not (app_home / "config.yaml").exists()


def test_ollama_state_and_lock_live_under_cache(
    tmp_path: Path, monkeypatch
) -> None:
    app_home = tmp_path / ".neo-localmcp"
    monkeypatch.setattr(config, "APP_DIR", app_home)

    assert ollama_client._state_path() == app_home / "cache" / "ollama" / "supervisor.json"
    assert ollama_client._lock_path() == app_home / "cache" / "ollama" / "supervisor.lock"


def test_server_registry_lives_under_process_cache(
    tmp_path: Path, monkeypatch
) -> None:
    app_home = tmp_path / ".neo-localmcp"
    monkeypatch.setattr(config, "APP_DIR", app_home)

    assert lifecycle._servers_root() == app_home / "cache" / "processes" / "servers"
