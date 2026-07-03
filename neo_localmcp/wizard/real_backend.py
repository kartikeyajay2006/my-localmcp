"""The real :class:`WizardBackend`: drives the actual install lifecycle.

Every side effect here is delegated to code ``setup.py`` already uses -- the
``neo_localmcp.installer`` operations, ``neo_localmcp.config``,
``neo_localmcp.ollama_client``, and ``neo_localmcp.client_setup``. This module
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
from .. import client_setup, config, ollama_client
from ..installer import (
    ManagedPaths,
    Operation,
    OperationContext,
    OperationStatus,
    Reporter,
    confirm_full_wipe,
    detect_state,
    install,
    reinstall,
    uninstall,
)
from ..installer import clients as clients_mod
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
    """A real Reporter whose printed line is also streamed to the UI."""

    def output_fn(line: str) -> None:
        level, _, rest = line.partition(": ")
        if level in _PREFIX_LEVELS and rest:
            emit(StepEvent(_PREFIX_LEVELS[level], rest))
        else:
            emit(StepEvent("info", line))

    return Reporter(output_fn=output_fn)


class RealBackend:
    """Drives real install/reinstall/uninstall + config against the managed root."""

    def __init__(self) -> None:
        self._paths = ManagedPaths.from_environment()
        self._source_root = Path(neo_localmcp.__file__).resolve().parent.parent
        self._source_version = neo_localmcp.__version__

    # -- read-only probes ------------------------------------------------- #

    def detect(self) -> DetectedInfo:
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
        sizes = self._model_sizes(base_url) if reachable and installed else {}
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
    def _model_sizes(base_url: str) -> dict[str, str]:
        """Best-effort per-model size from `ollama list`'s underlying /api/tags.

        Never raises -- an empty result just means sizes aren't shown, the
        model names themselves already came back fine from ``status()``.
        """
        try:
            code, tags = ollama_client._request_json("/api/tags", timeout=5)
            if code != 200:
                return {}
            sizes: dict[str, str] = {}
            for item in tags.get("models", []):
                name = str(item.get("name") or item.get("model") or "")
                size = item.get("size")
                if name and isinstance(size, (int, float)):
                    sizes[name] = human_size(size)
            return sizes
        except Exception:  # noqa: BLE001 - size display is a nice-to-have, never fatal
            return {}

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

        # Optional Ollama config on top of a successful install/reinstall.
        if (result.status is OperationStatus.SUCCEEDED
                and state.configure_ollama and state.operation != OP_UNINSTALL):
            self._write_ollama_config(state, emit)

        return self._outcome_from_result(state, result)

    def _dry_run(self, state: WizardState, emit: EmitFn) -> OperationOutcome:
        from .. import setup_cli  # private plan tables live here; same repo

        key = setup_cli._plan_key(
            state.operation,
            clean=False,
            delete_memory=(state.operation == OP_UNINSTALL and state.full_wipe),
        )
        st = detect_state(self._paths)
        emit(StepEvent("info", f"DRY RUN: no changes will be made for '{key}'."))
        emit(StepEvent("info", f"Managed root: {self._paths.root}"))
        emit(StepEvent("info", f"Detected state: {st.kind.value}"))
        for i, step in enumerate(setup_cli._DRY_RUN_PLANS.get(key, ()), start=1):
            emit(StepEvent("info", f"  {i}. {step}"))
        return OperationOutcome(
            ok=True, status="succeeded",
            title="Dry run complete - nothing was changed.",
            detail_lines=(f"Planned operation: {key}",),
        )

    def _outcome_from_result(self, state: WizardState, result: Any) -> OperationOutcome:
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
        cfg = config.load_config()
        ollama_cfg = cfg.setdefault("ollama", {})
        if state.ollama_base_url:
            ollama_cfg["base_url"] = state.ollama_base_url
        if state.fast_model:
            ollama_cfg["fast_model"] = state.fast_model
        if state.summary_model:
            ollama_cfg["summary_model"] = state.summary_model
        config.save_config(cfg)
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
        current = set(self._registered_clients())
        target = list(state.selected_clients)
        add = [c for c in target if c not in current]
        remove = [c for c in current if c not in target]
        failures: list[str] = []
        manual: list[str] = []
        for c in add:
            emit(StepEvent("action", f"Connecting {CLIENT_LABELS.get(c, c)} ..."))
            try:
                res = client_setup.setup_client(
                    c, apply=True, server_command=self._paths.server_executable)
                if isinstance(res, dict) and res.get("manual_install_required"):
                    note = str(res.get("instructions") or "Manual install required.")
                    emit(StepEvent("warning", f"  {note}"))
                    manual.append(f"{CLIENT_LABELS.get(c, c)}: {note}")
            except Exception as exc:  # noqa: BLE001
                failures.append(f"{c}: {exc}")
                emit(StepEvent("error", f"  failed: {exc}"))
        for c in remove:
            emit(StepEvent("action", f"Disconnecting {CLIENT_LABELS.get(c, c)} ..."))
            try:
                client_setup.remove_client(c, apply=True)
            except Exception as exc:  # noqa: BLE001
                failures.append(f"{c}: {exc}")
                emit(StepEvent("error", f"  failed: {exc}"))
        if not add and not remove:
            emit(StepEvent("info", "No client changes to apply."))
        try:
            clients_mod.record_selection(self._paths, target)
        except Exception as exc:  # noqa: BLE001
            emit(StepEvent("warning", f"Could not update registration record: {exc}"))
        if failures:
            return OperationOutcome(
                ok=False, status="failed", title="Some client changes failed.",
                detail_lines=tuple(failures + manual),
            )
        details = [f"Connected: {', '.join(target) or 'none'}"]
        details.extend(manual)
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
