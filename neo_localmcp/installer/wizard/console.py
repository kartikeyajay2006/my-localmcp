"""A simple, full-screen, numbered terminal wizard.

No TUI framework: it clears the screen, prints a running summary of your answers
so far, asks one numbered question at a time, and reads a number. It drives the
same :class:`WizardBackend` the rest of the package uses, so install / reinstall
/ uninstall / Ollama-config / client-management all go through the exact same
lifecycle code ``setup.py`` uses.

Every prompt accepts "b"/"back" to return to the previous question, in addition
to whatever else it accepts (a number, y/n, or free text). Navigation is a small
phase machine (see ``_run_phases``): each phase is a bound method that either
raises ``_Skip`` immediately (nothing to ask given the current answers) or asks
its question(s) and returns; typing "back" makes the *next* phase run backward.
Phases are re-evaluated live every time they're visited, so changing an earlier
answer (e.g. turning Ollama config off) correctly skips the phases that
depended on it, in both directions.
"""

from __future__ import annotations

import os
import re
import shutil
import sys
import textwrap
from typing import Callable

from . import _ansi
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

_WIDTH = 80
_MAX_TEXT_WIDTH = 100

# scheme://host[:port][/], port 0-99999 (loose -- real callers only ever see a
# handful of malformed typos, not adversarial input; this just catches those)
_OLLAMA_URL_RE = re.compile(r"^https?://[^\s/:]+(:[0-9]{1,5})?/?$")


def _is_valid_ollama_url(value: str) -> bool:
    return bool(_OLLAMA_URL_RE.match(value.strip()))


def _model_kind_label(capabilities: tuple[str, ...]) -> str:
    # Ollama's own reported capability tags -> a short display label; "embedding" without
    # "completion" is an embed-only model (wrong choice for fast/summary), anything with
    # "completion" is a normal chat/generation model.
    if not capabilities:
        return ""
    if "embedding" in capabilities and "completion" not in capabilities:
        return "embed"
    if "completion" in capabilities:
        return "chat"
    return "/".join(capabilities)


def _clear() -> None:
    os.system("cls" if os.name == "nt" else "clear")


def _text_width() -> int:
    # 100 columns, or narrower if the terminal itself is narrower
    cols = shutil.get_terminal_size(fallback=(_MAX_TEXT_WIDTH, 24)).columns
    return max(20, min(_MAX_TEXT_WIDTH, cols))


def _wrap(text: str, indent: str = " ", subsequent_indent: str | None = None) -> list[str]:
    # wraps text to the current terminal width, indenting every physical line
    sub = indent if subsequent_indent is None else subsequent_indent
    return textwrap.wrap(
        text, width=_text_width(), initial_indent=indent, subsequent_indent=sub,
    ) or [indent.rstrip()]


class _GoBack(Exception):
    # raised by an input primitive when the user types 'b'/'back'
    pass


class _Skip(Exception):
    # raised by a phase that has nothing to ask given the current answers
    pass


class _Abort(Exception):
    # raised when the user explicitly declines to proceed at a final confirm
    pass


class _TogglePreview(Exception):
    # raised by the main-menu prompt when the user types 'p'/'preview'
    pass


def _is_back(raw: str) -> bool:
    return raw.strip().lower() in {"b", "back"}


