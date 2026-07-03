"""Uninstall: choose runtime-only removal vs. a gated full data wipe."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import (
    Button,
    Footer,
    Header,
    Input,
    RadioButton,
    RadioSet,
    Static,
)

from ..backend import FULL_WIPE_PHRASE, OP_UNINSTALL
from .common import heading


class UninstallScreen(Screen):
    BINDINGS = [("escape", "app.pop_screen", "Back")]

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(id="body"):
            yield Static(
                heading(
                    "Uninstall neo-localmcp",
                    "Removing the runtime disconnects clients. Your indexed memory/data is "
                    "kept unless you explicitly choose a full wipe.",
                ),
                classes="title",
            )
            with RadioSet(id="mode"):
                yield RadioButton(
                    "Remove runtime only  —  keeps all memory/data (recommended)",
                    id="mode-runtime",
                    value=True,
                )
                yield RadioButton(
                    "Full wipe  —  delete the entire managed root and ALL stored data",
                    id="mode-wipe",
                )
            yield Static(
                f"A full wipe permanently deletes everything under "
                f"{self.app.detected.managed_root}. To authorize it, type the exact phrase "
                f"below.",
                classes="panel",
            )
            yield Static(f'Type "{FULL_WIPE_PHRASE}" to allow a full wipe:')
            yield Input(placeholder=FULL_WIPE_PHRASE, id="wipe_confirm")
            yield Static("", id="uninstall-error")
            with Horizontal(id="buttons"):
                yield Button("Back", id="back")
                yield Button("Continue", id="next", variant="warning")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#mode", RadioSet).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "back":
            self.app.pop_screen()
            return

        pressed = self.query_one("#mode", RadioSet).pressed_button
        full_wipe = pressed is not None and pressed.id == "mode-wipe"
        error = self.query_one("#uninstall-error", Static)

        if full_wipe:
            typed = self.query_one("#wipe_confirm", Input).value
            if typed != FULL_WIPE_PHRASE:
                error.update(
                    f'To confirm a full wipe you must type exactly: {FULL_WIPE_PHRASE}'
                )
                return

        wiz = self.app.wiz
        wiz.operation = OP_UNINSTALL
        wiz.full_wipe = full_wipe
        from .progress import ProgressScreen

        self.app.push_screen(ProgressScreen())
