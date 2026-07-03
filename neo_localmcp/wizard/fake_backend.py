"""In-memory simulation of :class:`WizardBackend`.

Touches no processes, venvs, network, or files. Every ``run_*`` method sleeps
briefly between simulated steps so the live progress log looks real. Use it to
walk the whole flow safely (``python setup_wizard.py --fake``).

The simulated starting state is controlled by NEO_LOCALMCP_WIZARD_FAKE_STATE:
``absent`` (default -- a fresh clone) or ``healthy`` (a returning user who has
already installed and wants to reconfigure).
"""

from __future__ import annotations

import os
import platform
import sys
import time
from typing import Any

from .backend import (
    CLIENT_KEYS,
    CLIENT_LABELS,
    OP_CONFIG_OLLAMA,
    OP_INSTALL,
    OP_MANAGE_CLIENTS,
    OP_REINSTALL,
    OP_UNINSTALL,
    ClientOption,
    DetectedInfo,
    EmitFn,
    OllamaInfo,
    OperationOutcome,
    StepEvent,
    WizardState,
)

_SOURCE_VERSION = "1.0.10"

# A believable `ollama list` result so the model-selection screen has real
# choices to offer in the simulation.
_FAKE_INSTALLED_MODELS = (
    "qwen3:8b",
    "qwen3-coder:30b",
    "llama3.1:8b",
    "nomic-embed-text:latest",
    "deepseek-r1:14b",
)

_STEP_DELAY = 0.35


def _os_label() -> str:
    if sys.platform == "darwin":
        return "macOS"
    if os.name == "nt":
        return "Windows"
    return platform.system() or "this platform"


def _fake_root() -> str:
    if os.name == "nt":
        return r"C:\Users\you\.neo-localmcp"
    return "/Users/you/.neo-localmcp" if sys.platform == "darwin" else "~/.neo-localmcp"


def _fake_client_path(key: str) -> str:
    windows = os.name == "nt"
    if key == "claude-code":
        return (r"C:\Users\you\.claude\commands\neo-localmcp"
                if windows else "~/.claude/commands/neo-localmcp")
    if key == "codex":
        return r"C:\Users\you\.codex\config.toml" if windows else "~/.codex/config.toml"
    # claude-desktop
    return (r"C:\Users\you\AppData\Roaming\Claude\claude_desktop_config.json"
            if windows else "~/Library/Application Support/Claude/claude_desktop_config.json")


