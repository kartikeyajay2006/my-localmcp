"""A simple, full-screen, numbered terminal wizard.

No TUI framework: it clears the screen, prints a running summary of your answers
so far, asks one numbered question at a time, and reads a number. It drives the
same :class:`WizardBackend` the rest of the package uses, so install / reinstall
/ uninstall / Ollama-config / client-management all go through the exact same
lifecycle code ``setup.py`` uses.
"""

from __future__ import annotations

import os
import sys

from .backend import (
    CLIENT_LABELS,
    FULL_WIPE_PHRASE,
    OP_CONFIG_OLLAMA,
    OP_INSTALL,
    OP_MANAGE_CLIENTS,
    OP_REINSTALL,
    OP_UNINSTALL,
    WizardBackend,
    WizardState,
)

_WIDTH = 64


def _clear() -> None:
    os.system("cls" if os.name == "nt" else "clear")


class ConsoleWizard:
    def __init__(self, backend: WizardBackend, fake: bool) -> None:
        self.backend = backend
        self.fake = fake
        self.detected = backend.detect()
        self.prefs = backend.load_prefs()
        self.state = WizardState()
        self.summary: list[tuple[str, str]] = []

    # -- rendering -------------------------------------------------------- #

    def _header(self, question: str = "") -> None:
        _clear()
        d = self.detected
        print("=" * _WIDTH)
        title = " neo-localmcp setup wizard"
        if self.fake:
            title += "   (SIMULATION - nothing changes)"
        print(title)
        print("=" * _WIDTH)
        print(f" {d.os_label} | Python {d.python_version}")
        print(f" {d.state_label}")
        if d.registered_clients:
            print(f" Connected clients: {', '.join(d.registered_clients)}")
        if self.summary:
            print()
            print(" Your choices so far:")
            for label, value in self.summary:
                print(f"   {label}: {value}")
        print("-" * _WIDTH)
        if question:
            print(f" {question}")
            print()

    @staticmethod
    def _print_options(rows: list[tuple[str, str]]) -> None:
        for index, (title, desc) in enumerate(rows, start=1):
            print(f"   {index}) {title}")
            if desc:
                print(f"        {desc}")

    @staticmethod
    def _explain(*lines: str) -> None:
        """Print a short, indented 'what this step is about' blurb, then a gap."""
        for line in lines:
            print(f" {line}")
        print()

    # -- input primitives ------------------------------------------------- #

    def _ask_int(self, low: int, high: int, default: int | None = None) -> int:
        hint = f" [{default}]" if default is not None else ""
        while True:
            raw = self._input(f"\n Enter a number {low}-{high}{hint}: ")
            if not raw and default is not None:
                return default
            if raw.isdigit() and low <= int(raw) <= high:
                return int(raw)
            print(f"   Please enter a number between {low} and {high}.")

    def _ask_multi(self, count: int, default: list[int] | None = None) -> list[int]:
        while True:
            raw = self._input("\n Numbers (space-separated), or Enter to keep the default: ")
            if not raw:
                return list(default) if default is not None else []
            parts = raw.replace(",", " ").split()
            if all(p.isdigit() and 1 <= int(p) <= count for p in parts):
                return sorted({int(p) for p in parts})
            print(f"   Enter space-separated numbers 1-{count}, or Enter for the default.")

    def _ask_text(self, prompt: str, default: str = "") -> str:
        shown = f"{prompt} [{default}]: " if default else f"{prompt}: "
        raw = self._input(f" {shown}")
        return raw or default

    def _ask_yesno(self, prompt: str, default: bool = False) -> bool:
        suffix = " [Y/n]: " if default else " [y/N]: "
        raw = self._input(f" {prompt}{suffix}").strip().lower()
        if not raw:
            return default
        return raw in {"y", "yes"}

    @staticmethod
    def _input(prompt: str) -> str:
        try:
            return input(prompt).strip()
        except (EOFError, KeyboardInterrupt):
            print()
            raise KeyboardInterrupt from None

    # -- menu ------------------------------------------------------------- #

    def _menu_rows(self) -> list[tuple[str, str, str]]:
        ollama = (OP_CONFIG_OLLAMA, "Configure Ollama models",
                  "Pick the fast + summary models, from those installed (ollama list).")
        quit_row = ("quit", "Quit", "Exit. Nothing is changed.")
        if not self.detected.is_installed:
            return [
                (OP_INSTALL, "Install neo-localmcp",
                 "Build the runtime and connect Claude Code / Codex / Claude Desktop."),
                ollama,
                quit_row,
            ]
        rows = []
        if self.detected.is_broken:
            rows.append((OP_REINSTALL, "Repair (reinstall)",
                         "Rebuild the runtime to fix a broken install. Keeps all data."))
        else:
            rows.append((OP_REINSTALL, "Reinstall / update",
                         "Replace the runtime with this checkout. Keeps all memory/data."))
        rows.append(ollama)
        rows.append((OP_MANAGE_CLIENTS, "Manage connected clients",
                     "Add or remove which AI clients are connected."))
        rows.append((OP_UNINSTALL, "Uninstall",
                     "Remove the runtime. Optionally wipe all data too."))
        rows.append(quit_row)
        return rows

    def _main_menu(self) -> str:
        self.detected = self.backend.detect()
        self.summary = []
        self.state = WizardState()
        rows = self._menu_rows()
        self._header("What would you like to do?")
        self._explain(
            "neo-localmcp is a local MCP server that gives your AI tools fast,",
            "deterministic repository context. This wizard sets it up and connects it.",
        )
        self._print_options([(title, desc) for _, title, desc in rows])
        choice = self._ask_int(1, len(rows))
        return rows[choice - 1][0]

    # -- flows ------------------------------------------------------------ #

    def _flow_clients(self, *, manage: bool) -> None:
        options = self.backend.client_options()
        registered = {opt.key for opt in options if opt.registered}
        default_keys = registered if manage else set(self.prefs.get("last_clients") or [])
        default_indices = [i for i, o in enumerate(options, 1) if o.key in default_keys]

        prompt = ("Which clients should stay connected?"
                  if manage else "Which clients should connect to neo-localmcp?")
        self._header(prompt)
        self._explain(
            "neo-localmcp registers itself with each AI client you pick so that",
            "client can call it. The path under each option is where that client",
            "is configured on this OS.",
        )
        for index, opt in enumerate(options, start=1):
            mark = "  (connected)" if opt.key in registered else ""
            manual = "  [manual step]" if opt.manual else ""
            print(f"   {index}) {opt.label}{mark}{manual}")
            print(f"        path: {opt.config_path}")
            if opt.detail:
                print(f"        {opt.detail}")
            print()
        picks = self._ask_multi(len(options), default=default_indices)
        chosen = [options[i - 1].key for i in picks]
        self.state.selected_clients = chosen
        self.summary.append(("Clients", ", ".join(chosen) or "none"))

    def _pick_model(self, label: str, hint: str, models: tuple[str, ...], current: str) -> str:
        self._header(f"Choose the {label}:")
        self._explain(hint, "Listed below are the models from your `ollama list`.")
        default = models.index(current) + 1 if current in models else 1
        for index, model in enumerate(models, start=1):
            mark = "  (current)" if model == current else ""
            print(f"   {index}) {model}{mark}")
        choice = self._ask_int(1, len(models), default=default)
        return models[choice - 1]

    def _flow_ollama(self, *, optional: bool) -> None:
        info = self.backend.ollama_info()
        self._header("Ollama models  (optional)")
        self._explain(
            "Ollama is optional. When present, neo-localmcp uses it locally to",
            "re-rank results and summarize files. Deterministic context always",
            "works without it, so this step is safe to skip.",
            f"Status: {info.detail}",
        )
        if optional and not self._ask_yesno("Configure Ollama models now?",
                                            default=info.reachable):
            self.state.configure_ollama = False
            self.summary.append(("Ollama", "not configured"))
            return
        self.state.configure_ollama = True
        self.state.ollama_base_url = self._ask_text("\n Ollama base URL", info.base_url)
        if info.reachable and info.installed_models:
            fast = self._pick_model(
                "fast model (ranking)",
                "Used to quickly re-rank candidate files. A smaller, fast model is best.",
                info.installed_models, info.fast_model)
            summary = self._pick_model(
                "summary model (file summaries)",
                "Used to write file/section summaries on request. A larger code model is best.",
                info.installed_models, info.summary_model)
        else:
            self._header("Ollama models")
            print(" No installed models detected - enter names manually.\n")
            fast = self._ask_text("fast model", info.fast_model)
            summary = self._ask_text("summary model", info.summary_model)
        self.state.fast_model = fast
        self.state.summary_model = summary
        self.summary.append(("Ollama", f"fast={fast}, summary={summary}"))

    def _flow_uninstall(self) -> bool:
        self._header("Uninstall neo-localmcp")
        self._explain(
            "Choose how much to remove. 'Runtime only' disconnects clients and",
            "deletes the venv but keeps your indexed memory/data, so a later",
            "reinstall is instant. 'Full wipe' deletes everything permanently.",
        )
        self._print_options([
            ("Remove runtime only", "keeps all memory/data (recommended)"),
            ("Full wipe", "delete the entire managed root and ALL stored data"),
        ])
        choice = self._ask_int(1, 2, default=1)
        full = choice == 2
        if full:
            print(f"\n A full wipe permanently deletes everything under")
            print(f"   {self.detected.managed_root}")
            typed = self._ask_text(f'\n Type "{FULL_WIPE_PHRASE}" to confirm')
            if typed != FULL_WIPE_PHRASE:
                print("\n Not confirmed - cancelled.")
                return False
        self.state.full_wipe = full
        self.summary.append(("Mode", "full wipe" if full else "runtime only"))
        return self._ask_yesno("\n Proceed?", default=not full)

    def _confirm(self, *, allow_dry_run: bool) -> bool:
        self._header("Review and confirm")
        self._explain(
            "Nothing has changed yet. Your choices are summarized above. If you",
            "want to see the exact steps without touching anything, use dry run.",
        )
        if allow_dry_run:
            self.state.dry_run = self._ask_yesno(
                "Preview only (dry run - show the plan, change nothing)?", default=False)
        return self._ask_yesno("Proceed?", default=True)

    # -- execution -------------------------------------------------------- #

    def _run(self) -> None:
        self._header("Working...")
        op = self.state.operation

        def emit(event) -> None:
            print(f"   {event.message}")
            sys.stdout.flush()

        if op == OP_CONFIG_OLLAMA:
            outcome = self.backend.apply_ollama_config(self.state, emit)
        elif op == OP_MANAGE_CLIENTS:
            outcome = self.backend.apply_client_changes(self.state, emit)
        else:
            outcome = self.backend.run_operation(self.state, emit)

        print()
        print(f" {outcome.title}")
        for line in outcome.detail_lines:
            print(f"   {line}")
        if outcome.log_hint:
            print(f"\n   Logs: {outcome.log_hint}")
        if outcome.next_command:
            print(f"   Try next:  {outcome.next_command}")
        self._save_prefs(outcome)

    def _save_prefs(self, outcome) -> None:
        if self.state.dry_run or not outcome.ok:
            return
        prefs = dict(self.prefs)
        if self.state.operation in {OP_INSTALL, OP_MANAGE_CLIENTS}:
            prefs["last_clients"] = list(self.state.selected_clients)
        if self.state.configure_ollama:
            prefs["fast_model"] = self.state.fast_model
            prefs["summary_model"] = self.state.summary_model
            prefs["base_url"] = self.state.ollama_base_url
        self.backend.save_prefs(prefs)
        self.prefs = prefs

    def _dispatch(self, op: str) -> None:
        self.state = WizardState(operation=op)
        labels = {
            OP_INSTALL: "install", OP_REINSTALL: "reinstall", OP_UNINSTALL: "uninstall",
            OP_CONFIG_OLLAMA: "configure Ollama", OP_MANAGE_CLIENTS: "manage clients",
        }
        self.summary = [("Operation", labels.get(op, op))]

        if op == OP_INSTALL:
            self._flow_clients(manage=False)
            self._flow_ollama(optional=True)
            if self._confirm(allow_dry_run=True):
                self._run()
        elif op == OP_REINSTALL:
            existing = ", ".join(self.detected.registered_clients) or "none"
            self.summary.append(("Clients", f"kept as-is ({existing})"))
            if self._confirm(allow_dry_run=True):
                self._run()
        elif op == OP_CONFIG_OLLAMA:
            self._flow_ollama(optional=False)
            if self._confirm(allow_dry_run=False):
                self._run()
        elif op == OP_MANAGE_CLIENTS:
            self._flow_clients(manage=True)
            if self._confirm(allow_dry_run=False):
                self._run()
        elif op == OP_UNINSTALL:
            if self._flow_uninstall():
                self._run()

    # -- loop ------------------------------------------------------------- #

    def run(self) -> int:
        try:
            while True:
                op = self._main_menu()
                if op == "quit":
                    _clear()
                    print("Goodbye.")
                    return 0
                self._dispatch(op)
                self._input("\n Press Enter to return to the menu... ")
        except KeyboardInterrupt:
            _clear()
            print("Cancelled. Nothing further was changed.")
            return 0


def run(argv: list[str] | None = None) -> int:
    argv = list(argv or [])
    fake = "--fake" in argv
    if fake:
        from .fake_backend import FakeBackend

        backend: WizardBackend = FakeBackend()
    else:
        from .real_backend import RealBackend

        backend = RealBackend()
    return ConsoleWizard(backend=backend, fake=fake).run()
