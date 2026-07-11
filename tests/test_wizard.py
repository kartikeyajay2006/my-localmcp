from __future__ import annotations

from neo_localmcp.installer.wizard import live_backend, preview_backend
from neo_localmcp.installer.wizard.backend import (
    OP_INSTALL,
    OP_UNINSTALL,
    WizardBackend,
    WizardState,
)


def _isolated_preview_backend(tmp_path, monkeypatch):
    # preview_backend persists simulated state to a fixed path relative to the
    # repo checkout (.wizard_preview/state.json), not something callers can
    # parameterize -- redirect it so tests don't read/write a real file in
    # this repo's working tree or leak state between tests (#13: this whole
    # module had zero pytest coverage before this file). Callers set
    # NEO_LOCALMCP_WIZARD_PREVIEW_STATE themselves (via monkeypatch) before
    # calling this, if they want a seed other than the "absent" default.
    monkeypatch.setattr(preview_backend, "_STATE_PATH", tmp_path / "wizard_state.json")
    return preview_backend.PreviewBackend()


def test_preview_backend_satisfies_wizard_backend_protocol(tmp_path, monkeypatch):
    backend = _isolated_preview_backend(tmp_path, monkeypatch)
    assert isinstance(backend, WizardBackend)


def test_live_backend_satisfies_wizard_backend_protocol(isolated_app_home):
    assert isinstance(live_backend.LiveBackend(), WizardBackend)


def test_preview_backend_detects_absent_by_default(tmp_path, monkeypatch):
    backend = _isolated_preview_backend(tmp_path, monkeypatch)
    info = backend.detect()
    assert info.state == "absent"
    assert info.installed_version is None


def test_preview_backend_detects_healthy_when_seeded(tmp_path, monkeypatch):
    monkeypatch.setenv("NEO_LOCALMCP_WIZARD_PREVIEW_STATE", "healthy")
    backend = _isolated_preview_backend(tmp_path, monkeypatch)
    info = backend.detect()
    assert info.state == "healthy"
    assert info.installed_version


def test_preview_backend_client_options_cover_every_client_key(tmp_path, monkeypatch):
    backend = _isolated_preview_backend(tmp_path, monkeypatch)
    options = backend.client_options()
    assert {o.key for o in options} == {"claude-code", "codex", "claude-desktop"}
    assert all(o.registered is False for o in options)


def test_preview_backend_ollama_info_reports_simulated_models(tmp_path, monkeypatch):
    backend = _isolated_preview_backend(tmp_path, monkeypatch)
    info = backend.ollama_info()
    assert info.reachable is True
    assert info.installed_models
    assert set(info.model_sizes) == set(info.installed_models)


def test_preview_backend_dry_run_install_makes_no_state_change(tmp_path, monkeypatch):
    backend = _isolated_preview_backend(tmp_path, monkeypatch)
    events = []
    state = WizardState(operation=OP_INSTALL, dry_run=True)
    outcome = backend.run_operation(state, events.append)
    assert outcome.ok is True
    assert outcome.status == "succeeded"
    assert backend.detect().state == "absent"  # dry run must not install anything
    assert any(e.level == "info" for e in events)


def test_preview_backend_install_then_uninstall_round_trips_state(tmp_path, monkeypatch):
    backend = _isolated_preview_backend(tmp_path, monkeypatch)
    monkeypatch.setattr(preview_backend, "_STEP_DELAY", 0.0)  # keep the smoke test fast
    install_outcome = backend.run_operation(WizardState(operation=OP_INSTALL), lambda e: None)
    assert install_outcome.ok is True
    assert backend.detect().state == "healthy"

    # A fresh backend instance re-reads the same (redirected) persisted state file,
    # the same way a later --preview run would see a prior simulated install.
    backend2 = _isolated_preview_backend(tmp_path, monkeypatch)
    assert backend2.detect().state == "healthy"

    uninstall_outcome = backend2.run_operation(WizardState(operation=OP_UNINSTALL), lambda e: None)
    assert uninstall_outcome.ok is True
    assert backend2.detect().state == "absent"


def test_live_backend_apply_client_changes_uses_shared_helper(isolated_app_home, monkeypatch):
    from neo_localmcp.installer import clients as clients_mod
    from neo_localmcp.installer.wizard.backend import WizardState
    from neo_localmcp.installer.wizard.live_backend import LiveBackend

    calls = []
    monkeypatch.setattr(
        clients_mod.client_setup, "setup_client",
        lambda client, apply=True, **kw: calls.append(client) or {"client": client, "ok": True},
    )

    backend = LiveBackend()
    state = WizardState(operation="manage-clients", selected_clients=["claude-code"])
    events = []
    outcome = backend.apply_client_changes(state, lambda event: events.append(event))

    assert outcome.ok
    assert calls == ["claude-code"]
    assert any(e.level == "action" for e in events)
    assert any(e.message == "Connecting Claude Code ..." for e in events)