class FakeBackend:
    """A fully navigable, side-effect-free WizardBackend."""

    def __init__(self) -> None:
        start = os.environ.get("NEO_LOCALMCP_WIZARD_FAKE_STATE", "absent").strip().lower()
        self._installed = start in {"healthy", "installed", "returning"}
        # Simulated persisted state.
        self._registered = ["claude-code", "codex"] if self._installed else []
        self._fast_model = "qwen3:8b"
        self._summary_model = "qwen3-coder:30b"
        self._base_url = "http://127.0.0.1:11434"
        self._prefs: dict[str, Any] = (
            {"last_clients": list(self._registered),
             "fast_model": self._fast_model,
             "summary_model": self._summary_model}
            if self._installed else {}
        )

    # -- read-only probes ------------------------------------------------- #

    def detect(self) -> DetectedInfo:
        if self._installed:
            state, label = "healthy", f"Installed and healthy (v{_SOURCE_VERSION})."
            version: str | None = _SOURCE_VERSION
        else:
            state, label = "absent", "Not installed yet on this machine."
            version = None
        return DetectedInfo(
            os_label=_os_label(),
            python_version="%d.%d.%d" % sys.version_info[:3],
            state=state,
            state_label=label,
            installed_version=version,
            source_version=_SOURCE_VERSION,
            managed_root=_fake_root(),
            registered_clients=tuple(self._registered),
        )

    def client_options(self) -> list[ClientOption]:
        return [
            ClientOption(
                key=key,
                label=CLIENT_LABELS[key],
                config_path=_fake_client_path(key),
                registered=key in self._registered,
            )
            for key in CLIENT_KEYS
        ]

    def ollama_info(self) -> OllamaInfo:
        return OllamaInfo(
            reachable=True,
            base_url=self._base_url,
            installed_models=_FAKE_INSTALLED_MODELS,
            fast_model=self._fast_model,
            summary_model=self._summary_model,
            state="ready",
            detail="Simulated `ollama list`: 5 models installed.",
        )

    # -- operations ------------------------------------------------------- #

    def run_operation(self, state: WizardState, emit: EmitFn) -> OperationOutcome:
        if state.operation == OP_UNINSTALL:
            return self._simulate_uninstall(state, emit)
        return self._simulate_install_like(state, emit)

    def _simulate_install_like(self, state: WizardState, emit: EmitFn) -> OperationOutcome:
        op = state.operation
        if state.dry_run:
            emit(StepEvent("info", f"DRY RUN: no changes will be made for '{op}'."))
            for i, step in enumerate(_PLAN.get(op, ()), start=1):
                emit(StepEvent("info", f"  {i}. {step}"))
                time.sleep(_STEP_DELAY / 2)
            return OperationOutcome(
                ok=True, status="succeeded",
                title="Dry run complete — nothing was changed.",
                detail_lines=("This is a simulation (--fake).",),
            )

        steps = _PLAN.get(op, _PLAN[OP_INSTALL])
        for step in steps:
            emit(StepEvent("action", step))
            time.sleep(_STEP_DELAY)

        if op == OP_INSTALL:
            self._installed = True
            self._registered = list(state.selected_clients)
        if state.configure_ollama:
            self._fast_model = state.fast_model or self._fast_model
            self._summary_model = state.summary_model or self._summary_model
            emit(StepEvent("action", f"Set Ollama models: fast={self._fast_model}, "
                                     f"summary={self._summary_model}"))

        clients = ", ".join(self._registered) or "none"
        emit(StepEvent("summary", f"{op} succeeded (simulated)"))
        return OperationOutcome(
            ok=True, status="succeeded",
            title=f"{op.capitalize()} complete (simulated).",
            detail_lines=(
                f"Clients connected: {clients}",
                f"Ollama: fast={self._fast_model}, summary={self._summary_model}",
                "This was a simulation (--fake) — nothing on disk changed.",
            ),
            next_command="neo-localmcp doctor",
        )

    def _simulate_uninstall(self, state: WizardState, emit: EmitFn) -> OperationOutcome:
        steps = (
            _PLAN["uninstall-wipe"] if state.full_wipe else _PLAN[OP_UNINSTALL]
        )
        for step in steps:
            emit(StepEvent("action", step))
            time.sleep(_STEP_DELAY)
        self._installed = False
        self._registered = []
        note = ("Full wipe (simulated): all data would be deleted."
                if state.full_wipe else
                "Runtime removed (simulated); durable data would be preserved.")
        emit(StepEvent("summary", "uninstall succeeded (simulated)"))
        return OperationOutcome(
            ok=True, status="succeeded",
            title="Uninstall complete (simulated).",
            detail_lines=(note, "This was a simulation (--fake)."),
        )

    def apply_ollama_config(self, state: WizardState, emit: EmitFn) -> OperationOutcome:
        emit(StepEvent("action", f"Probing Ollama at {self._base_url} ..."))
        time.sleep(_STEP_DELAY)
        self._fast_model = state.fast_model or self._fast_model
        self._summary_model = state.summary_model or self._summary_model
        emit(StepEvent("action", "Writing config.yaml (simulated)."))
        time.sleep(_STEP_DELAY)
        emit(StepEvent("summary", "Ollama models updated (simulated)"))
        return OperationOutcome(
            ok=True, status="succeeded",
            title="Ollama models updated (simulated).",
            detail_lines=(
                f"fast_model    = {self._fast_model}",
                f"summary_model = {self._summary_model}",
                f"base_url      = {self._base_url}",
            ),
            next_command="neo-localmcp ollama status",
        )

    def apply_client_changes(self, state: WizardState, emit: EmitFn) -> OperationOutcome:
        target = list(state.selected_clients)
        added = [c for c in target if c not in self._registered]
        removed = [c for c in self._registered if c not in target]
        for c in added:
            emit(StepEvent("action", f"Connecting {CLIENT_LABELS.get(c, c)} (simulated)."))
            time.sleep(_STEP_DELAY)
        for c in removed:
            emit(StepEvent("action", f"Disconnecting {CLIENT_LABELS.get(c, c)} (simulated)."))
            time.sleep(_STEP_DELAY)
        if not added and not removed:
            emit(StepEvent("info", "No client changes to apply."))
        self._registered = target
        emit(StepEvent("summary", "client changes applied (simulated)"))
        return OperationOutcome(
            ok=True, status="succeeded",
            title="Client connections updated (simulated).",
            detail_lines=(f"Connected: {', '.join(self._registered) or 'none'}",),
        )

    # -- prefs ------------------------------------------------------------ #

    def load_prefs(self) -> dict[str, Any]:
        return dict(self._prefs)

    def save_prefs(self, prefs: dict[str, Any]) -> None:
        self._prefs = dict(prefs)


# Ordered, human-readable action plans (mirror setup_cli._DRY_RUN_PLANS in spirit).
_PLAN: dict[str, tuple[str, ...]] = {
    OP_INSTALL: (
        "Validating source checkout and interpreter",
        "Detecting current install state",
        "Stopping any owned processes",
        "Recording selected client registrations",
        "Building a candidate runtime venv",
        "Promoting the candidate (atomic; rolls back on failure)",
        "Reconnecting client registrations",
        "Verifying the installation (CLI, MCP handshake, doctor)",
    ),
    OP_REINSTALL: (
        "Validating source checkout and interpreter",
        "Detecting current install state",
        "Stopping any owned processes",
        "Snapshotting current client registrations",
        "Building a candidate runtime venv",
        "Promoting the candidate (atomic; rolls back on failure)",
        "Reconnecting client registrations",
        "Verifying the installation",
    ),
    OP_UNINSTALL: (
        "Detecting current install state",
        "Stopping any owned processes",
        "Removing active client registrations",
        "Removing the managed runtime (venv only; data preserved)",
    ),
    "uninstall-wipe": (
        "Detecting current install state",
        "Stopping any owned processes",
        "Removing active client registrations",
        "Deleting the entire managed root (all data)",
    ),
}
