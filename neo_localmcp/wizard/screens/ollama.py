"""Ollama model selection, populated from the live `ollama list` result."""

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

from ..backend import OP_CONFIG_OLLAMA
from .common import heading


class OllamaScreen(Screen):
    BINDINGS = [("escape", "app.pop_screen", "Back")]

    def __init__(self, mode: str) -> None:
        super().__init__()
        self.mode = mode  # "install" (optional, skippable) | "config" (standalone)
        self.info = None

    def compose(self) -> ComposeResult:
        yield Header()
        self.info = self.app.backend.ollama_info()
        info = self.info
        with Vertical(id="body"):
            yield Static(
                heading(
                    "Choose Ollama models",
                    "Ollama is optional — it re-ranks and summarizes locally. "
                    "neo-localmcp always works without it.",
                ),
                classes="title",
            )
            yield Static(info.detail, classes="panel")

            yield Static("Ollama endpoint (base URL):")
            yield Input(value=info.base_url, id="base_url")

            has_models = info.reachable and bool(info.installed_models)
            self._has_models = has_models
            if has_models:
                yield Static("Fast model (ranking) — pick from installed:")
                with RadioSet(id="fast"):
                    for model in info.installed_models:
                        yield RadioButton(model, value=(model == info.fast_model))
                yield Static("Summary model (file summaries) — pick from installed:")
                with RadioSet(id="summary"):
                    for model in info.installed_models:
                        yield RadioButton(model, value=(model == info.summary_model))
            else:
                yield Static(
                    "No installed models detected — enter model names manually "
                    "(they'll be used if/when Ollama is available).",
                    classes="",
                )
                yield Static("Fast model (ranking):")
                yield Input(value=info.fast_model, id="fast_input")
                yield Static("Summary model (file summaries):")
                yield Input(value=info.summary_model, id="summary_input")

            with Horizontal(id="buttons"):
                yield Button("Back", id="back")
                if self.mode == "install":
                    yield Button("Skip — no Ollama", id="skip")
                    yield Button("Use these models", id="next", variant="primary")
                else:
                    yield Button("Save models", id="next", variant="primary")
        yield Footer()

    def _selected_from_radio(self, radio_id: str, fallback: str) -> str:
        radio = self.query_one(f"#{radio_id}", RadioSet)
        button = radio.pressed_button
        if button is not None:
            return str(button.label)
        return fallback

    def _read_models(self) -> tuple[str, str, str]:
        base_url = self.query_one("#base_url", Input).value.strip()
        if self._has_models:
            fast = self._selected_from_radio("fast", self.info.fast_model)
            summary = self._selected_from_radio("summary", self.info.summary_model)
        else:
            fast = self.query_one("#fast_input", Input).value.strip()
            summary = self.query_one("#summary_input", Input).value.strip()
        return base_url, fast, summary

    def on_button_pressed(self, event: Button.Pressed) -> None:
        wiz = self.app.wiz
        if event.button.id == "back":
            self.app.pop_screen()
            return

        if event.button.id == "skip":
            wiz.configure_ollama = False
            from .confirm import ConfirmScreen

            self.app.push_screen(ConfirmScreen())
            return

        # "next": capture the chosen endpoint + models.
        base_url, fast, summary = self._read_models()
        wiz.configure_ollama = True
        wiz.ollama_base_url = base_url
        wiz.fast_model = fast
        wiz.summary_model = summary

        if self.mode == "install":
            from .confirm import ConfirmScreen

            self.app.push_screen(ConfirmScreen())
        else:
            wiz.operation = OP_CONFIG_OLLAMA
            from .progress import ProgressScreen

            self.app.push_screen(ProgressScreen())
