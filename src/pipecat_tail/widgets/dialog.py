#
# Copyright (c) 2025-2026, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

"""Dialogs over the dashboard: a few lines, the keys they take, and a button."""

from rich.console import Group
from rich.style import Style
from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Static

from pipecat_tail.widgets import palette


class Dialog(ModalScreen):
    """A panel over the dashboard: lines, then the keys it takes, then a button.

    Enter presses the button, which confirms; Esc closes it. A modal screen
    keeps the app's keys from reaching it, so a dialog binds the ones it
    offers itself.
    """

    DEFAULT_CSS = """
    Dialog { align: center middle; background: $background 60%; }
    Dialog #dialog {
        width: 47;
        max-width: 95%;
        height: auto;
        padding: 1 2;
        border: round $primary;
        border-title-style: bold;
        background: $surface;
    }
    Dialog #dialog-body { height: auto; }
    Dialog Button { margin-top: 1; width: 100%; }
    """

    BINDINGS = [Binding("escape", "dismiss", "close")]

    def __init__(
        self, title: str, lines: list[Text], keys: list[tuple[str, str]], button: str = "OK"
    ):
        """Create the dialog.

        Args:
            title: Its title, on the border.
            lines: What it says.
            keys: The keys it takes and what they do, listed under the lines.
            button: The button's label.
        """
        super().__init__()
        self.title_text = title
        self.button_label = button
        p = palette.current()
        body = list(lines)
        if keys:
            body.append(Text(""))
        for key, meaning in keys:
            line = Text(f"{key:<5}", style=Style(color=p.bright, bold=True))
            line.append(meaning, style=Style(color=p.muted))
            body.append(line)
        self.body = Group(*body)

    def compose(self) -> ComposeResult:
        """Lay out the lines and the button."""
        with Vertical(id="dialog") as panel:
            panel.border_title = self.title_text
            yield Static(self.body, id="dialog-body")
            yield Button(self.button_label, id="dialog-button", variant="primary")

    def on_mount(self) -> None:
        """Enter presses the button."""
        self.query_one(Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """The button confirms."""
        self.action_confirm()

    def action_confirm(self) -> None:
        """Close the dialog, confirming it."""
        self.dismiss(True)


class QuitDialog(Dialog):
    """Asks before quitting: the session on screen goes, and the bot may too.

    ``q`` again or Enter quits, ``s`` saves a report first, Esc stays.
    """

    BINDINGS = [
        Binding("q", "confirm", "quit"),
        Binding("s", "app.save_report", "save a report"),
    ]

    def __init__(self, *, stops_bot: bool):
        """Create the dialog.

        Args:
            stops_bot: Quitting stops the bot, live call and all: Tail runs
                in the bot's own terminal.
        """
        p = palette.current()
        if stops_bot:
            line = Text("The bot stops too, and the call with it.", style=Style(color=p.warn))
        else:
            line = Text("The session on screen goes away.", style=Style(color=p.muted))
        keys = [("q ⏎", "quit"), ("s", "save a report first"), ("esc", "stay")]
        super().__init__("Quit Tail?", [line], keys, button="Quit")
