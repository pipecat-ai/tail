#
# Copyright (c) 2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

"""The waiting screen and the help screen."""

import math
import time

from rich.align import Align
from rich.console import Group
from rich.style import Style
from rich.table import Table
from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widget import Widget
from textual.widgets import Static

from pipecat_tail.state import SessionState
from pipecat_tail.widgets import palette
from pipecat_tail.widgets.graphics import SPARK, gradient, spinner
from pipecat_tail.widgets.render import fmt_secs

LOGO = (
    "████████╗ █████╗ ██╗██╗     ",
    "╚══██╔══╝██╔══██╗██║██║     ",
    "   ██║   ███████║██║██║     ",
    "   ██║   ██╔══██║██║██║     ",
    "   ██║   ██║  ██║██║███████╗",
    "   ╚═╝   ╚═╝  ╚═╝╚═╝╚══════╝",
)


def logo(phase: float = 0.0) -> list[Text]:
    """The logo with a gradient that drifts with ``phase``."""
    p = palette.current()
    stops = (p.accent, p.user, p.llm, p.bot, p.accent)
    lines = []
    for row in LOGO:
        text = Text()
        for i, ch in enumerate(row):
            t = (i / len(row) * 0.75 + phase) % 1.0
            text.append(ch, style=Style(color=gradient(stops, t), bold=True))
        lines.append(text)
    return lines


class SplashLogo(Widget):
    """The logo and the tagline. The gradient drifts slowly, so it redraws rarely."""

    DEFAULT_CSS = """
    SplashLogo { height: 8; width: 100%; }
    """

    def render(self):
        """Draw the logo."""
        p = palette.current()
        rows: list = [Align.center(line) for line in logo(int(time.time()) * 0.05)]
        rows.append(Text(""))
        tagline = Text()
        tagline.append("ᓚᘏᗢ", style=Style(color=p.accent, bold=True))
        tagline.append("  a terminal for voice agents", style=Style(color=p.dim))
        rows.append(Align.center(tagline))
        return Group(*rows)


class SplashPulse(Widget):
    """A moving wave and the connection status: the only part that animates."""

    DEFAULT_CSS = """
    SplashPulse { height: 2; width: 100%; margin: 2 0 0 0; }
    """

    def __init__(self, state: SessionState, url: str):
        """Create the pulse."""
        super().__init__()
        self.state = state
        self.url = url

    def render(self):
        """Draw the wave and the status."""
        p = palette.current()
        now = time.time()
        wave = Text()
        for i in range(40):
            v = (math.sin(i / 3.0 - now * 4) + 1) / 2 * (0.5 + 0.5 * math.sin(i / 11.0 + now))
            v = max(0.0, min(1.0, v))
            wave.append(SPARK[int(v * 7)], style=Style(color=gradient((p.grid, p.accent), v)))
        status = Text()
        if self.state.status == "connected":
            status.append(
                "connected, waiting for the pipeline to start ", style=Style(color=p.muted)
            )
        elif self.state.status == "error":
            status.append(self.state.status_detail or "connection error", style=Style(color=p.err))
            status.append("  retrying ", style=Style(color=p.muted))
        else:
            status.append("waiting for a bot on ", style=Style(color=p.muted))
            status.append(self.url or "ws://localhost:9292", style=Style(color=p.bright, bold=True))
            status.append(" ")
        status.append(spinner(now), style=Style(color=p.accent, bold=True))
        return Group(Align.center(wave), Align.center(status))


