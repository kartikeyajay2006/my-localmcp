"""Progress screen: runs the chosen operation in a worker thread, live-logs it."""

from __future__ import annotations

from rich.text import Text
from textual import work
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Button, Footer, Header, RichLog, Static

from ..backend import (
    OP_CONFIG_OLLAMA,
    OP_INSTALL,
    OP_MANAGE_CLIENTS,
    StepEvent,
)
from .common import LEVEL_STYLES, heading


class ProgressScreen(Screen):
    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(id="body"):
            yield Static(
                heading("Working…", "Live progress is shown below."),
                id="progress-heading",
                classes="title",
            )
            yield RichLog(wrap=True, markup=False, id="log")
            yield Static("", id="result-panel", classes="panel")
            with Horizontal(id="buttons"):
                yield Button("Back to menu", id="menu", disabled=True)
                yield Button("Quit", id="quit", disabled=True)
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#result-panel", Static).display = False
        self._run_operation()

    # -- worker ----------------------------------------------------------- #

    @work(thread=True)
    def _run_operation(self) -> None:
        wiz = self.app.wiz
        backend = self.app.backend

        def emit(event: StepEvent) -> None:
            self.app.call_from_thread(self._write_event, event)

        if wiz.operation == OP_CONFIG_OLLAMA:
            outcome = backend.apply_ollama_config(wiz, emit)
        elif wiz.operation == OP_MANAGE_CLIENTS:
            outcome = backend.apply_client_changes(wiz, emit)
        else:
            outcome = backend.run_operation(wiz, emit)

        self.app.call_from_thread(self._finish, outcome)

    def _write_event(self, event: StepEvent) -> None:
        style = LEVEL_STYLES.get(event.level, "")
        self.query_one("#log", RichLog).write(Text(event.message, style=style))

    # -- completion ------------------------------------------------------- #

    def _finish(self, outcome) -> None:
        self.app.wiz.outcome = outcome
        self._save_prefs(outcome)

        heading_style = "bold green" if outcome.ok else "bold red"
        self.query_one("#progress-heading", Static).update(
            Text(outcome.title, style=heading_style)
        )

        panel = self.query_one("#result-panel", Static)
        body = Text()
        for line in outcome.detail_lines:
            body.append(line + "\n")
        if outcome.log_hint:
            body.append(f"\nLogs: {outcome.log_hint}\n", style="grey62")
        if outcome.next_command:
            body.append(f"\nTry next:  {outcome.next_command}", style="cyan")
        panel.update(body)
        panel.display = True

        menu = self.query_one("#menu", Button)
        quit_button = self.query_one("#quit", Button)
        menu.disabled = False
        quit_button.disabled = False
        menu.focus()

    def _save_prefs(self, outcome) -> None:
        wiz = self.app.wiz
        if wiz.dry_run or not outcome.ok:
            return
        prefs = dict(self.app.prefs)
        if wiz.operation in {OP_INSTALL, OP_MANAGE_CLIENTS}:
            prefs["last_clients"] = list(wiz.selected_clients)
        if wiz.configure_ollama:
            prefs["fast_model"] = wiz.fast_model
            prefs["summary_model"] = wiz.summary_model
            prefs["base_url"] = wiz.ollama_base_url
        self.app.backend.save_prefs(prefs)
        self.app.prefs = prefs

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "menu":
            self.app.reset_to_home()
        elif event.button.id == "quit":
            self.app.exit()
