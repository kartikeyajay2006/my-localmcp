from __future__ import annotations

import copy
from pathlib import Path

import pytest

from neo_localmcp import config


@pytest.fixture
def isolated_config(tmp_path, monkeypatch):
    app = tmp_path / "app"
    cfg_path = app / "config" / "config.yaml"
    defaults = copy.deepcopy(config.DEFAULT_CONFIG)
    defaults["memory"]["db_path"] = str(app / "sqlite" / "repo-context.sqlite")
    defaults["repo"]["max_files"] = None
    monkeypatch.setattr(config, "APP_DIR", app)
    monkeypatch.setattr(config, "CONFIG_PATH", cfg_path)
    monkeypatch.setattr(config, "DEFAULT_CONFIG", defaults)
    return app


@pytest.fixture
def isolated_app_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Provide a disposable managed home for installer tests.

    Installer modules receive this path explicitly. The environment variable is
    also set so subprocess-based tests inherit the same isolation boundary.
    """

    app_home = tmp_path / ".neo-localmcp"
    monkeypatch.setenv("NEO_LOCALMCP_HOME", str(app_home))
    return app_home
