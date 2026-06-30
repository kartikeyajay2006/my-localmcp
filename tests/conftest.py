from __future__ import annotations

import copy

import pytest

from neo_localmcp import config, ollama_client


@pytest.fixture
def isolated_config(tmp_path, monkeypatch):
    app = tmp_path / "app"
    cfg_path = app / "config.json"
    defaults = copy.deepcopy(config.DEFAULT_CONFIG)
    defaults["memory"]["db_path"] = str(app / "repo.sqlite")
    defaults["repo"]["max_files"] = None
    monkeypatch.setattr(config, "APP_DIR", app)
    monkeypatch.setattr(config, "CONFIG_PATH", cfg_path)
    monkeypatch.setattr(config, "DEFAULT_CONFIG", defaults)
    monkeypatch.setattr(ollama_client, "APP_DIR", app)
    monkeypatch.setattr(ollama_client, "STATE_PATH", app / "ollama-supervisor.json")
    monkeypatch.setattr(ollama_client, "LOCK_PATH", app / "ollama-supervisor.lock")
    return app
