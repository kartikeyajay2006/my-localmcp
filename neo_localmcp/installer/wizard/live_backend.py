"""The real :class:`WizardBackend`: drives the actual install lifecycle.

Every side effect here is delegated to code ``setup.py`` already uses -- the
``neo_localmcp.installer`` operations, ``neo_localmcp.config``,
``neo_localmcp.ollama_client``, and ``neo_localmcp.ai_client_config``. This module
adds no path, deletion, or process policy of its own; it adapts those existing
primitives to the wizard's :class:`WizardBackend` contract and streams their
Reporter output back to the UI as :class:`StepEvent` lines.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any

import neo_localmcp
from ... import ai_client_config as client_setup, config, ollama_client
from .. import (
    ManagedPaths,
    Operation,
    OperationContext,
    OperationStatus,
    Reporter,
    confirm_full_wipe,
    configure_models,
    detect_state,
    install,
    reinstall,
    uninstall,
)
from .. import clients as clients_mod
from ..mcpb import build_mcpb
from .backend import (
    CLIENT_KEYS,
    CLIENT_LABELS,
    OP_UNINSTALL,
    ClientOption,
    DetectedInfo,
    EmitFn,
    human_size,
    OllamaInfo,
    OperationOutcome,
    StepEvent,
    WizardState,
)

_PREFIX_LEVELS = {
    "INFO": "info",
    "WARNING": "warning",
    "ERROR": "error",
    "ACTION": "action",
    "SUMMARY": "summary",
}

_STATE_LABELS = {
    "absent": "Not installed yet on this machine.",
    "data-only": "Data present, but no runtime - a previous uninstall kept your memory.",
    "healthy": "Installed and healthy.",
    "broken-runtime": "Installed but the runtime looks broken - a reinstall will repair it.",
    "partial-operation": "A previous setup did not finish - a reinstall will recover it.",
    "legacy-layout": "An older layout was detected - installing will migrate it.",
}


def _os_label(paths: ManagedPaths) -> str:
    if paths.platform == "windows":
        return "Windows"
    return "macOS" if sys.platform == "darwin" else "this platform"


def _reporter_forwarding_to(emit: EmitFn) -> Reporter:
    # real Reporter whose printed "LEVEL: message" lines also stream to the wizard UI as StepEvents
    def output_fn(line: str) -> None:
        level, _, rest = line.partition(": ")
        if level in _PREFIX_LEVELS and rest:
            emit(StepEvent(_PREFIX_LEVELS[level], rest))
        else:
            emit(StepEvent("info", line))

    return Reporter(output_fn=output_fn)


class LiveBackend:
    """Drives real install/reinstall/uninstall + config against the managed root."""

    def __init__(self) -> None:
        self._paths = ManagedPaths.from_environment()
        self._source_root = Path(neo_localmcp.__file__).resolve().parent.parent
        self._source_version = neo_localmcp.__version__

    # -- read-only probes ------------------------------------------------- #

    def detect(self) -> DetectedInfo:
        # detect_state() + registered clients -> the wizard's DetectedInfo snapshot
        state = detect_state(self._paths)
        kind = state.kind.value
        registered = self._registered_clients()
        installed_version = (
            state.details.get("runtime_version")
            or state.details.get("installed_version")
            or (self._source_version if kind == "healthy" else None)
        )
        return DetectedInfo(
            os_label=_os_label(self._paths),
            python_version="%d.%d.%d" % sys.version_info[:3],
            state=kind,
            state_label=_STATE_LABELS.get(kind, kind),
            installed_version=installed_version,
            source_version=self._source_version,
            managed_root=str(self._paths.root),
            registered_clients=tuple(registered),
        )

    def _registered_clients(self) -> list[str]:
        try:
            records = clients_mod.read_registrations(self._paths)
        except Exception:  # noqa: BLE001 - a broken record file must not break the UI
            return []
        return [r.client for r in records if r.client in CLIENT_KEYS]

    def client_options(self) -> list[ClientOption]:
        # each known client key -> its OS-specific config path/detail + whether it's currently registered
        registered = set(self._registered_clients())
        try:
            status = client_setup.client_status(server_command=self._paths.server_executable)
            paths = status.get("paths", {})
        except Exception:  # noqa: BLE001
            paths = {}
        claude_code = paths.get("claude_code_commands", {}).get("path", "")
        codex = paths.get("codex_cli_config", {}).get("path", "")
        mcpb = str(self._paths.root / "neo-localmcp.mcpb")
        rows = {
            "claude-code": (
                claude_code,
                "Slash commands installed here; the MCP server is registered via "
                "`claude mcp add --scope user` (no file edited directly).",
                False,
            ),
            "codex": (
                codex,
                "A marked neo-localmcp block is written into config.toml "
                "(shared by Codex CLI, IDE, and app).",
                False,
            ),
            "claude-desktop": (
                mcpb,
                "Manual step: install this .mcpb in Claude Desktop via "
                "Settings > Extensions > Advanced settings. Not written automatically.",
                True,
            ),
        }
        return [
            ClientOption(
                key=key,
                label=CLIENT_LABELS[key],
                config_path=rows[key][0] or "(path unavailable)",
                registered=key in registered,
                detail=rows[key][1],
                manual=rows[key][2],
            )
            for key in CLIENT_KEYS
        ]

    def ollama_info(self) -> OllamaInfo:
        # config values + a live status probe -> reachability, installed models, and human-readable sizes
        cfg = config.load_config().get("ollama", {})
        base_url = str(cfg.get("base_url", "http://127.0.0.1:11434"))
        fast = str(cfg.get("fast_model", ""))
        summary = str(cfg.get("summary_model", ""))
        try:
            probe = ollama_client.status()
        except Exception as exc:  # noqa: BLE001
            return OllamaInfo(
                reachable=False, base_url=base_url, installed_models=(),
                fast_model=fast, summary_model=summary, state="unreachable",
                detail=f"Could not probe Ollama: {exc}",
            )
        state = str(probe.get("state", "unreachable"))
        reachable = state not in {"unreachable", "timed_out", "disabled", "failed"}
        installed = sorted({str(m) for m in probe.get("installed_models", []) if m})
        sizes = self._model_sizes() if reachable and installed else {}
        if reachable and installed:
            detail = f"`ollama list`: {len(installed)} model(s) installed at {base_url}."
        elif reachable:
            detail = f"Ollama is reachable at {base_url} but reports no installed models."
        else:
            detail = (f"Ollama not reachable at {base_url} "
                      f"({probe.get('error') or state}). neo-localmcp works without it.")
        return OllamaInfo(
            reachable=reachable, base_url=base_url, installed_models=tuple(installed),
            fast_model=fast, summary_model=summary, state=state, detail=detail,
            model_sizes=sizes,
        )

    @staticmethod
    def _model_sizes() -> dict[str, str]:
        # ollama_client.model_sizes()'s raw bytes -> human-formatted strings for display; formatting is a wizard-presentation concern, kept here
        return {name: human_size(size) for name, size in ollama_client.model_sizes().items()}

    # -- operations ------------------------------------------------------- #

    def _build_context(self, emit: EmitFn) -> OperationContext:
        return OperationContext(
            paths=self._paths,
            source_root=self._source_root,
            python_executable=Path(sys.executable),
            reporter=_reporter_forwarding_to(emit),
            source_version=self._source_version,
            process_provider=None,
            clock=time.time,
            confirm=confirm_full_wipe,
        )

    def run_operation(self, state: WizardState, emit: EmitFn) -> OperationOutcome:
        # dry_run -> preview only; else dispatch to install/reinstall/uninstall, then optionally write Ollama config + rebuild the desktop bundle on success
        if state.dry_run:
            return self._dry_run(state, emit)
        try:
            context = self._build_context(emit)
            if state.operation == OP_UNINSTALL:
                context.selected_clients = []
                result = uninstall(context, delete_memory=state.full_wipe, assume_yes=True)
            elif state.operation == "reinstall":
                result = reinstall(context)
            else:  # install
                context.selected_clients = list(state.selected_clients)
                result = install(context, clean=False)
        except Exception as exc:  # noqa: BLE001 - never crash the UI on a lifecycle error
            emit(StepEvent("error", f"Operation raised: {exc}"))
            return OperationOutcome(
                ok=False, status="failed",
                title=f"{state.operation.capitalize()} failed.",
                detail_lines=(str(exc),),
                log_hint=str(self._paths.logs),
            )

        if (result.status is OperationStatus.SUCCEEDED
                and state.configure_ollama and state.operation != OP_UNINSTALL):
            self._write_ollama_config(state, emit)

        # dev-only rebuild of the versioned .mcpb from a source checkout; never an uninstall concern
        extra_details: tuple[str, ...] = ()
        if (result.status is OperationStatus.SUCCEEDED
                and state.operation != OP_UNINSTALL):
            built = self._build_desktop_bundle(emit)
            if built:
                extra_details = (f"Claude Desktop bundle: {built}",)

        return self._outcome_from_result(state, result, extra_details=extra_details)

    def _build_desktop_bundle(self, emit: EmitFn) -> str | None:
        # packs the versioned .mcpb into packages/claude-desktop/ if run from a source checkout; dev-only, no staging inputs -> None
        # a build failure degrades to a warning, never fails an otherwise-successful install
        try:
            written = build_mcpb(self._source_root, self._source_version)
        except Exception as exc:  # noqa: BLE001 - a build hiccup must not fail the install
            emit(StepEvent("warning",
                           f"Could not build the Claude Desktop .mcpb bundle: {exc}"))
            return None
        if written is None:
            return None
        emit(StepEvent("action", f"Built Claude Desktop bundle: {written}"))
        return str(written)

    def _dry_run(self, state: WizardState, emit: EmitFn) -> OperationOutcome:
        # previews the ordered action plan via cli.dry_run_plan + current detect_state; makes no changes
        from .. import cli as installer_cli  # public dry_run_plan() lives here; same repo

        key, plan = installer_cli.dry_run_plan(
            state.operation,
            clean=False,
            delete_memory=(state.operation == OP_UNINSTALL and state.full_wipe),
        )
        st = detect_state(self._paths)
        emit(StepEvent("info", f"DRY RUN: no changes will be made for '{key}'."))
        emit(StepEvent("info", f"Managed root: {self._paths.root}"))
        emit(StepEvent("info", f"Detected state: {st.kind.value}"))
        for i, step in enumerate(plan, start=1):
            emit(StepEvent("info", f"  {i}. {step}"))
        return OperationOutcome(
            ok=True, status="succeeded",
            title="Dry run complete - nothing was changed.",
            detail_lines=(f"Planned operation: {key}",),
        )

    def _outcome_from_result(
        self, state: WizardState, result: Any, *, extra_details: tuple[str, ...] = ()
    ) -> OperationOutcome:
        # OperationResult -> wizard-facing OperationOutcome, branching on cancelled/succeeded/failed
        ok = result.status is OperationStatus.SUCCEEDED
        status = result.status.value
        op = result.operation.value
        if result.status is OperationStatus.CANCELLED:
            return OperationOutcome(
                ok=False, status=status,
                title=f"{op.capitalize()} cancelled - nothing was changed.",
            )
        if ok:
            details = [f"Actions: {', '.join(result.actions) or 'none'}"]
            if result.warnings:
                details.append(f"Warnings: {len(result.warnings)}")
                details.extend(f"  - {w}" for w in result.warnings)
            if state.configure_ollama and op != OP_UNINSTALL:
                details.append(
                    f"Ollama: fast={state.fast_model}, summary={state.summary_model}")
            details.extend(extra_details)
            return OperationOutcome(
                ok=True, status=status, title=f"{op.capitalize()} succeeded.",
                detail_lines=tuple(details), next_command="neo-localmcp doctor",
            )
        details = list(result.warnings) or ["See the log for details."]
        return OperationOutcome(
            ok=False, status=status, title=f"{op.capitalize()} failed.",
            detail_lines=tuple(details), log_hint=str(self._paths.logs),
        )

    # -- config-only paths ------------------------------------------------ #

    def _write_ollama_config(self, state: WizardState, emit: EmitFn) -> None:
        ollama_cfg = configure_models(
            base_url=state.ollama_base_url or None,
            fast_model=state.fast_model or None,
            summary_model=state.summary_model or None,
        )
        emit(StepEvent("action",
                       f"Saved Ollama config: fast={ollama_cfg.get('fast_model')}, "
                       f"summary={ollama_cfg.get('summary_model')}"))

    def apply_ollama_config(self, state: WizardState, emit: EmitFn) -> OperationOutcome:
        try:
            self._write_ollama_config(state, emit)
        except Exception as exc:  # noqa: BLE001
            emit(StepEvent("error", f"Could not write config: {exc}"))
            return OperationOutcome(
                ok=False, status="failed", title="Failed to update Ollama config.",
                detail_lines=(str(exc),),
            )
        cfg = config.load_config().get("ollama", {})
        return OperationOutcome(
            ok=True, status="succeeded", title="Ollama models updated.",
            detail_lines=(
                f"fast_model    = {cfg.get('fast_model')}",
                f"summary_model = {cfg.get('summary_model')}",
                f"base_url      = {cfg.get('base_url')}",
                f"Written to {config.config_path()}",
            ),
            next_command="neo-localmcp ollama status",
        )

    def apply_client_changes(self, state: WizardState, emit: EmitFn) -> OperationOutcome:
        outcome = clients_mod.apply_client_selection(
            self._paths,
            state.selected_clients,
            server_command=self._paths.server_executable,
            on_event=lambda level, message: emit(StepEvent(level, message)),
            label_fn=lambda client: CLIENT_LABELS.get(client, client),
        )
        if not outcome.ok:
            return OperationOutcome(
                ok=False, status="failed", title="Some client changes failed.",
                detail_lines=tuple(outcome.failures) + outcome.manual,
            )
        details = [f"Connected: {', '.join(outcome.connected) or 'none'}"]
        details.extend(outcome.manual)
        return OperationOutcome(
            ok=True, status="succeeded", title="Client connections updated.",
            detail_lines=tuple(details),
        )

    # -- prefs ------------------------------------------------------------ #

    def _prefs_path(self) -> Path:
        return self._paths.config / "wizard-prefs.json"

    def load_prefs(self) -> dict[str, Any]:
        path = self._prefs_path()
        try:
            if path.exists():
                return json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return {}
        return {}

    def save_prefs(self, prefs: dict[str, Any]) -> None:
        path = self._prefs_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(prefs, indent=2), encoding="utf-8")
        except Exception:  # noqa: BLE001 - prefs are a convenience, never fatal
            pass
