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


def test_preview_backend_ollama_info_reports_model_capabilities(tmp_path, monkeypatch):
    """The simulated model list distinguishes embedding-only models from chat/
    completion models (real Ollama's own reported capability tags), so the fake
    matches the real backend's shape closely enough to smoke-test the console UI."""
    backend = _isolated_preview_backend(tmp_path, monkeypatch)
    info = backend.ollama_info()
    assert set(info.model_capabilities) == set(info.installed_models)
    assert "embedding" in info.model_capabilities["nomic-embed-text:latest"]
    assert "embedding" not in info.model_capabilities["qwen3:8b"]
    assert "completion" in info.model_capabilities["qwen3:8b"]


def test_live_backend_ollama_info_reports_model_capabilities(isolated_app_home, monkeypatch):
    from neo_localmcp import ollama_client
    from neo_localmcp.installer.wizard.live_backend import LiveBackend

    monkeypatch.setattr(ollama_client, "status", lambda: {
        "state": "ready", "installed_models": ["qwen3:8b", "nomic-embed-text:latest"],
    })
    monkeypatch.setattr(ollama_client, "model_details", lambda: {
        "qwen3:8b": {"size": 5_200_000_000, "capabilities": ["completion", "tools"], "family": "qwen3", "parameter_size": "8.2B"},
        "nomic-embed-text:latest": {"size": 274_000_000, "capabilities": ["embedding"], "family": "nomic-bert", "parameter_size": "137M"},
    })
    info = LiveBackend().ollama_info()
    assert info.model_capabilities["qwen3:8b"] == ("completion", "tools")
    assert info.model_capabilities["nomic-embed-text:latest"] == ("embedding",)
    assert info.model_sizes["qwen3:8b"]  # still human-formatted size, unchanged behavior


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


def test_confirm_phase_asks_a_single_default_yes_question_no_dry_run_prompt(tmp_path, monkeypatch):
    """The interactive review-and-confirm page used to ask two questions
    (a 'Preview only (dry run)?' toggle, then a separate proceed question) --
    confusing UX for a rarely-used capability. It must now ask exactly one
    question ('Install as shown?', default Yes), and never set dry_run."""
    from neo_localmcp.installer.wizard.console import ConsoleWizard

    backend = _isolated_preview_backend(tmp_path, monkeypatch)
    wizard = ConsoleWizard(backend, fake=True)
    wizard.state.operation = OP_INSTALL

    prompts: list[str] = []

    def fake_input(self, prompt):
        prompts.append(prompt)
        return ""  # accept the default every time

    monkeypatch.setattr(ConsoleWizard, "_input", fake_input)
    wizard._phase_confirm()  # must not raise _Abort on the default

    assert len(prompts) == 1, f"expected exactly one confirm prompt, got {prompts}"
    assert "dry run" not in prompts[0].lower()
    assert "preview only" not in prompts[0].lower()
    assert wizard.state.dry_run is False


def test_path_phase_defaults_to_adding_managed_cli_directory(tmp_path, monkeypatch):
    from neo_localmcp.installer.wizard.console import ConsoleWizard

    backend = _isolated_preview_backend(tmp_path, monkeypatch)
    wizard = ConsoleWizard(backend, fake=True)
    wizard.state.operation = OP_INSTALL

    monkeypatch.setattr(ConsoleWizard, "_input", lambda self, prompt: "")
    wizard._phase_path()

    assert wizard.state.add_to_path is True
    assert any(label == "PATH" and "enabled" in value for label, value in wizard.summary)


def test_live_backend_updates_path_only_after_successful_opt_in(isolated_app_home, monkeypatch):
    from neo_localmcp.installer.path import PathUpdate
    from neo_localmcp.installer.types import Operation, OperationResult, OperationStatus

    backend = live_backend.LiveBackend()
    updates = []
    result = OperationResult(Operation.INSTALL, OperationStatus.SUCCEEDED, (), ())
    monkeypatch.setattr(live_backend, "install", lambda *_args, **_kwargs: result)
    monkeypatch.setattr(backend, "_build_desktop_bundle", lambda _emit: None)
    monkeypatch.setattr(
        live_backend,
        "add_to_path",
        lambda paths: updates.append(paths) or PathUpdate(True, paths.home / ".zshrc"),
    )

    outcome = backend.run_operation(
        WizardState(operation=OP_INSTALL, add_to_path=True), lambda _event: None,
    )

    assert outcome.ok is True
    assert updates == [backend._paths]


