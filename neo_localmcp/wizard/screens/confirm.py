"""Confirmation screen: a summary of every choice before anything executes."""

from __future__ import annotations

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Button, Checkbox, Footer, Header, Static

from ..backend import OP_INSTALL, OP_REINSTALL
from .common import DIM, heading


class ConfirmScreen(Screen):
    BINDINGS = [("escape", "app.pop_screen", "Back")]

    def compose(self) -> ComposeResult:
        yield Header()
        wiz = self.app.wiz
        with Vertical(id="body"):
            yield Static(
                heading("Review and confirm",
                        "Nothing has changed yet. Review the plan, then start."),
                classes="title",
            )
            yield Static(self._summary(), classes="panel")
            if wiz.operation in {OP_INSTALL, OP_REINSTALL}:
                yield Checkbox("Preview only (dry run) — show the plan, change nothing",
                               value=False, id="dry_run")
            with Horizontal(id="buttons"):
                yield Button("Back", id="back")
                yield Button("Start", id="start", variant="primary")
        yield Footer()

    def _summary(self) -> Text:
        wiz = self.app.wiz
        text = Text()
        text.append("Operation: ", style="bold")
        text.append(f"{wiz.operation}\n")
        if wiz.operation == OP_INSTALL:
            clients = ", ".join(wiz.selected_clients) or "none"
            text.append("Connect clients: ", style="bold")
            text.append(f"{clients}\n")
        elif wiz.operation == OP_REINSTALL:
            existing = ", ".join(self.app.detected.registered_clients) or "none"
            text.append("Clients (kept as-is): ", style="bold")
            text.append(f"{existing}\n")
        if wiz.configure_ollama:
            text.append("Ollama models: ", style="bold")
            text.append(f"fast={wiz.fast_model or '(unchanged)'}, "
                        f"summary={wiz.summary_model or '(unchanged)'}\n")
            text.append("Ollama endpoint: ", style="bold")
            text.append(f"{wiz.ollama_base_url or '(unchanged)'}\n")
        else:
            text.append("Ollama: ", style="bold")
            text.append("not configured in this run\n", style=DIM)
        text.append("Managed root: ", style="bold")
        text.append(f"{self.app.detected.managed_root}\n", style=DIM)
        return text

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "back":
            self.app.pop_screen()
            return
        try:
            self.app.wiz.dry_run = self.query_one("#dry_run", Checkbox).value
        except Exception:  # noqa: BLE001 - checkbox only present for install/reinstall
            self.app.wiz.dry_run = False
        from .progress import ProgressScreen

        self.app.push_screen(ProgressScreen())