class SplashHowTo(Widget):
    """How to attach a bot. Static."""

    DEFAULT_CSS = """
    SplashHowTo { height: auto; width: 100%; margin: 2 0 0 0; }
    """

    def __init__(self, demo_hint: bool):
        """Create the instructions."""
        super().__init__()
        self.demo_hint = demo_hint

    def render(self):
        """Draw the instructions.

        Labels sit beside their commands when there is room, above them when
        not, and a command too long for the line breaks the way a shell
        would read it, so it still pastes.
        """
        p = palette.current()
        prompt = Style(color=p.muted)
        accent, bright, text = Style(color=p.accent), Style(color=p.bright), Style(color=p.text)
        ways: list[tuple[str, list[Text], list[Text]]] = [
            (
                "in your bot",
                [
                    Text.assemble(
                        ("PipelineWorker(pipeline, observers=[", text),
                        ("TailObserver()", Style(color=p.accent, bold=True)),
                        ("])", text),
                    )
                ],
                [
                    Text("PipelineWorker(pipeline,", style=text),
                    Text.assemble(
                        ("    observers=[", text),
                        ("TailObserver()", Style(color=p.accent, bold=True)),
                        ("])", text),
                    ),
                ],
            ),
            (
                "a bot on another machine",
                [
                    Text.assemble(
                        ("$ ", prompt),
                        ("ssh -L 9292:localhost:9292 ", accent),
                        ("bot-host", bright),
                        ("  then  ", prompt),
                        ("pipecat tail", bright),
                    )
                ],
                [
                    Text.assemble(
                        ("$ ", prompt),
                        ("ssh -L 9292:localhost:9292 ", accent),
                        ("bot-host", bright),
                    ),
                    Text.assemble(("$ ", prompt), ("pipecat tail", bright)),
                ],
            ),
        ]
        if self.demo_hint:
            demo = [
                Text.assemble(
                    ("$ ", prompt), ("pipecat tail --demo", Style(color=p.bright, bold=True))
                )
            ]
            ways.append(("just looking", demo, demo))
        width = self.size.width or 120
        label_w = max(len(label) for label, _, _ in ways)
        widest = max(line.cell_len for _, wide, _ in ways for line in wide)
        if label_w + 2 + widest <= width:
            howto = Table.grid(padding=(0, 2))
            howto.add_column(justify="right")
            howto.add_column()
            for label, wide, _ in ways:
                howto.add_row(Text(label, style=prompt), Text("\n").join(wide))
            return Align.center(howto)
        lines: list[Text] = []
        fits = all(line.cell_len + 2 <= width for _, wide, _ in ways for line in wide)
        for label, wide, narrow in ways:
            lines.append(Text(label, style=prompt))
            lines.extend(Text("  ") + line for line in (wide if fits else narrow))
            lines.append(Text(""))
        return Align.center(Group(*lines[:-1]))


class Splash(Vertical):
    """What Tail shows before a bot connects.

    Only the wave and the status move, a few times a second; the logo
    drifts once a second and the instructions never change. A screen left
    open all day costs next to nothing.
    """

    DEFAULT_CSS = """
    Splash { height: 1fr; align: center middle; }
    """

    def __init__(self, state: SessionState, url: str = "", *, demo_hint: bool = True):
        """Create the screen."""
        super().__init__(id="splash")
        self.logo = SplashLogo()
        self.pulse = SplashPulse(state, url)
        self.howto = SplashHowTo(demo_hint)
        self._frames = 0

    def compose(self) -> ComposeResult:
        """Lay out the logo, the pulse and the instructions."""
        yield self.logo
        yield self.pulse
        yield self.howto

    def next_frame(self) -> None:
        """Advance one frame: the pulse every time, the logo once a second."""
        self._frames += 1
        self.pulse.refresh()
        if self._frames % 5 == 0:
            self.logo.refresh()

    def redraw(self) -> None:
        """Redraw everything, for example after a theme change."""
        for widget in (self.logo, self.pulse, self.howto):
            widget.refresh()


HELP = (
    (
        "Views",
        (
            ("1", "Live: the voice timeline over the conversation, every turn"),
            ("2", "Latency: where the time goes, turn by turn"),
            ("3", "Pipeline: processors, frame flow, startup, workers, jobs and the bus"),
            ("4", "Metrics: service latency and usage"),
            ("5", "Logs"),
        ),
    ),
    (
        "Everywhere",
        (
            (
                "⏎",
                "inspect the selected turn (the latest if none); tab for its LLM context and events",
            ),
            ("↑ ↓", "select a turn in Live and Latency"),
            ("w", "next worker, when the runner has several bots"),
            ("e", "the latest error, in the logs"),
            ("s", "save a report of the session, JSON and Markdown, here"),
            ("x", "dismiss the error banner"),
            ("t", "next theme"),
            ("c", "reconnect now"),
            ("q", "quit"),
        ),
    ),
    (
        "Live",
        (
            ("n p", "next or previous turn where something went wrong, or search match"),
            ("/", "search what was said; esc ends the search"),
            ("f", "follow the latest turn, or pause to read back"),
            ("+ -", "show more or less time in the voice timeline"),
        ),
    ),
    (
        "Logs",
        (
            ("/", "search with a regular expression"),
            ("l", "minimum level"),
            ("n p", "select the next or previous error"),
            ("f", "follow, or pause"),
        ),
    ),
    ("Pipeline", (("b", "filter the bus by kind of message"),)),
)