class ConsoleWizard:
    def __init__(self, backend: WizardBackend, fake: bool) -> None:
        self.backend = backend
        self.fake = fake
        self.detected = backend.detect()
        self.prefs = backend.load_prefs()
        self.state = WizardState()
        self.summary: list[tuple[str, str]] = []
        # Set per-dispatch to parameterize shared phase methods.
        self._ollama_optional = False
        self._clients_manage_mode = False
        # True once the clients phase has actually run this dispatch, so the
        # running summary can show full per-client detail (not just "chose none").
        self._clients_chosen = False

    # -- rendering -------------------------------------------------------- #

    def _header(self, question: str = "") -> None:
        # full-screen redraw before every prompt: clear -> title bar -> os/state -> running choices -> optional question
        _clear()
        d = self.detected

        # Content - Header title bar
        print(_ansi.cyan_bold("=" * _WIDTH))
        title = _ansi.cyan_bold(" neo-localmcp setup wizard")
        if self.fake:
            title += "   " + _ansi.yellow("[Preview Mode]")
        print(title)
        print(_ansi.cyan_bold("=" * _WIDTH))
        
        # System Info
        print(f" {d.os_label} | Python {d.python_version}")
        print(f" {d.state_label}")

        # User choices
        if d.registered_clients:
            print(f" Connected clients: {', '.join(d.registered_clients)}")
        if self.summary or self._clients_chosen:
            print()
            print(" Your choices so far:")
            for label, value in self.summary:
                print(f"   {label}: {value}")
            if self._clients_chosen:
                for line in self._client_detail_lines():
                    print(line)
        
        # Barrier
        print("-" * _WIDTH)
        
        # Prompt
        if question:
            print(_ansi.cyan_bold(f" {question}"))
            print()

    @staticmethod
    def _print_options(rows: list[tuple[str, str]]) -> None:
        for index, (title, desc) in enumerate(rows, start=1):
            print(f"   {index}) {title}") # Option
            if desc:
                for line in _wrap(desc, indent="        "):
                    print(_ansi.dim(line)) #Subtext

    @staticmethod
    def _explain(*lines: str) -> None:
        # prints a short, indented "what this step is about" blurb, then a blank line
        for line in lines:
            for wrapped in _wrap(line, indent=" "):
                print(wrapped)
        print()

    def _set_summary(self, label: str, value: str) -> None:
        # sets (replaces, never duplicates) one running-summary line -- phases can be revisited via "back", so this must overwrite or redoing a step would duplicate its line
        self.summary = [(l, v) for l, v in self.summary if l != label]
        self.summary.append((label, value))

    # -- input primitives --------------------------------------------------
    # Every primitive accepts "b"/"back" (raising _GoBack) in addition to its
    # normal input, and shows its default (if any) as "[Default: ...]".

    def _ask_int(
        self, low: int, high: int, default: int | None = None,
        allow_preview_toggle: bool = False,
    ) -> int:
        hint = f" [Default: {default}]" if default is not None else ""
        toggle_hint = " (or p for preview mode)" if allow_preview_toggle else ""
        while True:
            raw = self._input(
                f"\n Enter a number {low}-{high}{hint} (or b to go back){toggle_hint}: ")
            if allow_preview_toggle and raw.strip().lower() in {"p", "preview"}:
                raise _TogglePreview
            if _is_back(raw):
                raise _GoBack
            if not raw and default is not None:
                return default
            if raw.isdigit() and low <= int(raw) <= high:
                return int(raw)
            print(f"   Please enter a number between {low} and {high}.")

    def _ask_multi(self, count: int, default: list[int] | None = None) -> list[int]:
        default = default or []
        hint = f" [Default: {', '.join(str(i) for i in default) or 'none'}]"
        while True:
            raw = self._input(f"\n Numbers (space-separated){hint} (or b to go back): ")
            if _is_back(raw):
                raise _GoBack
            if not raw:
                return list(default)
            parts = raw.replace(",", " ").split()
            if all(p.isdigit() and 1 <= int(p) <= count for p in parts):
                return sorted({int(p) for p in parts})
            print(f"   Enter space-separated numbers 1-{count}, or Enter for the default.")

    def _ask_text(self, prompt: str, default: str = "") -> str:
        hint = f" [Default: {default}]" if default else ""
        raw = self._input(f" {prompt}{hint} (or b to go back): ")
        if _is_back(raw):
            raise _GoBack
        return raw or default

    def _ask_yesno(self, prompt: str, default: bool = False) -> bool:
        hint = "[Default: Y] (y/n/b)" if default else "[Default: N] (y/n/b)"
        raw = self._input(f" {prompt} {hint}: ").strip().lower()
        if _is_back(raw):
            raise _GoBack
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

    # -- phase driver ------------------------------------------------------
    # Each phase is a zero-arg bound method. Moving forward after a phase
    # succeeds, or backward after it raises _GoBack; a phase that raises _Skip
    # (nothing to ask given current state) is passed over in whichever
    # direction we're already moving. _Abort ends the whole flow immediately
    # (used for an explicit "No" at a final confirm) without running anything.

    def _run_phases(self, phases: list[Callable[[], None]]) -> bool:
        # phase succeeds -> advance; _GoBack -> reverse direction and step back; _Skip -> step over in whichever direction we're already moving; _Abort -> end immediately, False
        idx = 0
        direction = 1
        while 0 <= idx < len(phases):
            try:
                phases[idx]()
            except _GoBack:
                direction = -1
                idx += direction
                continue
            except _Skip:
                idx += direction
                continue
            except _Abort:
                return False
            direction = 1
            idx += 1
        return idx >= len(phases)

    # -- menu ------------------------------------------------------------- #

    def _menu_rows(self) -> list[tuple[str, str, str]]:
        # detected install state -> the (op_key, title, description) rows the main menu offers
        # not installed -> install-only; installed -> reinstall/repair + manage-clients + uninstall
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
        # re-detects state fresh on every visit (loop body), so a menu reached via "back to menu" always reflects current reality
        while True:
            self.detected = self.backend.detect()
            self.summary = []
            self._clients_chosen = False
            self.state = WizardState()
            rows = self._menu_rows()
            self._header("What would you like to do?")
            self._explain(
                "neo-localmcp is a local MCP server that gives your AI tools fast,",
                "deterministic repository context. This wizard sets it up and connects it.",
            )
            self._print_options([(title, desc) for _, title, desc in rows])
            try:
                choice = self._ask_int(1, len(rows), allow_preview_toggle=not self.fake)
            except _TogglePreview:
                self._enter_preview()
                continue
            return rows[choice - 1][0]

    def _enter_preview(self) -> None:
        # one-way switch to PreviewBackend for the rest of this process
        from .preview_backend import PreviewBackend

        self.backend = PreviewBackend()
        self.fake = True
        self.detected = self.backend.detect()
        self.prefs = self.backend.load_prefs()

    # -- phase: clients ----------------------------------------------------

    def _phase_clients(self) -> None:
        # default selection: this-session pick (if revisited via "back") -> else currently-registered (manage mode) -> else last_clients pref (install mode)
        manage = self._clients_manage_mode
        options = self.backend.client_options()
        registered = {opt.key for opt in options if opt.registered}
        if self.state.selected_clients:
            default_keys = set(self.state.selected_clients)
        elif manage:
            default_keys = registered
        else:
            default_keys = set(self.prefs.get("last_clients") or [])
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
            mark = _ansi.green("  (connected)") if opt.key in registered else ""
            manual = "  [manual step]" if opt.manual else ""
            print(f"   {index}) {opt.label}{mark}{manual}")
            for line in _wrap(f"path: {opt.config_path}", indent="        "):
                print(_ansi.dim(line))
            if opt.detail:
                for line in _wrap(opt.detail, indent="        "):
                    print(_ansi.dim(line))
            print()
        picks = self._ask_multi(len(options), default=default_indices)
        chosen = [options[i - 1].key for i in picks]
        self.state.selected_clients = chosen
        self._clients_chosen = True

    # -- phases: ollama ------------------------------------------------------

    def _phase_ollama_yesno(self) -> None:
        if not self._ollama_optional:
            # Standalone "Configure Ollama models" already means yes; nothing to ask.
            self.state.configure_ollama = True
            raise _Skip
        info = self.backend.ollama_info()
        self._header("Ollama models  (optional)")
        self._explain(
            "Ollama is optional. When present, it allows for:",
            "  - Re-ranking results via fast models",
            "  - Summarizing files",
            "Deterministic context will always work regardless, so it's safe to skip.",
            f"Status: {info.detail}",
        )
        self.state.configure_ollama = self._ask_yesno(
            "Configure Ollama models now?", default=info.reachable)
        if not self.state.configure_ollama:
            self._set_summary("Ollama", "skipped config")

    def _phase_ollama_baseurl(self) -> None:
        if not self.state.configure_ollama:
            raise _Skip
        info = self.backend.ollama_info()
        self._header("Ollama endpoint")
        self._explain(
            "This is the base URL neo-localmcp will talk to Ollama on -- almost",
            "always the local default unless you run Ollama remotely.",
        )
        current = self.state.ollama_base_url or info.base_url
        while True:
            print(f"\n The endpoint URL to use Ollama is: [{current}]\n")
            if self._ask_yesno("Looks good?", default=True):
                self.state.ollama_base_url = current
                return
            current = self._ask_new_url("Please type the new endpoint", current)

    def _ask_new_url(self, prompt: str, default: str) -> str:
        # like _ask_text, but loops on a syntactically invalid URL instead of accepting anything
        hint = f" [Default: {default}]" if default else ""
        while True:
            raw = self._input(f" {prompt}{hint} (or b to go back): ")
            if _is_back(raw):
                raise _GoBack
            value = raw or default
            if _is_valid_ollama_url(value):
                return value
            print("   Enter a URL like http://host:port (e.g. http://127.0.0.1:11434).")

    @staticmethod
    def _format_model_table(models: list[str], info, current: str, start_index: int = 1) -> list[str]:
        # index) name  size  kind -- kind is Ollama's own reported capability tag ("chat"/
        # "embed"), not a guess, so a user can't accidentally pick an embed-only model for
        # fast/summary. The current selection's row is colored (no-op if color is disabled).
        name_width = max((len(m) for m in models), default=0)
        rows = []
        for offset, model in enumerate(models):
            index = start_index + offset
            size = info.model_sizes.get(model, "")
            kind = _model_kind_label(info.model_capabilities.get(model, ()))
            marker = "  (current)" if model == current else ""
            line = f"   {index}) {model:<{name_width}}  {size:<9}  {kind:<6}{marker}"
            rows.append(_ansi.green(line) if model == current else line)
        return rows

    def _pick_model(self, label: str, hint: list[str], info, current: str) -> str:
        self._header(f"Choose the {label}:")
        self._explain(*hint, "Listed below are the models from your `ollama list`,")
        print(" sorted alphabetically:\n")
        models = list(info.installed_models)
        default = models.index(current) + 1 if current in models else 1
        for row in self._format_model_table(models, info, current):
            print(row)
        choice = self._ask_int(1, len(models), default=default)
        return models[choice - 1]

    def _phase_ollama_fast(self) -> None:
        if not self.state.configure_ollama:
            raise _Skip
        info = self.backend.ollama_info()
        if info.reachable and info.installed_models:
            self.state.fast_model = self._pick_model(
                "fast model (ranking)",
                [
                    "This is the model used to quickly re-rank candidate files before",
                    "showing them to your AI tool -- e.g. for a task like 'fix the login",
                    "bug', it decides which files are probably relevant. A smaller,",
                    "faster model is best here; you don't need a large coder model.",
                ],
                info, self.state.fast_model or info.fast_model)
        else:
            self._header("Fast model (ranking)")
            self._explain(
                "No installed models were detected, so enter the name manually.",
                "This is the model used to quickly re-rank candidate files, e.g. for",
                "'fix the login bug' it decides which files are probably relevant.",
            )
            self.state.fast_model = self._ask_text("Fast model", info.fast_model)

    def _phase_ollama_summary(self) -> None:
        if not self.state.configure_ollama:
            raise _Skip
        info = self.backend.ollama_info()
        if info.reachable and info.installed_models:
            self.state.summary_model = self._pick_model(
                "summary model (file summaries)",
                ["Used to write file/section summaries on request. A larger,",
                 "code-capable model gives better summaries than the fast model."],
                info, self.state.summary_model or info.summary_model)
        else:
            self._header("Summary model (file summaries)")
            self._explain(
                "No installed models were detected, so enter the name manually.",
                "Used to write file/section summaries on request.",
            )
            self.state.summary_model = self._ask_text("Summary model", info.summary_model)
        self._set_summary(
            "Ollama", f"fast={self.state.fast_model}, summary={self.state.summary_model}")

    _EMBED_HINT = (
        "OPTIONAL -- leave disabled unless you want the semantic layer.",
        "An embedding model lets retrieval match by MEANING, not just keywords:",
        "it recognizes a paraphrased task as the same recurring workflow (e.g.",
        "'fix login bug' ~ 'debug auth failure') and nudges file ranking by",
        "similarity. Deterministic keyword ranking is always the primary path and",
        "works fully without this. Use a dedicated embedding model (e.g.",
        "nomic-embed-text, mxbai-embed-large), not a chat model.",
    )

    def _phase_ollama_embed(self) -> None:
        if not self.state.configure_ollama:
            raise _Skip
        info = self.backend.ollama_info()
        current = self.state.embed_model or info.embed_model  # "" when disabled
        if info.reachable and info.installed_models:
            self._header("Embedding model for the semantic layer (optional):")
            self._explain(*self._EMBED_HINT, "", "Choose 0 to leave it disabled (the default).")
            models = list(info.installed_models)
            print("\n   0) None -- leave the semantic layer disabled")
            for row in self._format_model_table(models, info, current, start_index=1):
                print(row)
            default = models.index(current) + 1 if current in models else 0
            choice = self._ask_int(0, len(models), default=default)
            self.state.embed_model = "" if choice == 0 else models[choice - 1]
        else:
            self._header("Embedding model for the semantic layer (optional)")
            self._explain(*self._EMBED_HINT, "", "Leave blank to keep it disabled.")
            self.state.embed_model = self._ask_text("Embedding model (blank = disabled)", current)
        self._set_summary(
            "Ollama",
            f"fast={self.state.fast_model}, summary={self.state.summary_model}, "
            f"embed={self.state.embed_model or 'disabled'}")

    # -- phase: uninstall ---------------------------------------------------

    def _phase_uninstall_mode(self) -> None:
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
        self.state.full_wipe = choice == 2
        self._set_summary("Mode", "full wipe" if self.state.full_wipe else "runtime only")

    def _phase_uninstall_wipe_confirm(self) -> None:
        if not self.state.full_wipe:
            raise _Skip
        self._header("Confirm full wipe")
        for line in _wrap("A full wipe permanently deletes everything under:", indent=" "):
            print(_ansi.red_bold(line))
        print(_ansi.red_bold(f"   {self.detected.managed_root}"))
        print()
        while True:
            typed = self._ask_text(f'Type "{FULL_WIPE_PHRASE}" to confirm')
            if typed == FULL_WIPE_PHRASE:
                return
            print()
            for line in _wrap(f"That did not match. Type exactly: {FULL_WIPE_PHRASE}",
                               indent="   "):
                print(_ansi.red_bold(line))

    # -- phase: confirm ------------------------------------------------------

    def _client_detail_lines(self) -> list[str]:
        if not self.state.selected_clients:
            return ["   Chosen clients: none"]
        options = {opt.key: opt for opt in self.backend.client_options()}
        lines = ["   Chosen clients:"]
        for i, key in enumerate(self.state.selected_clients, start=1):
            opt = options.get(key)
            label = CLIENT_LABELS.get(key, key)
            if opt is None:
                lines.append(f"     {i}) {label}")
                continue
            manual = "  [manual step]" if opt.manual else ""
            lines.append(f"     {i}) {label}{manual}")
            lines.extend(_wrap(f"path: {opt.config_path}", indent="          "))
            if opt.detail:
                lines.extend(_wrap(opt.detail, indent="          "))
        return lines

    _CONFIRM_VERBS = {
        OP_INSTALL: "Install",
        OP_REINSTALL: "Reinstall",
        OP_UNINSTALL: "Uninstall",
        OP_CONFIG_OLLAMA: "Apply",
        OP_MANAGE_CLIENTS: "Apply",
    }

    def _confirm(self, *, default_proceed: bool = True) -> None:
        # final gate before any change: show chosen actions -> single yes/no; no -> _Abort
        self._header("Review and confirm")
        self._explain(
            "No changes have been made yet. Your current choices are shown above",
            "and will be applied accordingly.",
        )
        if self.fake:
            for line in _wrap(
                "** Preview mode is active -- choosing Yes will show a demo output. "
                "No changes will be made at all.", indent=" ",
            ):
                print(_ansi.yellow(line))
            print()
        print(f"   Managed root: {self.detected.managed_root}")
        print()

        verb = self._CONFIRM_VERBS.get(self.state.operation, "Proceed")
        prompt = "Proceed?" if verb == "Proceed" else f"{verb} as shown?"
        if not self._ask_yesno(prompt, default=default_proceed):
            raise _Abort

    def _phase_confirm(self) -> None:
        default_proceed = not (self.state.operation == OP_UNINSTALL and self.state.full_wipe)
        self._confirm(default_proceed=default_proceed)

    # -- execution -------------------------------------------------------- #
    def _run(self) -> None:
        # dispatches to the matching backend call by operation, streams its progress via emit, then renders the OperationOutcome
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
        color = _ansi.green if outcome.ok else _ansi.red_bold
        for line in _wrap(outcome.title, indent=" "):
            print(color(line))
        for detail in outcome.detail_lines:
            for line in _wrap(detail, indent="   "):
                print(color(line))
        if outcome.log_hint:
            print()
            for line in _wrap(f"Logs: {outcome.log_hint}", indent="   "):
                print(line)
        if outcome.next_command:
            for line in _wrap(f"Try next:  {outcome.next_command}", indent="   "):
                print(line)
        self._save_prefs(outcome)

    def _save_prefs(self, outcome) -> None:
        if self.state.dry_run or not outcome.ok:
            return
        if self.state.operation == OP_UNINSTALL:
            # never write prefs after tearing something down -- a full wipe deletes the whole root, and recreating config/ just for a prefs file would undermine that
            return
        prefs = dict(self.prefs)
        if self.state.operation in {OP_INSTALL, OP_MANAGE_CLIENTS}:
            prefs["last_clients"] = list(self.state.selected_clients)
        if self.state.configure_ollama:
            prefs["fast_model"] = self.state.fast_model
            prefs["summary_model"] = self.state.summary_model
            prefs["embed_model"] = self.state.embed_model
            prefs["base_url"] = self.state.ollama_base_url
        self.backend.save_prefs(prefs)
        self.prefs = prefs

    # -- dispatch ----------------------------------------------------------- #

    def _dispatch(self, op: str) -> None:
        # operation -> its ordered phase list -> _run_phases -> execute on success, else report cancelled
        self.state = WizardState(operation=op)
        labels = {
            OP_INSTALL: "install", OP_REINSTALL: "reinstall", OP_UNINSTALL: "uninstall",
            OP_CONFIG_OLLAMA: "configure Ollama", OP_MANAGE_CLIENTS: "manage clients",
        }
        self.summary = [("Operation", labels.get(op, op))]
        self._clients_chosen = False
        self._ollama_optional = op == OP_INSTALL
        self._clients_manage_mode = op == OP_MANAGE_CLIENTS

        if op == OP_INSTALL:
            phases = [
                self._phase_clients,
                self._phase_ollama_yesno,
                self._phase_ollama_baseurl,
                self._phase_ollama_fast,
                self._phase_ollama_summary,
                self._phase_ollama_embed,
                self._phase_confirm,
            ]
        elif op == OP_REINSTALL:
            existing = ", ".join(self.detected.registered_clients) or "none"
            self._set_summary("Clients", f"kept as-is ({existing})")
            phases = [self._phase_confirm]
        elif op == OP_CONFIG_OLLAMA:
            phases = [
                self._phase_ollama_yesno,
                self._phase_ollama_baseurl,
                self._phase_ollama_fast,
                self._phase_ollama_summary,
                self._phase_ollama_embed,
                self._phase_confirm,
            ]
        elif op == OP_MANAGE_CLIENTS:
            phases = [self._phase_clients, self._phase_confirm]
        elif op == OP_UNINSTALL:
            phases = [
                self._phase_uninstall_mode,
                self._phase_uninstall_wipe_confirm,
                self._phase_confirm,
            ]
        else:
            return

        if self._run_phases(phases):
            self._run()
        else:
            print("\n Cancelled - nothing was changed.")

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
    fake = "--preview" in argv
    if fake:
        from .preview_backend import PreviewBackend

        backend: WizardBackend = PreviewBackend()
    else:
        from .live_backend import LiveBackend

        backend = LiveBackend()
    return ConsoleWizard(backend=backend, fake=fake).run()