def test_live_backend_leaves_path_unchanged_when_wizard_opt_outs(isolated_app_home, monkeypatch):
    from neo_localmcp.installer.types import Operation, OperationResult, OperationStatus

    backend = live_backend.LiveBackend()
    updates = []
    result = OperationResult(Operation.INSTALL, OperationStatus.SUCCEEDED, (), ())
    monkeypatch.setattr(live_backend, "install", lambda *_args, **_kwargs: result)
    monkeypatch.setattr(backend, "_build_desktop_bundle", lambda _emit: None)
    monkeypatch.setattr(live_backend, "add_to_path", lambda paths: updates.append(paths))

    outcome = backend.run_operation(
        WizardState(operation=OP_INSTALL, add_to_path=False), lambda _event: None,
    )

    assert outcome.ok is True
    assert updates == []


# --- Ollama URL validation + model-kind labeling (pure functions) ----------

def test_is_valid_ollama_url_accepts_common_forms():
    from neo_localmcp.installer.wizard.console import _is_valid_ollama_url

    assert _is_valid_ollama_url("http://127.0.0.1:11434")
    assert _is_valid_ollama_url("https://ollama.example.com:11434")
    assert _is_valid_ollama_url("http://neofedora:11434/")
    assert _is_valid_ollama_url("http://localhost")  # no port is fine


def test_is_valid_ollama_url_rejects_malformed_input():
    from neo_localmcp.installer.wizard.console import _is_valid_ollama_url

    assert not _is_valid_ollama_url("")
    assert not _is_valid_ollama_url("not a url")
    assert not _is_valid_ollama_url("ftp://127.0.0.1:11434")  # wrong scheme
    assert not _is_valid_ollama_url("http://127.0.0.1:999999")  # port out of range
    assert not _is_valid_ollama_url("127.0.0.1:11434")  # missing scheme


def test_model_kind_label_distinguishes_embed_from_chat():
    from neo_localmcp.installer.wizard.console import _model_kind_label

    assert _model_kind_label(("completion", "tools")) == "chat"
    assert _model_kind_label(("embedding",)) == "embed"
    assert _model_kind_label(()) == ""


def test_console_baseurl_phase_confirms_default_without_prompting_for_new_value(tmp_path, monkeypatch):
    """Saying 'yes, looks good' to the shown default must not ask for a new URL."""
    from neo_localmcp.installer.wizard.console import ConsoleWizard

    backend = _isolated_preview_backend(tmp_path, monkeypatch)
    wizard = ConsoleWizard(backend, fake=True)
    wizard.state.configure_ollama = True

    replies = iter([""])  # accept "Looks good?" default (Yes)
    monkeypatch.setattr(ConsoleWizard, "_input", lambda self, prompt: next(replies))
    wizard._phase_ollama_baseurl()
    assert wizard.state.ollama_base_url == backend.ollama_info().base_url


def test_console_baseurl_phase_loops_on_invalid_then_accepts_valid(tmp_path, monkeypatch):
    """Saying 'no' prompts for a new URL, re-asks on an invalid one, then loops
    back to the confirm question with the new value until accepted."""
    from neo_localmcp.installer.wizard.console import ConsoleWizard

    backend = _isolated_preview_backend(tmp_path, monkeypatch)
    wizard = ConsoleWizard(backend, fake=True)
    wizard.state.configure_ollama = True

    # 1) "no" to the default  2) an invalid URL (re-prompted)  3) a valid URL
    # 4) "yes" to the new "looks good?" confirmation
    replies = iter(["n", "not-a-url", "http://192.168.1.50:11434", ""])
    monkeypatch.setattr(ConsoleWizard, "_input", lambda self, prompt: next(replies))
    wizard._phase_ollama_baseurl()
    assert wizard.state.ollama_base_url == "http://192.168.1.50:11434"


# --- model table: type awareness + colored current selection ----------------

def test_format_model_table_labels_kind_and_shows_size(tmp_path, monkeypatch):
    from neo_localmcp.installer.wizard.console import ConsoleWizard

    backend = _isolated_preview_backend(tmp_path, monkeypatch)
    wizard = ConsoleWizard(backend, fake=True)
    info = backend.ollama_info()
    rows = wizard._format_model_table(list(info.installed_models), info, current="qwen3:8b")

    embed_row = next(r for r in rows if "nomic-embed-text:latest" in r)
    chat_row = next(r for r in rows if "qwen3-coder:30b" in r)
    assert "embed" in embed_row
    assert "chat" in chat_row
    # size is shown (human-formatted, e.g. "5.2 GB")
    assert any(unit in chat_row for unit in ("GB", "MB"))


