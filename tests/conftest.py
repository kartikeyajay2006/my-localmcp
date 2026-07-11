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

    Setting NEO_LOCALMCP_HOME alone is NOT enough for in-process isolation:
    config.py's APP_DIR/CONFIG_PATH are computed once at module import time
    from that env var and never re-derived live the way NEO_LOCALMCP_CONFIG
    is inside config_path() -- so a test that only sets the env var and then
    calls config.load_config()/save_config() in-process (not via a spawned
    subprocess) silently falls through to the real ~/.neo-localmcp. Mirror
    isolated_config's proven pattern and patch the module globals directly too.
    """

    app_home = tmp_path / ".neo-localmcp"
    monkeypatch.setenv("NEO_LOCALMCP_HOME", str(app_home))
    monkeypatch.setattr(config, "APP_DIR", app_home)
    monkeypatch.setattr(config, "CONFIG_PATH", app_home / "config" / "config.yaml")
    return app_home
