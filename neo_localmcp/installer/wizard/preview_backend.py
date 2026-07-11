"""In-memory simulation of :class:`WizardBackend`.

Touches no processes, venvs, network, or files. Every ``run_*`` method sleeps
briefly between simulated steps so the live progress log looks real. Use it to
walk the whole flow safely (``python setup_wizard.py --preview``).

Simulated state persists across runs in ``.wizard_preview/state.json`` at the
repo root (gitignored), so a later ``--preview`` run sees what a previous
simulated install/uninstall would have left behind. If that file doesn't
exist yet, the starting state is seeded from NEO_LOCALMCP_WIZARD_PREVIEW_STATE:
``absent`` (default -- a fresh clone) or ``healthy`` (a returning user who has
already installed and wants to reconfigure).
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
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
    human_size,
)

_SOURCE_VERSION = "1.0.10"

# A believable `ollama list` result so the model-selection screen has real
# choices to offer in the simulation. Sizes are illustrative, in bytes.
_FAKE_MODEL_SIZES_RAW = {
    "qwen3:8b": 5_200_000_000,
    "qwen3-coder:30b": 19_000_000_000,
    "llama3.1:8b": 4_900_000_000,
    "nomic-embed-text:latest": 274_000_000,
    "deepseek-r1:14b": 9_000_000_000,
}
_FAKE_INSTALLED_MODELS = tuple(sorted(_FAKE_MODEL_SIZES_RAW))

_STEP_DELAY = 0.35

# Persisted simulation state, so a later `--preview` run (or mid-session `p`
# toggle) sees what a previous simulated install/uninstall would have left
# behind, instead of always restarting from the same NEO_LOCALMCP_WIZARD_PREVIEW_STATE
# seed. Never touches any real managed root, venv, or client config.
_STATE_DIR = Path(__file__).resolve().parents[3] / ".wizard_preview"
_STATE_PATH = _STATE_DIR / "state.json"

_BLANK_STATE: dict[str, Any] = {
    "installed": False,
    "installed_version": None,
    "registered_clients": [],
    "fast_model": "qwen3:8b",
    "summary_model": "qwen3-coder:30b",
    "embed_model": "",  # optional semantic layer; disabled by default in the simulation
    "base_url": "http://127.0.0.1:11434",
    "prefs": {},
}


def _seed_state() -> dict[str, Any]:
    # NEO_LOCALMCP_WIZARD_PREVIEW_STATE "healthy"/"installed"/"returning" -> seeds an already-installed simulated state; default "absent" -> fresh clone
    start = os.environ.get("NEO_LOCALMCP_WIZARD_PREVIEW_STATE", "absent").strip().lower()
    installed = start in {"healthy", "installed", "returning"}
    state = dict(_BLANK_STATE)
    if installed:
        state["installed"] = True
        state["installed_version"] = _SOURCE_VERSION
        state["registered_clients"] = ["claude-code", "codex"]
        state["prefs"] = {
            "last_clients": list(state["registered_clients"]),
            "fast_model": state["fast_model"],
            "summary_model": state["summary_model"],
        }
    return state


def _load_state() -> dict[str, Any]:
    # persisted state.json present and valid -> merged onto _BLANK_STATE (fills any new fields); else -> freshly seeded and saved
    try:
        with open(_STATE_PATH, encoding="utf-8") as fh:
            loaded = json.load(fh)
        if isinstance(loaded, dict) and "installed" in loaded:
            merged = dict(_BLANK_STATE)
            merged.update(loaded)
            return merged
    except (OSError, ValueError):
        pass
    seeded = _seed_state()
    _save_state(seeded)
    return seeded


def _save_state(state: dict[str, Any]) -> None:
    _STATE_DIR.mkdir(parents=True, exist_ok=True)
    with open(_STATE_PATH, "w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=2, sort_keys=True)
        fh.write("\n")


# All fake paths are derived from the *live* OS (via sys.platform) so the
# simulation shows real-looking, correctly-separated paths for whichever machine
# it runs on: a Mac shows /Users/you/... with forward slashes, Windows shows
# C:\Users\you\... with backslashes, Linux shows /home/you/....
_OS_LABELS = {"windows": "Windows", "macos": "macOS", "linux": "Linux"}


def _fake_os() -> str:
    if sys.platform.startswith("win"):
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return "linux"


def _os_label() -> str:
    return _OS_LABELS[_fake_os()]


def _fake_home() -> str:
    return {"windows": r"C:\Users\you", "macos": "/Users/you", "linux": "/home/you"}[_fake_os()]


def _join(*parts: str) -> str:
    sep = "\\" if _fake_os() == "windows" else "/"
    return _fake_home() + sep + sep.join(parts)


def _fake_root() -> str:
    return _join(".neo-localmcp")


def _fake_client_meta(key: str) -> tuple[str, str, bool]:
    # (path, detail, manual) mirroring what ai_client_config.py really does, on a fake OS-appropriate path
    if key == "claude-code":
        return (_join(".claude", "commands", "neo-localmcp"),
                "Slash commands installed here; the MCP server is registered via "
                "`claude mcp add --scope user` (no file edited directly).",
                False)
    if key == "codex":
        return (_join(".codex", "config.toml"),
                "A marked neo-localmcp block is written into config.toml "
                "(shared by Codex CLI, IDE, and app).",
                False)
    # claude-desktop: a .mcpb package you install manually in-app, NOT a JSON edit.
    return (_join(".neo-localmcp", "neo-localmcp.mcpb"),
            "Manual step: install this .mcpb in Claude Desktop via "
            "Settings > Extensions > Advanced settings. Not written automatically.",
            True)


class PreviewBackend:
    """A fully navigable, side-effect-free WizardBackend."""

    def __init__(self) -> None:
        state = _load_state()
        self._installed = bool(state["installed"])
        self._installed_version: str | None = state["installed_version"]
        self._registered: list[str] = list(state["registered_clients"])
        self._fast_model: str = state["fast_model"]
        self._summary_model: str = state["summary_model"]
        self._embed_model: str = state.get("embed_model", "")
        self._base_url: str = state["base_url"]
        self._prefs: dict[str, Any] = dict(state["prefs"])

    def _persist(self) -> None:
        # writes current in-memory simulated state to disk so the next --preview run continues from here
        _save_state({
            "installed": self._installed,
            "installed_version": self._installed_version,
            "registered_clients": list(self._registered),
            "fast_model": self._fast_model,
            "summary_model": self._summary_model,
            "embed_model": self._embed_model,
            "base_url": self._base_url,
            "prefs": dict(self._prefs),
        })

    # -- read-only probes ------------------------------------------------- #

    def detect(self) -> DetectedInfo:
        if self._installed:
            version = self._installed_version or _SOURCE_VERSION
            state, label = "healthy", f"Installed and healthy (v{version})."
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
        options = []
        for key in CLIENT_KEYS:
            path, detail, manual = _fake_client_meta(key)
            options.append(ClientOption(
                key=key,
                label=CLIENT_LABELS[key],
                config_path=path,
                registered=key in self._registered,
                detail=detail,
                manual=manual,
            ))
        return options

    def ollama_info(self) -> OllamaInfo:
        return OllamaInfo(
            reachable=True,
            base_url=self._base_url,
            installed_models=_FAKE_INSTALLED_MODELS,
            fast_model=self._fast_model,
            summary_model=self._summary_model,
            state="ready",
            detail="Simulated `ollama list`: 5 models installed.",
            model_sizes={name: human_size(size) for name, size in _FAKE_MODEL_SIZES_RAW.items()},
            embed_model=self._embed_model,
        )

    # -- operations ------------------------------------------------------- #

    def run_operation(self, state: WizardState, emit: EmitFn) -> OperationOutcome:
        # uninstall -> _simulate_uninstall; else -> _simulate_install_like (install/reinstall)
        if state.operation == OP_UNINSTALL:
            return self._simulate_uninstall(state, emit)
        return self._simulate_install_like(state, emit)

    def _simulate_install_like(self, state: WizardState, emit: EmitFn) -> OperationOutcome:
        # dry_run -> preview steps only, no state change; else emits each _PLAN step with a delay, then mutates simulated state to match
        op = state.operation
        if state.dry_run:
            emit(StepEvent("info", f"DRY RUN: no changes will be made for '{op}'."))
            for i, step in enumerate(_PLAN.get(op, ()), start=1):
                emit(StepEvent("info", f"  {i}. {step}"))
                time.sleep(_STEP_DELAY / 2)
            return OperationOutcome(
                ok=True, status="succeeded",
                title="Dry run complete - nothing was changed.",
                detail_lines=("This is a simulation (--preview).",),
            )

        steps = _PLAN.get(op, _PLAN[OP_INSTALL])
        for step in steps:
            emit(StepEvent("action", step))
            time.sleep(_STEP_DELAY)

        if op == OP_INSTALL:
            self._installed = True
            self._installed_version = _SOURCE_VERSION
            self._registered = list(state.selected_clients)
        if state.configure_ollama:
            self._fast_model = state.fast_model or self._fast_model
            self._summary_model = state.summary_model or self._summary_model
            self._embed_model = state.embed_model  # "" -> disabled (explicit), a name -> enabled
            emit(StepEvent("action", f"Set Ollama models: fast={self._fast_model}, "
                                     f"summary={self._summary_model}, embed={self._embed_model or 'disabled'}"))

        clients = ", ".join(self._registered) or "none"
        emit(StepEvent("summary", f"{op} succeeded (simulated)"))
        self._persist()
        return OperationOutcome(
            ok=True, status="succeeded",
            title=f"{op.capitalize()} complete (simulated).",
            detail_lines=(
                f"Clients connected: {clients}",
                f"Ollama: fast={self._fast_model}, summary={self._summary_model}, "
                f"embed={self._embed_model or 'disabled'}",
                "This was a simulation (--preview) - nothing on disk changed.",
            ),
            next_command="neo-localmcp doctor",
        )

    def _simulate_uninstall(self, state: WizardState, emit: EmitFn) -> OperationOutcome:
        # emits the uninstall (or full-wipe) plan steps, then resets simulated install state; full_wipe additionally resets Ollama config/prefs
        steps = (
            _PLAN["uninstall-wipe"] if state.full_wipe else _PLAN[OP_UNINSTALL]
        )
        for step in steps:
            emit(StepEvent("action", step))
            time.sleep(_STEP_DELAY)
        self._installed = False
        self._installed_version = None
        self._registered = []
        if state.full_wipe:
            # Mirrors a real full wipe: nothing survives, including Ollama config.
            self._fast_model = _BLANK_STATE["fast_model"]
            self._summary_model = _BLANK_STATE["summary_model"]
            self._embed_model = _BLANK_STATE["embed_model"]
            self._base_url = _BLANK_STATE["base_url"]
            self._prefs = {}
            note = "Full wipe (simulated): all data would be deleted."
        else:
            # Runtime-only: durable data (Ollama config, prefs) survives.
            note = "Runtime removed (simulated); durable data would be preserved."
        emit(StepEvent("summary", "uninstall succeeded (simulated)"))
        self._persist()
        return OperationOutcome(
            ok=True, status="succeeded",
            title="Uninstall complete (simulated).",
            detail_lines=(note, "This was a simulation (--preview)."),
        )

    def apply_ollama_config(self, state: WizardState, emit: EmitFn) -> OperationOutcome:
        emit(StepEvent("action", f"Probing Ollama at {self._base_url} ..."))
        time.sleep(_STEP_DELAY)
        self._fast_model = state.fast_model or self._fast_model
        self._summary_model = state.summary_model or self._summary_model
        self._embed_model = state.embed_model  # "" -> disabled (explicit), a name -> enabled
        emit(StepEvent("action", "Writing config.yaml (simulated)."))
        time.sleep(_STEP_DELAY)
        emit(StepEvent("summary", "Ollama models updated (simulated)"))
        self._persist()
        return OperationOutcome(
            ok=True, status="succeeded",
            title="Ollama models updated (simulated).",
            detail_lines=(
                f"fast_model    = {self._fast_model}",
                f"summary_model = {self._summary_model}",
                f"embed_model   = {self._embed_model or 'disabled'}",
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
        self._persist()
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
        self._persist()


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
