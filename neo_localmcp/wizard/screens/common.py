"""Small shared helpers for wizard screens (dim-description option text, headers)."""

from __future__ import annotations

from rich.text import Text

# Rich style used for the explainer line under every option/field.
DIM = "grey62"

# Per-level styling for the live progress log.
LEVEL_STYLES = {
    "info": "",
    "action": "cyan",
    "warning": "yellow",
    "error": "bold red",
    "summary": "bold green",
}


def option_text(title: str, desc: str) -> Text:
    """A two-line renderable: a bright title with a dim-grey explainer beneath it.

    This is the core of the requested look — every selectable option carries its
    own meaning in dimmer text directly underneath.
    """
    text = Text()
    text.append(title, style="bold")
    if desc:
        text.append("\n")
        text.append(desc, style=DIM)
    return text


def heading(title: str, subtitle: str = "") -> Text:
    text = Text()
    text.append(title, style="bold")
    if subtitle:
        text.append("\n")
        text.append(subtitle, style=DIM)
    return text
