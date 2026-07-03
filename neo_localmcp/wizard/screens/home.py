"""Home screen: an adaptive main menu keyed on the detected install state."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import Screen
from textual.widgets import Footer, Header, OptionList, Static
from textual.widgets.option_list import Option

from ..backend import (
    OP_CONFIG_OLLAMA,
    OP_INSTALL,
    OP_MANAGE_CLIENTS,
    OP_REINSTALL,
    OP_UNINSTALL,
)
from .common import heading, option_text


def _menu_for(detected) -> list[tuple[str, str, str]]:
    """Return (id, title, description) rows appropriate to the current state."""
    ollama = (
        OP_CONFIG_OLLAMA,
        "Configure Ollama models",
        "Pick the fast + summary models neo-localmcp uses, from those installed (ollama list).",
    )
    quit_row = ("quit", "Quit", "Exit the wizard. Nothing is changed.")

    if not detected.is_installed:
        return [
            (OP_INSTALL, "Install neo-localmcp",
             "Build the managed runtime and connect Claude Code / Codex / Claude Desktop."),
            ollama,
            quit_row,
        ]

    rows: list[tuple[str, str, str]] = []
    if detected.is_broken:
        rows.append((OP_REINSTALL, "Repair (reinstall)",
                     "Rebuild the runtime to fix a broken or half-finished install. Keeps all data."))
    else:
        rows.append((OP_REINSTALL, "Reinstall / update",
                     "Replace the runtime with this checkout's version. Keeps all memory/data."))
    rows.append(ollama)
    rows.append((OP_MANAGE_CLIENTS, "Manage connected clients",
                 "Add or remove which AI clients are connected to neo-localmcp."))
    rows.append((OP_UNINSTALL, "Uninstall",
                 "Remove the runtime. Optionally wipe all stored memory/data too."))
    rows.append(quit_row)
    return rows


class HomeScreen(Screen):
    BINDINGS = [("q", "app.quit", "Quit")]

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(id="body"):
            yield Static(id="home-heading", classes="title")
            yield OptionList(id="menu")
        yield Footer()

    def on_mount(self) -> None:
        self.rebuild()

    def on_screen_resume(self) -> None:
        # Returning here after an operation: reflect any state change.
        self.app.refresh_detected()
        self.rebuild()

    def rebuild(self) -> None:
        detected = self.app.detected
        subtitle = (
            f"{detected.os_label} · Python {detected.python_version} · "
            f"{detected.state_label}"
        )
        if detected.registered_clients:
            subtitle += f"\nConnected clients: {', '.join(detected.registered_clients)}"
        self.query_one("#home-heading", Static).update(
            heading("What would you like to do?", subtitle)
        )
        menu = self.query_one("#menu", OptionList)
        menu.clear_options()
        for row_id, title, desc in _menu_for(detected):
            menu.add_option(Option(option_text(title, desc), id=row_id))
        menu.focus()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        choice = event.option_id
        self.app.wiz.reset_operation()
        if choice == "quit":
            self.app.exit()
            return
        self.app.wiz.operation = choice

        if choice == OP_INSTALL:
            from .clients import ClientsScreen

            self.app.push_screen(ClientsScreen(mode="install"))
        elif choice == OP_REINSTALL:
            from .confirm import ConfirmScreen

            self.app.push_screen(ConfirmScreen())
        elif choice == OP_CONFIG_OLLAMA:
            from .ollama import OllamaScreen

            self.app.push_screen(OllamaScreen(mode="config"))
        elif choice == OP_MANAGE_CLIENTS:
            from .clients import ClientsScreen

            self.app.push_screen(ClientsScreen(mode="manage"))
        elif choice == OP_UNINSTALL:
            from .uninstall import UninstallScreen

            self.app.push_screen(UninstallScreen())
