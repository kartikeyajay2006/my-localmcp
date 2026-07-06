from __future__ import annotations

from neo_localmcp.wizard import fake_backend, real_backend
from neo_localmcp.wizard.backend import (
    OP_INSTALL,
    OP_UNINSTALL,
    WizardBackend,
    WizardState,
)


def _isolated_fake_backend(tmp_path, monkeypatch):
    # fake_backend persists simulated state to a fixed path relative to the
    # repo checkout (.wizard_preview/state.json), not something callers can
    # parameterize -- redirect it so tests don't read/write a real file in
    # this repo's working tree or leak state between tests (#13: this whole
    # module had zero pytest coverage before this file). Callers set
    # NEO_LOCALMCP_WIZARD_FAKE_STATE themselves (via monkeypatch) before
    # calling this, if they want a seed other than the "absent" default.
    monkeypatch.setattr(fake_backend, "_STATE_PATH", tmp_path / "wizard_state.json")
    return fake_backend.FakeBackend()


def test_fake_backend_satisfies_wizard_backend_protocol(tmp_path, monkeypatch):
    backend = _isolated_fake_backend(tmp_path, monkeypatch)
    assert isinstance(backend, WizardBackend)


def test_real_backend_satisfies_wizard_backend_protocol(isolated_app_home):
    assert isinstance(real_backend.RealBackend(), WizardBackend)


def test_fake_backend_detects_absent_by_default(tmp_path, monkeypatch):
    backend = _isolated_fake_backend(tmp_path, monkeypatch)
    info = backend.detect()
    assert info.state == "absent"
    assert info.installed_version is None


def test_fake_backend_detects_healthy_when_seeded(tmp_path, monkeypatch):
    monkeypatch.setenv("NEO_LOCALMCP_WIZARD_FAKE_STATE", "healthy")
    backend = _isolated_fake_backend(tmp_path, monkeypatch)
    info = backend.detect()
    assert info.state == "healthy"
    assert info.installed_version


def test_fake_backend_client_options_cover_every_client_key(tmp_path, monkeypatch):
    backend = _isolated_fake_backend(tmp_path, monkeypatch)
    options = backend.client_options()
    assert {o.key for o in options} == {"claude-code", "codex", "claude-desktop"}
    assert all(o.registered is False for o in options)


def test_fake_backend_ollama_info_reports_simulated_models(tmp_path, monkeypatch):
    backend = _isolated_fake_backend(tmp_path, monkeypatch)
    info = backend.ollama_info()
    assert info.reachable is True
    assert info.installed_models
    assert set(info.model_sizes) == set(info.installed_models)


def test_fake_backend_dry_run_install_makes_no_state_change(tmp_path, monkeypatch):
    backend = _isolated_fake_backend(tmp_path, monkeypatch)
    events = []
    state = WizardState(operation=OP_INSTALL, dry_run=True)
    outcome = backend.run_operation(state, events.append)
    assert outcome.ok is True
    assert outcome.status == "succeeded"
    assert backend.detect().state == "absent"  # dry run must not install anything
    assert any(e.level == "info" for e in events)


def test_fake_backend_install_then_uninstall_round_trips_state(tmp_path, monkeypatch):
    backend = _isolated_fake_backend(tmp_path, monkeypatch)
    monkeypatch.setattr(fake_backend, "_STEP_DELAY", 0.0)  # keep the smoke test fast
    install_outcome = backend.run_operation(WizardState(operation=OP_INSTALL), lambda e: None)
    assert install_outcome.ok is True
    assert backend.detect().state == "healthy"

    # A fresh backend instance re-reads the same (redirected) persisted state file,
    # the same way a later --fake run would see a prior simulated install.
    backend2 = _isolated_fake_backend(tmp_path, monkeypatch)
    assert backend2.detect().state == "healthy"

    uninstall_outcome = backend2.run_operation(WizardState(operation=OP_UNINSTALL), lambda e: None)
    assert uninstall_outcome.ok is True
    assert backend2.detect().state == "absent"