def test_format_model_table_colors_the_current_row(tmp_path, monkeypatch):
    from neo_localmcp.installer.wizard import _ansi
    from neo_localmcp.installer.wizard.console import ConsoleWizard

    monkeypatch.setattr(_ansi, "COLOR_ENABLED", True)
    backend = _isolated_preview_backend(tmp_path, monkeypatch)
    wizard = ConsoleWizard(backend, fake=True)
    info = backend.ollama_info()
    rows = wizard._format_model_table(list(info.installed_models), info, current="qwen3:8b")

    current_row = next(r for r in rows if "qwen3:8b" in r and "qwen3-coder" not in r)
    other_row = next(r for r in rows if "qwen3-coder:30b" in r)
    assert "\033[" in current_row  # ANSI escape present -- colored
    assert "\033[" not in other_row  # non-current rows stay plain


def test_pick_model_uses_the_colored_kind_aware_table(tmp_path, monkeypatch):
    """Smoke test: _pick_model still returns the chosen model name, now via the
    table renderer (regression -- picking must still work after the format change)."""
    from neo_localmcp.installer.wizard.console import ConsoleWizard

    backend = _isolated_preview_backend(tmp_path, monkeypatch)
    wizard = ConsoleWizard(backend, fake=True)
    info = backend.ollama_info()
    target_index = info.installed_models.index("qwen3-coder:30b") + 1

    replies = iter([str(target_index)])
    monkeypatch.setattr(ConsoleWizard, "_input", lambda self, prompt: next(replies))
    chosen = wizard._pick_model("fast model", ["hint"], info, current="qwen3:8b")
    assert chosen == "qwen3-coder:30b"


def _custom_ollama_info(**overrides):
    from neo_localmcp.installer.wizard.backend import OllamaInfo
    defaults = dict(
        reachable=True, base_url="http://127.0.0.1:11434",
        installed_models=("bge-m3:latest", "gemma3:12b", "qwen3:8b"),
        fast_model="qwen3:8b", summary_model="qwen3-coder:30b",
        state="ready",
        model_sizes={"bge-m3:latest": "1.1 GB", "gemma3:12b": "7.6 GB", "qwen3:8b": "4.9 GB"},
        model_capabilities={
            "bge-m3:latest": ("embedding",),
            "gemma3:12b": ("completion",),
            "qwen3:8b": ("completion", "tools"),
        },
    )
    defaults.update(overrides)
    return OllamaInfo(**defaults)


def test_pick_model_default_skips_embed_only_model_when_current_not_installed(tmp_path, monkeypatch):
    """Regression: bge-m3:latest sorts alphabetically first but is embed-only.
    When the configured model (qwen3-coder:30b) isn't in the installed list,
    the default choice must not silently fall back to it."""
    from neo_localmcp.installer.wizard.console import ConsoleWizard

    backend = _isolated_preview_backend(tmp_path, monkeypatch)
    wizard = ConsoleWizard(backend, fake=True)
    info = _custom_ollama_info()

    replies = iter([""])  # accept whatever the default is
    monkeypatch.setattr(ConsoleWizard, "_input", lambda self, prompt: next(replies))
    chosen = wizard._pick_model("summary model", ["hint"], info, current="qwen3-coder:30b")
    assert chosen != "bge-m3:latest"
    assert "embedding" not in info.model_capabilities.get(chosen, ())


def test_format_model_table_marks_the_default_row_when_not_current(tmp_path, monkeypatch):
    """When the default selection differs from the persisted 'current' value
    (e.g. current isn't installed), that row must still be visually distinct
    -- not just silently unmarked -- so the user can see what Enter will pick."""
    from neo_localmcp.installer.wizard import _ansi
    from neo_localmcp.installer.wizard.console import ConsoleWizard

    monkeypatch.setattr(_ansi, "COLOR_ENABLED", True)
    backend = _isolated_preview_backend(tmp_path, monkeypatch)
    wizard = ConsoleWizard(backend, fake=True)
    info = _custom_ollama_info()

    rows = wizard._format_model_table(
        list(info.installed_models), info, current="qwen3-coder:30b", default_model="gemma3:12b")
    default_row = next(r for r in rows if "gemma3:12b" in r)
    embed_row = next(r for r in rows if "bge-m3:latest" in r)
    assert "\033[" in default_row  # colored
    assert "default" in default_row.lower()
    assert "\033[" not in embed_row
