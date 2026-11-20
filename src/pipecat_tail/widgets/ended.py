#
# Copyright (c) 2025-2026, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

"""The dialog that says a call is over, and how it went."""

from rich.style import Style
from rich.text import Text
from textual.binding import Binding

from pipecat_tail.state import SessionState, WorkerSession
from pipecat_tail.widgets import palette
from pipecat_tail.widgets.dialog import Dialog
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


class CallEnded(Dialog):
    """Says the call is over, how it went, and what to do next.

    ``n`` and ``p`` close it and step back through the turns where something
    went wrong; Enter or Esc just close it, and ``q`` asks to quit instead.
    """

    BINDINGS = [
        Binding("n", "look_back(1)", "next issue"),
        Binding("p", "look_back(-1)", "previous issue"),
        Binding("s", "app.save_report", "save a report"),
        Binding("q", "app.request_quit", "quit"),
        Binding("c", "app.connect", "reconnect"),
    ]

    def __init__(self, state: SessionState, session: WorkerSession, *, bot_gone: bool):
        """Create the dialog for a session whose call is over.

        Args:
            state: The whole model.
            session: The session whose call ended.
            bot_gone: The bot went away before its pipeline finished.
        """
        keys = [("n p", "look back at what went wrong"), ("s", "save a report"), ("q", "quit")]
        if bot_gone:
            keys.append(("c", "reconnect now; Tail also retries"))
        title = "The bot went away" if bot_gone else "Call ended"
        super().__init__(title, call_summary(state, session), keys)

    def action_look_back(self, step: int) -> None:
        """Close the dialog and step to the next or previous issue."""
        self.dismiss()
        self.app.action_jump(step)  # type: ignore[attr-defined]