def test_live_backend_apply_ollama_config_uses_shared_helper(isolated_app_home):
    from neo_localmcp import config
    from neo_localmcp.installer.wizard.backend import WizardState
    from neo_localmcp.installer.wizard.live_backend import LiveBackend

    backend = LiveBackend()
    state = WizardState(
        operation="config-ollama", fast_model="new-fast", summary_model="new-summary",
        ollama_base_url="http://127.0.0.1:11434",
    )
    outcome = backend.apply_ollama_config(state, lambda event: None)

    assert outcome.ok
    assert config.load_config()["ollama"]["fast_model"] == "new-fast"
    assert config.load_config()["ollama"]["summary_model"] == "new-summary"


def test_live_backend_writes_and_disables_embed_model(isolated_app_home):
    """The wizard's embed phase threads state.embed_model through to config:
    a model name enables the semantic layer; an empty string (user chose 'None')
    disables it back to None -- the tri-state configure_models honors."""
    from neo_localmcp import config
    from neo_localmcp.installer.wizard.backend import WizardState
    from neo_localmcp.installer.wizard.live_backend import LiveBackend

    backend = LiveBackend()
    enable = WizardState(operation="config-ollama", fast_model="f", summary_model="s",
                         embed_model="nomic-embed-text", ollama_base_url="http://127.0.0.1:11434")
    assert backend.apply_ollama_config(enable, lambda e: None).ok
    assert config.load_config()["ollama"]["embed_model"] == "nomic-embed-text"

    disable = WizardState(operation="config-ollama", fast_model="f", summary_model="s",
                          embed_model="", ollama_base_url="http://127.0.0.1:11434")
    assert backend.apply_ollama_config(disable, lambda e: None).ok
    assert config.load_config()["ollama"]["embed_model"] is None  # "" disabled it


def test_console_embed_phase_picks_and_disables(tmp_path, monkeypatch):
    """Smoke the interactive embed phase against the preview backend: choosing a
    numbered model enables it; choosing 0 leaves the semantic layer disabled."""
    from neo_localmcp.installer.wizard.console import ConsoleWizard

    backend = _isolated_preview_backend(tmp_path, monkeypatch)
    wizard = ConsoleWizard(backend, fake=True)
    wizard.state.configure_ollama = True

    # nomic-embed-text:latest is entry among the fake installed models; drive a pick then a disable.
    info = backend.ollama_info()
    embed_index = info.installed_models.index("nomic-embed-text:latest") + 1

    replies = iter([str(embed_index)])
    monkeypatch.setattr(ConsoleWizard, "_input", lambda self, prompt: next(replies))
    wizard._phase_ollama_embed()
    assert wizard.state.embed_model == "nomic-embed-text:latest"

    replies2 = iter(["0"])
    monkeypatch.setattr(ConsoleWizard, "_input", lambda self, prompt: next(replies2))
    wizard._phase_ollama_embed()
    assert wizard.state.embed_model == ""  # 0 -> disabled


def test_ollama_info_carries_embed_model(isolated_app_home):
    from neo_localmcp import config
    from neo_localmcp.installer.wizard.live_backend import LiveBackend

    cfg = config.load_config(); cfg["ollama"]["embed_model"] = "mxbai-embed-large"; config.save_config(cfg)
    info = LiveBackend().ollama_info()
    assert info.embed_model == "mxbai-embed-large"


def test_preview_backend_config_ollama_reports_embed(tmp_path, monkeypatch):
    backend = _isolated_preview_backend(tmp_path, monkeypatch)
    from neo_localmcp.installer.wizard.backend import WizardState
    state = WizardState(operation="config-ollama", fast_model="qwen3:8b",
                        summary_model="qwen3-coder:30b", embed_model="nomic-embed-text:latest")
    outcome = backend.apply_ollama_config(state, lambda e: None)
    assert outcome.ok
    assert any("embed_model" in line and "nomic-embed-text" in line for line in outcome.detail_lines)
    assert backend.ollama_info().embed_model == "nomic-embed-text:latest"


def test_isolated_app_home_actually_redirects_in_process_config_writes(isolated_app_home):
    """Regression: isolated_app_home must isolate in-process config reads/writes,
    not just the env var for subprocess children. config.py's APP_DIR/CONFIG_PATH
    are computed once at module import time from NEO_LOCALMCP_HOME and are never
    re-derived live -- unlike NEO_LOCALMCP_CONFIG, which config_path() does check
    live. Before this fix, setting only the env var left config.load_config()/
    save_config() silently resolving to the real ~/.neo-localmcp/config/config.yaml
    for any in-process caller (e.g. LiveBackend.apply_ollama_config above), which
    is exactly how a routine test run could stamp this fixture's hardcoded
    fast_model="new-fast"/summary_model="new-summary" into a real user's config."""
    from neo_localmcp import config

    resolved = config.config_path()
    assert str(resolved).startswith(str(isolated_app_home))
    assert resolved != config._INITIAL_CONFIG_PATH
