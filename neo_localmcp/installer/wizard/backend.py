"""The seam between the wizard UI and real lifecycle work.

Screens depend only on :class:`WizardBackend` and the plain dataclasses here --
never on ``neo_localmcp.installer`` directly. Two implementations satisfy this
contract: :mod:`neo_localmcp.installer.wizard.preview_backend` (in-memory,
side-effect free, for walking the flow) and
:mod:`neo_localmcp.installer.wizard.live_backend` (drives the actual install
lifecycle). Swapping them is a one-line change in ``app.py``.

This module is stdlib-only on purpose so it stays importable everywhere.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Protocol, runtime_checkable

# Operation identifiers used across the UI and both backends.
OP_INSTALL = "install"
OP_REINSTALL = "reinstall"
OP_UNINSTALL = "uninstall"
OP_CONFIG_OLLAMA = "config-ollama"
OP_MANAGE_CLIENTS = "manage-clients"

# The exact phrase a user must type to authorize a full data wipe. Mirrors
# neo_localmcp.installer.output.FULL_WIPE_CONFIRMATION, duplicated here so the
# stdlib-only UI layer never has to import the psutil-dependent installer.
FULL_WIPE_PHRASE = "DELETE ALL NEO-LOCALMCP DATA"

# Client surface identifiers (match neo_localmcp.installer / client_setup keys).
CLIENT_KEYS = ("claude-code", "codex", "claude-desktop")
CLIENT_LABELS = {
    "claude-code": "Claude Code",
    "codex": "Codex (CLI / IDE)",
    "claude-desktop": "Claude Desktop",
}


@dataclass(frozen=True)
class DetectedInfo:
    # fast, network-free snapshot of the current machine + install state
    os_label: str  # "Windows" / "macOS" / "this platform"
    python_version: str
    state: str  # raw InstallStateKind value: absent / data-only / healthy / broken-runtime / ...
    state_label: str  # human-friendly one-liner
    installed_version: str | None
    source_version: str
    managed_root: str
    registered_clients: tuple[str, ...] = ()

    @property
    def is_installed(self) -> bool:
        return self.state in {"healthy", "broken-runtime", "partial-operation", "legacy-layout"}

    @property
    def is_broken(self) -> bool:
        return self.state in {"broken-runtime", "partial-operation"}


@dataclass(frozen=True)
class ClientOption:
    # one connectable client surface: config_path is the OS-specific location touched, detail says what that path is (slash-commands dir / config.toml block / .mcpb package)
    # manual=True marks a surface the wizard can't fully automate (Claude Desktop needs a manual .mcpb install in-app)
    key: str
    label: str
    config_path: str
    registered: bool
    detail: str = ""
    manual: bool = False


@dataclass(frozen=True)
class OllamaInfo:
    # result of probing Ollama (the `ollama list` equivalent) plus current config
    # installed_models always alphabetically sorted; model_sizes maps name -> human size ("4.9 GB"), missing entry means size wasn't available
    reachable: bool
    base_url: str
    installed_models: tuple[str, ...]
    fast_model: str
    summary_model: str
    state: str  # ready / model_cold / model_missing / unreachable / disabled / ...
    detail: str = ""
    model_sizes: dict[str, str] = field(default_factory=dict)


def human_size(num_bytes: float) -> str:
    # bytes -> short human-readable size string, e.g. "4.9 GB"
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{int(size)} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


@dataclass(frozen=True)
class StepEvent:
    # one streamed line of progress from a running operation
    level: str  # info / action / warning / error / summary
    message: str


@dataclass(frozen=True)
class OperationOutcome:
    # terminal result of an operation, for the result panel
    ok: bool
    status: str  # succeeded / failed / cancelled
    title: str
    detail_lines: tuple[str, ...] = ()
    log_hint: str | None = None
    next_command: str | None = None


@dataclass
class WizardState:
    # mutable choices accumulated as the user moves through the wizard; executes nothing itself, handed to the backend once at confirmation
    operation: str = ""
    selected_clients: list[str] = field(default_factory=list)
    configure_ollama: bool = False
    ollama_base_url: str = ""
    fast_model: str = ""
    summary_model: str = ""
    full_wipe: bool = False
    dry_run: bool = False
    outcome: OperationOutcome | None = None

    def reset_operation(self) -> None:
        self.operation = ""
        self.selected_clients = []
        self.configure_ollama = False
        self.full_wipe = False
        self.dry_run = False
        self.outcome = None


EmitFn = Callable[[StepEvent], None]


@runtime_checkable
class WizardBackend(Protocol):
    # everything a screen can ask the outside world to do

    def detect(self) -> DetectedInfo:
        # fast, network-free state snapshot; safe to call on every screen resume
        ...

    def client_options(self) -> list[ClientOption]:
        # client surfaces + their OS-specific config paths and registration state
        ...

    def ollama_info(self) -> OllamaInfo:
        # probes Ollama for its installed models (the `ollama list` equivalent)
        ...

    def run_operation(self, state: WizardState, emit: EmitFn) -> OperationOutcome:
        # runs install/reinstall/uninstall synchronously, streaming StepEvents to emit; caller invokes from a worker thread to keep the UI responsive
        # never raises for an expected failure -- returns OperationOutcome(ok=False) instead
        ...

    def apply_ollama_config(self, state: WizardState, emit: EmitFn) -> OperationOutcome:
        # persists the chosen Ollama base URL + models; no runtime rebuild
        ...

    def apply_client_changes(self, state: WizardState, emit: EmitFn) -> OperationOutcome:
        # registers/deregisters client surfaces to match state.selected_clients
        ...

    def load_prefs(self) -> dict[str, Any]:
        # remembered wizard UX choices (last clients, last models); never authoritative
        ...

    def save_prefs(self, prefs: dict[str, Any]) -> None:
        ...
