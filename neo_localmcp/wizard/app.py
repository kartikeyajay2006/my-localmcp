"""The Textual application shell for the setup wizard.

Owns the shared :class:`WizardState`, the chosen backend, and the detected
machine state; everything else lives in the screens. Which backend is
constructed (fake vs real) is the only line that changes between the simulation
and the real installer.
"""

from __future__ import annotations

from textual.app import App

from .backend import WizardBackend, WizardState


class WizardApp(App):
    TITLE = "neo-localmcp setup wizard"

    CSS = """
    Screen {
        align: center top;
    }
    #body {
        width: 90%;
        max-width: 100;
        height: auto;
        padding: 1 2;
    }
    .title {
        padding: 1 0 0 0;
    }
    .panel {
        border: round $primary;
        padding: 1 2;
        margin: 1 0;
        height: auto;
    }
    #buttons {
        height: auto;
        padding: 1 0 0 0;
    }
    #buttons Button {
        margin: 0 2 0 0;
    }
    OptionList {
        height: auto;
        max-height: 20;
        border: round $panel;
    }
    SelectionList {
        height: auto;
        max-height: 16;
        border: round $panel;
    }
    RadioSet {
        height: auto;
        border: round $panel;
    }
    RichLog {
        height: 1fr;
        min-height: 12;
        border: round $panel;
        padding: 0 1;
    }
    Input {
        margin: 0 0 1 0;
    }
    """

    def __init__(self, backend: WizardBackend, fake: bool) -> None:
        super().__init__()
        self.backend = backend
        self.fake = fake
        self.wiz = WizardState()
        self.detected = backend.detect()
        self.prefs = backend.load_prefs()

    def on_mount(self) -> None:
        self.sub_title = "SIMULATION (--fake) — nothing on disk changes" if self.fake else ""
        from .screens.home import HomeScreen

        self.push_screen(HomeScreen())

    def refresh_detected(self) -> None:
        self.detected = self.backend.detect()

    def reset_to_home(self) -> None:
        """Pop every pushed screen back to the base Home screen and refresh it."""
        while len(self.screen_stack) > 1:
            self.pop_screen()
        self.refresh_detected()
        top = self.screen
        if hasattr(top, "rebuild"):
            top.rebuild()


def run(argv: list[str] | None = None) -> int:
    argv = list(argv or [])
    fake = "--fake" in argv
    if fake:
        from .fake_backend import FakeBackend

        backend: WizardBackend = FakeBackend()
    else:
        from .real_backend import RealBackend

        backend = RealBackend()

    WizardApp(backend=backend, fake=fake).run()
    return 0
