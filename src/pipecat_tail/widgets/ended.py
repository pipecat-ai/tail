#
# Copyright (c) 2025-2026, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

"""The dialog that says a call is over, and how it went."""

from rich.console import Group
from rich.style import Style
from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Static

from pipecat_tail.state import SessionState, WorkerSession
from pipecat_tail.widgets import palette
from pipecat_tail.widgets.render import fmt_elapsed, fmt_secs


def _count(n: int, thing: str) -> str:
    return f"{n} {thing}{'' if n == 1 else 's'}"


def call_summary(state: SessionState, session: WorkerSession) -> list[Text]:
    """How the call went, a short line each, as the report counts it."""
    from pipecat_tail.report import session_summary

    p = palette.current()
    muted, bright = Style(color=p.muted), Style(color=p.bright, bold=True)
    summary = session_summary(state, session)
    latency = summary["latency"]
    lines = []
    length = [_count(summary["turn_count"], "turn")]
    if summary["duration_secs"]:
        length.insert(0, fmt_elapsed(summary["duration_secs"]))
    lines.append(Text(" · ".join(length), style=bright))
    if latency["turns"]:
        median, worst = latency["p50"], latency["max"]
        line = Text("median ", style=muted)
        line.append(fmt_secs(median), style=Style(color=palette.latency_color(median), bold=True))
        line.append(" · worst ", style=muted)
        line.append(fmt_secs(worst), style=Style(color=palette.latency_color(worst), bold=True))
        lines.append(line)
        on_target = round((latency["on_target"] or 0) * latency["turns"])
        lines.append(Text(f"{on_target} of {latency['turns']} answers on target", style=muted))
    issues = [
        _count(summary["early_ends"], "early end"),
        _count(summary["interruptions"], "interruption"),
        _count(len(summary["errors"]), "error"),
    ]
    lines.append(Text(" · ".join(issues), style=muted))
    return lines


class CallEnded(ModalScreen):
    """Says the call is over, how it went, and what to do next.

    ``n`` and ``p`` close it and step back through the turns where something
    went wrong; Enter or Esc just close it.
    """

    DEFAULT_CSS = """
    CallEnded { align: center middle; background: $background 60%; }
    CallEnded #call-ended {
        width: 47;
        max-width: 95%;
        height: auto;
        padding: 1 2;
        border: round $primary;
        border-title-style: bold;
        background: $surface;
    }
    CallEnded #call-ended-body { height: auto; }
    CallEnded Button { margin-top: 1; width: 100%; }
    """

    BINDINGS = [
        Binding("escape", "dismiss", "close"),
        Binding("n", "look_back(1)", "next issue"),
        Binding("p", "look_back(-1)", "previous issue"),
    ]

    def __init__(self, state: SessionState, session: WorkerSession, *, bot_gone: bool):
        """Create the dialog for a session whose call is over.

        Args:
            state: The whole model.
            session: The session whose call ended.
            bot_gone: The bot went away before its pipeline finished.
        """
        super().__init__()
        self.title_text = "The bot went away" if bot_gone else "Call ended"
        p = palette.current()
        lines = call_summary(state, session)
        lines.append(Text(""))
        hints = [("n p", "look back at what went wrong"), ("s", "save a report"), ("q", "quit")]
        if bot_gone:
            hints.append(("c", "reconnect now; Tail also retries"))
        for key, meaning in hints:
            line = Text(f"{key:<5}", style=Style(color=p.bright, bold=True))
            line.append(meaning, style=Style(color=p.muted))
            lines.append(line)
        self.body = Group(*lines)

    def compose(self) -> ComposeResult:
        """Lay out the summary and the button."""
        with Vertical(id="call-ended") as panel:
            panel.border_title = self.title_text
            yield Static(self.body, id="call-ended-body")
            yield Button("OK", id="call-ended-ok", variant="primary")

    def on_mount(self) -> None:
        """Enter presses OK."""
        self.query_one(Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Close the dialog."""
        self.dismiss()

    def action_look_back(self, step: int) -> None:
        """Close the dialog and step to the next or previous issue."""
        self.dismiss()
        self.app.action_jump(step)  # type: ignore[attr-defined]