class HelpScreen(ModalScreen):
    """Keys and what the colors mean."""

    DEFAULT_CSS = """
    HelpScreen { align: center middle; background: $background 70%; }
    HelpScreen #help {
        width: 84;
        max-width: 95%;
        height: auto;
        max-height: 90%;
        border: round $primary;
        border-title-style: bold;
        background: $surface;
        padding: 1 2;
    }
    """

    BINDINGS = [Binding("escape,q,question_mark", "dismiss", "close")]

    def compose(self) -> ComposeResult:
        """Lay out the help."""
        panel = VerticalScroll(Static(self._body()), id="help")
        panel.border_title = "ᓚᘏᗢ tail · help"
        yield panel

    def on_mount(self) -> None:
        """Focus the panel so the arrows and page keys scroll it right away."""
        self.query_one("#help").focus()

    def _body(self) -> Group:
        p = palette.current()
        rows: list = [Text("Reading the screen", style=Style(color=p.bright, bold=True))]
        marks = Table.grid(padding=(0, 2))
        marks.add_column(width=18, no_wrap=True)
        marks.add_column()

        def mark(sample: Text, meaning: str) -> None:
            marks.add_row(Text("  ") + sample, Text(meaning, style=Style(color=p.text)))

        def styled(*parts: tuple[str, str]) -> Text:
            return Text.assemble(*((t, Style(color=c, bold=True)) for t, c in parts))

        mark(
            styled(("▸ THINKING", p.llm)), "what the bot is doing: LISTENING, THINKING or SPEAKING"
        )
        mark(
            styled(("voice-to-voice", p.muted)),
            "from you going silent to the bot speaking; it counts up while the bot thinks",
        )
        mark(styled(("turn 5 so far", p.text)), "where the time of the current turn is going")
        mark(styled(("━", p.vad)), "your speech, as the VAD heard it")
        mark(
            styled(("┃", p.user), (" ┃", p.warn)),
            "the end of your turn; amber when Pipecat ended it before you had finished",
        )
        mark(styled(("╰─╯ 880ms", p.ok)), "the wait for the bot's answer, in its grade's color")
        mark(
            styled(("↯", p.err)),
            "an interruption; what the bot didn't get to say is struck through",
        )
        mark(
            styled(("─ 6 ─ 1.03 s ▲", p.warn)),
            "a turn: its number, then what went wrong and how long the answer took",
        )
        mark(
            Text.assemble(
                ("bright", Style(color=p.bright, bold=True)), (" dim", Style(color=p.dim))
            ),
            "what the bot said, and what it hasn't said yet",
        )
        mark(styled(("ƒ tool", p.tool)), "a tool call, with its arguments, result and time")
        mark(
            Text("(warm)", style=Style(color=p.bot, italic=True)),
            "how the bot was asked to say what follows",
        )
        rows.append(marks)
        rows.append(Text(""))
        for title, keys in HELP:
            rows.append(Text(title, style=Style(color=p.bright, bold=True)))
            grid = Table.grid(padding=(0, 2))
            grid.add_column(width=8, no_wrap=True)
            grid.add_column()
            for key, label in keys:
                cap = Text("  ")
                cap.append(f" {key} ".ljust(6), style=Style(color=p.ink, bgcolor=p.dim, bold=True))
                grid.add_row(cap, Text(label, style=Style(color=p.text)))
            rows.append(grid)
            rows.append(Text(""))
        rows.append(Text("Colors", style=Style(color=p.bright, bold=True)))
        legend = Text("  ")
        for label, color in (("you", p.user), ("bot", p.bot)):
            legend.append("■ ", style=Style(color=color))
            legend.append(label + "   ", style=Style(color=p.text))
        rows.append(legend)
        # The stages of a response, in the color of their part of the cycle.
        for label, colors, names in (
            ("listening", (p.vad,), "VAD, transcription, end of turn"),
            ("thinking", (p.llm, p.tool), "LLM, then tools"),
            ("speaking", (p.bot, p.tts), "first sentence, then speech synthesis"),
        ):
            line = Text("  ")
            for color in colors:
                line.append("■", style=Style(color=color))
            line.append(" " * (3 - len(colors)))
            line.append(f"{label:<11}", style=Style(color=p.text))
            line.append(names, style=Style(color=p.muted))
            rows.append(line)
        target = palette.latency_target()
        rows.append(
            Text(
                f"  Latency is green under the {fmt_secs(target)} target (--target), "
                f"amber under {fmt_secs(target * palette.SLOW_FACTOR)} and red above.",
                style=Style(color=p.muted),
            )
        )
        return Group(*rows)
