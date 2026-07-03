"""Client selection: which AI surfaces connect to neo-localmcp, with OS paths."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Button, Footer, Header, SelectionList, Static
from textual.widgets.selection_list import Selection

from ..backend import OP_MANAGE_CLIENTS
from .common import heading, option_text


class ClientsScreen(Screen):
    BINDINGS = [("escape", "app.pop_screen", "Back")]

    def __init__(self, mode: str) -> None:
        super().__init__()
        self.mode = mode  # "install" | "manage"

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(id="body"):
            if self.mode == "install":
                subtitle = ("Pick the clients to connect now. Each shows the config file that "
                            "will be written on this OS. You can change these later.")
            else:
                subtitle = ("Checked clients stay/become connected; unchecked ones are "
                            "disconnected. Paths shown are this OS's config locations.")
            yield Static(heading("Connect your AI clients", subtitle), classes="title")

            options = self.app.backend.client_options()
            preselected = self._preselected(options)
            selections = [
                Selection(
                    option_text(opt.label, opt.config_path),
                    opt.key,
                    opt.key in preselected,
                )
                for opt in options
            ]
            yield SelectionList[str](*selections, id="clients")

            with Horizontal(id="buttons"):
                yield Button("Back", id="back")
                label = "Next" if self.mode == "install" else "Apply changes"
                yield Button(label, id="next", variant="primary")
        yield Footer()

    def _preselected(self, options) -> set[str]:
        if self.mode == "manage":
            return {opt.key for opt in options if opt.registered}
        # install mode: remember the user's last choice if we have one.
        last = self.app.prefs.get("last_clients")
        if isinstance(last, list) and last:
            return set(last)
        return set()

    def on_mount(self) -> None:
        self.query_one("#clients", SelectionList).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "back":
            self.app.pop_screen()
            return
        selected = list(self.query_one("#clients", SelectionList).selected)
        self.app.wiz.selected_clients = selected

        if self.mode == "manage":
            self.app.wiz.operation = OP_MANAGE_CLIENTS
            from .progress import ProgressScreen

            self.app.push_screen(ProgressScreen())
        else:
            from .ollama import OllamaScreen

            self.app.push_screen(OllamaScreen(mode="install"))
